"""Guild dashboard CRUD for reminders, engagement stats, and custom commands.

Sprint 3b: control-panel stops querying Railway SQL directly and proxies here.
Auth: Dashboard service key + Discord snowflake via verify_dashboard_discord_admin.
"""
from __future__ import annotations

import re
import time as time_module
from datetime import UTC, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import config
from utils.logger import logger
from utils.premium_guard import get_user_tier, is_premium, premium_required_message
from utils.premium_tiers import REMINDER_LIMIT

BRUSSELS_TZ = ZoneInfo("Europe/Brussels")

LIVE_SESSION_NAME = "Live session"
LIVE_SESSION_MESSAGE = "Live session starting now!"
MAX_CUSTOM_COMMANDS_PER_GUILD = 50
VALID_TRIGGER_TYPES = frozenset({"exact", "starts_with", "contains", "regex"})
# Max image reminder creates/updates per user+guild within IMAGE_REMINDER_RATE_LIMIT_WINDOW
# (matches Discord cog hard-cap of 3; list retention uses IMAGE_REMINDER_RATE_LIMIT_COUNT).
IMAGE_REMINDER_RATE_LIMIT = 3

_image_reminder_timestamps: dict[tuple[int, int], list[float]] = {}


async def _require_reminder_quota(conn: Any, user_id: int, guild_id: int) -> None:
    """Mirror Discord /add_reminder free-tier REMINDER_LIMIT for (created_by, guild)."""
    tier = await get_user_tier(user_id, guild_id)
    limit = REMINDER_LIMIT.get(tier)
    if limit is None:
        return
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM reminders WHERE created_by = $1 AND guild_id = $2",
        user_id,
        guild_id,
    )
    if int(count or 0) >= limit:
        raise HTTPException(
            status_code=403,
            detail=(
                f"You have reached the maximum of {limit} reminders for your tier. "
                "Upgrade via /premium to create unlimited reminders."
            ),
        )


async def _require_premium_image(
    user_id: int,
    guild_id: int,
    image_url: str | None,
    *,
    feature_name: str = "Reminders with images",
) -> str | None:
    """Return stripped image URL or None; enforce premium + rate limit when set."""
    resolved = (image_url or "").strip() or None
    if not resolved:
        return None
    if not await is_premium(user_id, guild_id):
        raise HTTPException(status_code=403, detail=premium_required_message(feature_name))
    key = (user_id, guild_id)
    now_ts = time_module.time()
    timestamps = _image_reminder_timestamps.get(key, [])
    recent = [t for t in timestamps if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
    if len(recent) >= IMAGE_REMINDER_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="You can add at most 3 reminders with images per hour. Try again later.",
        )
    return resolved


def _record_image_reminder(user_id: int, guild_id: int) -> None:
    key = (user_id, guild_id)
    now_ts = time_module.time()
    ts_list = _image_reminder_timestamps.get(key, [])
    ts_list = [t for t in ts_list if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
    ts_list.append(now_ts)
    _image_reminder_timestamps[key] = ts_list[-config.IMAGE_REMINDER_RATE_LIMIT_COUNT :]

def _dt_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(BRUSSELS_TZ).isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) < 2:
        raise ValueError("time must be HH:MM")
    hours = int(parts[0])
    minutes = int(parts[1])
    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        raise ValueError("time must be HH:MM")
    return time(hours, minutes)


def _compute_reminder_times(session_time: str, offset_minutes: int) -> tuple[time, time]:
    """Map event/session HH:MM → (reminder T−offset, call/event T0)."""
    call = _parse_hhmm(session_time)
    call_total = call.hour * 60 + call.minute
    reminder_total = ((call_total - offset_minutes) % (24 * 60) + 24 * 60) % (24 * 60)
    return time(reminder_total // 60, reminder_total % 60), call


def _times_from_event_dt(event_dt: datetime, offset_minutes: int) -> tuple[time, time, datetime]:
    """One-off: event timestamptz → (reminder time, call time, normalized event_dt)."""
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=UTC)
    local = event_dt.astimezone(BRUSSELS_TZ)
    call_t = time(local.hour, local.minute)
    rem_t, _ = _compute_reminder_times(call_t.strftime("%H:%M"), offset_minutes)
    return rem_t, call_t, event_dt


def _shift_event_dt_to_call_clock(event_dt: datetime, call_t: time) -> datetime:
    """Keep one-off calendar date (Brussels); move clock to call_t."""
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=UTC)
    local = event_dt.astimezone(BRUSSELS_TZ)
    return local.replace(
        hour=call_t.hour,
        minute=call_t.minute,
        second=0,
        microsecond=0,
    )


async def _get_reminder_offset_minutes(conn: Any, guild_id: int) -> int:
    raw = await conn.fetchval(
        """
        SELECT value FROM bot_settings
        WHERE guild_id = $1 AND scope = 'embedwatcher' AND key = 'reminder_offset_minutes'
        LIMIT 1
        """,
        guild_id,
    )
    if raw is None:
        return 60
    try:
        if isinstance(raw, (int, float)):
            return int(raw)
        text = str(raw).strip().strip('"')
        return int(text)
    except (TypeError, ValueError):
        return 60


def _serialize_reminder(row: Any) -> dict[str, Any]:
    data = dict(row)
    event_time = data.get("event_time")
    scheduled = data.get("scheduled_time") or event_time
    return {
        "id": data.get("id"),
        "guild_id": str(data.get("guild_id")) if data.get("guild_id") is not None else None,
        "name": data.get("name"),
        "channel_id": str(data.get("channel_id")) if data.get("channel_id") is not None else None,
        "time": _dt_iso(data.get("time")),
        "call_time": _dt_iso(data.get("call_time")),
        "days": list(data.get("days") or []) if data.get("days") is not None else None,
        "message": data.get("message"),
        "created_by": str(data.get("created_by")) if data.get("created_by") is not None else None,
        "location": data.get("location"),
        "event_time": _dt_iso(event_time),
        "scheduled_time": _dt_iso(scheduled),
        "image_url": data.get("image_url"),
        "completed": bool(data.get("completed") or False),
        "last_sent_at": _dt_iso(data.get("last_sent_at")),
        "created_at": _dt_iso(data.get("created_at")),
        "updated_at": _dt_iso(data.get("updated_at")),
    }


def _serialize_command(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data.get("id"),
        "guild_id": str(data.get("guild_id")) if data.get("guild_id") is not None else None,
        "name": data.get("name"),
        "trigger_type": data.get("trigger_type"),
        "trigger_value": data.get("trigger_value"),
        "response": data.get("response"),
        "enabled": bool(data.get("enabled", True)),
        "case_sensitive": bool(data.get("case_sensitive", False)),
        "delete_trigger": bool(data.get("delete_trigger", False)),
        "reply_to_user": bool(data.get("reply_to_user", True)),
        "uses": int(data.get("uses") or 0),
        "created_by": str(data.get("created_by")) if data.get("created_by") is not None else None,
        "created_at": _dt_iso(data.get("created_at")),
        "updated_at": _dt_iso(data.get("updated_at")),
    }


def _invalidate_custom_commands_cache(guild_id: int) -> None:
    try:
        from gpt.helpers import bot_instance

        if bot_instance is None:
            return
        cog = bot_instance.get_cog("CustomCommandsCog")
        if cog is not None and hasattr(cog, "_invalidate_cache"):
            cog._invalidate_cache(guild_id)
    except Exception as exc:
        logger.debug("Custom commands cache invalidate skipped: %s", exc)


def _require_pool() -> Any:
    import api as api_module

    if api_module.db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return api_module.db_pool


class CreateReminderBody(BaseModel):
    message: str
    scheduled_time: str | None = None
    name: str | None = None
    channel_id: str | int | None = None
    days: list[str | int] | None = None
    time: str | None = None


class UpdateReminderBody(BaseModel):
    message: str | None = None
    scheduled_time: str | None = None
    completed: bool | None = None
    name: str | None = None
    channel_id: str | int | None = None
    days: list[str | int] | None = None
    time: str | None = None
    image_url: str | None = None
    call_time: str | None = None
    session_time: str | None = None


class LiveSessionBody(BaseModel):
    time: str
    channel_id: str | int
    days: list[int] | None = None
    image_url: str | None = None


class CreateCustomCommandBody(BaseModel):
    name: str
    trigger_type: Literal["exact", "starts_with", "contains", "regex"]
    trigger_value: str
    response: str
    case_sensitive: bool = False
    delete_trigger: bool = False
    reply_to_user: bool = True


class UpdateCustomCommandBody(BaseModel):
    trigger_type: Literal["exact", "starts_with", "contains", "regex"] | None = None
    trigger_value: str | None = None
    response: str | None = None
    case_sensitive: bool | None = None
    delete_trigger: bool | None = None
    reply_to_user: bool | None = None
    enabled: bool | None = None


def register_dashboard_guild_crud(router: APIRouter, verify_dashboard_discord_admin: Any) -> None:
    """Attach Sprint 3b guild CRUD routes to the shared /api router."""

    @router.get("/dashboard/{guild_id}/reminders")
    async def list_guild_reminders(
        guild_id: int,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM reminders
                    WHERE guild_id = $1
                    ORDER BY COALESCE(event_time, NOW()) ASC, time ASC NULLS LAST, id ASC
                    """,
                    guild_id,
                )
            return {"reminders": [_serialize_reminder(row) for row in rows]}
        except Exception as exc:
            logger.error("[dashboard] list reminders failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to fetch reminders") from exc

    @router.post("/dashboard/{guild_id}/reminders")
    async def create_guild_reminder(
        guild_id: int,
        body: CreateReminderBody,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        if not body.scheduled_time and not body.time:
            raise HTTPException(
                status_code=400,
                detail="scheduled_time (one-off) or time (recurring) is required",
            )
        pool = _require_pool()
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        if channel_id is None:
            raise HTTPException(status_code=400, detail="channel_id is required")

        name = (body.name or "Reminder").strip() or "Reminder"
        try:
            async with pool.acquire() as conn:
                await _require_reminder_quota(conn, discord_admin_id, guild_id)
                offset = await _get_reminder_offset_minutes(conn, guild_id)
                event_time: datetime | None = None
                reminder_time: time | None = None
                call_time: time | None = None
                days: list[str] | None = None

                if body.scheduled_time:
                    try:
                        parsed = datetime.fromisoformat(body.scheduled_time.replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail="Invalid scheduled_time") from exc
                    reminder_time, call_time, event_time = _times_from_event_dt(parsed, offset)
                    days = None
                else:
                    if not body.days:
                        raise HTTPException(
                            status_code=400,
                            detail="days is required for recurring reminders",
                        )
                    days = [str(d) for d in body.days]
                    if not days:
                        raise HTTPException(
                            status_code=400,
                            detail="At least one day is required for recurring reminders",
                        )
                    try:
                        # UI sends event (call) time; store T−offset + call_time like Discord.
                        reminder_time, call_time = _compute_reminder_times(body.time or "", offset)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc

                row = await conn.fetchrow(
                    """
                    INSERT INTO reminders
                        (guild_id, name, channel_id, time, call_time, days, message,
                         created_by, event_time, completed)
                    VALUES ($1, $2, $3, $4, $5, $6::text[], $7, $8, $9, FALSE)
                    RETURNING *
                    """,
                    guild_id,
                    name,
                    channel_id,
                    reminder_time,
                    call_time,
                    days,
                    body.message,
                    discord_admin_id,
                    event_time,
                )
            return {"success": True, "reminderId": row["id"], "reminder": _serialize_reminder(row)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[dashboard] create reminder failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to create reminder") from exc

    @router.put("/dashboard/{guild_id}/reminders/{reminder_id}")
    async def update_guild_reminder(
        guild_id: int,
        reminder_id: int,
        body: UpdateReminderBody,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        pool = _require_pool()
        fields: dict[str, Any] = {}
        image_to_record: str | None = None

        if body.message is not None:
            fields["message"] = body.message
        if body.name is not None:
            fields["name"] = body.name
        if body.channel_id is not None:
            fields["channel_id"] = int(body.channel_id)
        if body.completed is not None:
            fields["completed"] = body.completed
        if body.image_url is not None:
            image_to_record = await _require_premium_image(
                discord_admin_id,
                guild_id,
                body.image_url,
            )
            fields["image_url"] = image_to_record
        if body.days is not None:
            fields["days"] = [str(d) for d in body.days]

        try:
            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT event_time FROM reminders WHERE id = $1 AND guild_id = $2",
                    reminder_id,
                    guild_id,
                )
                if existing is None:
                    raise HTTPException(status_code=404, detail="Reminder not found")

                offset = await _get_reminder_offset_minutes(conn, guild_id)
                was_one_off = existing["event_time"] is not None
                effective_event_time = existing["event_time"]

                if body.scheduled_time is not None:
                    if body.scheduled_time == "":
                        fields["event_time"] = None
                        effective_event_time = None
                        # Only clear leftover weekdays when dismantling a one-off.
                        # Do not wipe days on an already-recurring row.
                        if body.days is None and was_one_off:
                            fields["days"] = []
                    else:
                        try:
                            parsed = datetime.fromisoformat(
                                body.scheduled_time.replace("Z", "+00:00")
                            )
                        except ValueError as exc:
                            raise HTTPException(
                                status_code=400, detail="Invalid scheduled_time"
                            ) from exc
                        rem_t, call_t, event_dt = _times_from_event_dt(parsed, offset)
                        fields["event_time"] = event_dt
                        fields["time"] = rem_t
                        fields["call_time"] = call_t
                        effective_event_time = event_dt
                        # Converting to / refreshing a one-off must not keep weekday days.
                        if body.days is None:
                            fields["days"] = []

                # completed is for one-offs: allow if row was or becomes a one-off
                # (including clear-schedule + mark-done in one payload).
                if body.completed is True and not was_one_off and effective_event_time is None:
                    raise HTTPException(
                        status_code=400,
                        detail="completed is only supported for one-off reminders",
                    )

                if body.session_time:
                    try:
                        rem_t, call_t = _compute_reminder_times(body.session_time, offset)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    fields["time"] = rem_t
                    fields["call_time"] = call_t
                    if effective_event_time is not None:
                        synced = _shift_event_dt_to_call_clock(effective_event_time, call_t)
                        fields["event_time"] = synced
                        effective_event_time = synced
                elif body.call_time is not None or body.time is not None:
                    # Prefer explicit call_time; otherwise treat `time` as event/call clock
                    # (same contract as create + ReminderModal).
                    event_clock_raw = body.call_time if body.call_time is not None else body.time
                    assert event_clock_raw is not None
                    try:
                        rem_t, call_t = _compute_reminder_times(event_clock_raw, offset)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    fields["time"] = rem_t
                    fields["call_time"] = call_t
                    if effective_event_time is not None:
                        synced = _shift_event_dt_to_call_clock(effective_event_time, call_t)
                        fields["event_time"] = synced
                        effective_event_time = synced

                if not fields:
                    raise HTTPException(status_code=400, detail="No fields to update")

                assignments = []
                values: list[Any] = []
                for idx, (key, value) in enumerate(fields.items(), start=1):
                    if key == "days":
                        assignments.append(f"{key} = ${idx}::text[]")
                    else:
                        assignments.append(f"{key} = ${idx}")
                    values.append(value)
                values.extend([reminder_id, guild_id])
                sql = (
                    f"UPDATE reminders SET {', '.join(assignments)} "
                    f"WHERE id = ${len(values) - 1} AND guild_id = ${len(values)} RETURNING *"
                )
                row = await conn.fetchrow(sql, *values)
            if row is None:
                raise HTTPException(status_code=404, detail="Reminder not found")
            if image_to_record:
                _record_image_reminder(discord_admin_id, guild_id)
            return {"success": True, "reminder": _serialize_reminder(row)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "[dashboard] update reminder failed guild=%s id=%s: %s",
                guild_id,
                reminder_id,
                exc,
            )
            raise HTTPException(status_code=500, detail="Failed to update reminder") from exc

    @router.delete("/dashboard/{guild_id}/reminders/{reminder_id}")
    async def delete_guild_reminder(
        guild_id: int,
        reminder_id: int,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "DELETE FROM reminders WHERE id = $1 AND guild_id = $2 RETURNING id",
                    reminder_id,
                    guild_id,
                )
            if row is None:
                raise HTTPException(status_code=404, detail="Reminder not found")
            return {"success": True, "deleted": True}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "[dashboard] delete reminder failed guild=%s id=%s: %s",
                guild_id,
                reminder_id,
                exc,
            )
            raise HTTPException(status_code=500, detail="Failed to delete reminder") from exc

    @router.post("/dashboard/{guild_id}/reminders/live-sessions")
    async def create_live_session(
        guild_id: int,
        body: LiveSessionBody,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        pool = _require_pool()
        day_numbers = body.days if body.days else [0, 1, 2, 3, 4, 5, 6]
        day_numbers = [d for d in day_numbers if 0 <= int(d) <= 6]
        if not day_numbers:
            raise HTTPException(status_code=400, detail="At least one day is required")
        try:
            resolved_image = await _require_premium_image(
                discord_admin_id,
                guild_id,
                body.image_url,
                feature_name="Live session presets with images",
            )
            async with pool.acquire() as conn:
                await _require_reminder_quota(conn, discord_admin_id, guild_id)
                offset = await _get_reminder_offset_minutes(conn, guild_id)
                reminder_time, call_time = _compute_reminder_times(body.time, offset)
                row = await conn.fetchrow(
                    """
                    INSERT INTO reminders
                        (guild_id, name, channel_id, time, call_time, days, message, created_by, image_url, completed)
                    VALUES ($1, $2, $3, $4, $5, $6::text[], $7, $8, $9, FALSE)
                    RETURNING id
                    """,
                    guild_id,
                    LIVE_SESSION_NAME,
                    int(body.channel_id),
                    reminder_time,
                    call_time,
                    [str(d) for d in day_numbers],
                    LIVE_SESSION_MESSAGE,
                    discord_admin_id,
                    resolved_image,
                )
            if resolved_image:
                _record_image_reminder(discord_admin_id, guild_id)
            return {"success": True, "reminderId": row["id"]}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("[dashboard] live session create failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to create live session") from exc

    @router.get("/dashboard/{guild_id}/engagement")
    async def get_engagement_stats(
        guild_id: int,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        pool = _require_pool()

        try:
            async with pool.acquire() as conn:
                active = await conn.fetch(
                    """
                    SELECT
                      c.id, c.title, c.mode, c.channel_id, c.started_at, c.ends_at,
                      COUNT(p.id)::text AS participant_count
                    FROM engagement_challenges c
                    LEFT JOIN engagement_participants p ON p.challenge_id = c.id
                    WHERE c.guild_id = $1 AND c.active = TRUE
                    GROUP BY c.id
                    ORDER BY c.started_at DESC
                    """,
                    guild_id,
                )
                history = await conn.fetch(
                    """
                    SELECT
                      c.id, c.title, c.mode, c.winner_id, c.ended_at,
                      COUNT(p.id)::text AS participant_count
                    FROM engagement_challenges c
                    LEFT JOIN engagement_participants p ON p.challenge_id = c.id
                    WHERE c.guild_id = $1 AND c.active = FALSE
                    GROUP BY c.id
                    ORDER BY c.ended_at DESC NULLS LAST
                    LIMIT 10
                    """,
                    guild_id,
                )
                og_claims = await conn.fetch(
                    "SELECT COUNT(*)::text AS count FROM engagement_og_claims WHERE guild_id = $1",
                    guild_id,
                )
                og_setup = await conn.fetch(
                    "SELECT message_id, channel_id FROM engagement_og_setup WHERE guild_id = $1",
                    guild_id,
                )
                badges = await conn.fetch(
                    "SELECT COUNT(*)::text AS count FROM engagement_badges WHERE guild_id = $1",
                    guild_id,
                )
                streaks = await conn.fetch(
                    """
                    SELECT COUNT(*)::text AS count, MAX(current_days)::text AS max_streak
                    FROM engagement_streaks WHERE guild_id = $1
                    """,
                    guild_id,
                )
                # Fetch latest week first, then all of its results (no join LIMIT truncation).
                latest_week = await conn.fetchrow(
                    """
                    SELECT id, week_start, week_end
                    FROM engagement_weekly_awards
                    WHERE guild_id = $1
                    ORDER BY week_start DESC
                    LIMIT 1
                    """,
                    guild_id,
                )
                weekly_results = (
                    await conn.fetch(
                        """
                        SELECT award_key, user_id, metric
                        FROM engagement_weekly_results
                        WHERE week_id = $1
                        ORDER BY award_key ASC
                        """,
                        latest_week["id"],
                    )
                    if latest_week
                    else []
                )

            def row_dict(row: Any) -> dict[str, Any]:
                out = dict(row)
                for key, value in list(out.items()):
                    if isinstance(value, datetime):
                        out[key] = _dt_iso(value)
                    elif value is not None and key.endswith("_id"):
                        out[key] = str(value)
                return out

            latest_weekly = None
            if latest_week:
                week = row_dict(latest_week)
                latest_weekly = {
                    "id": week["id"],
                    "week_start": week.get("week_start"),
                    "week_end": week.get("week_end"),
                    "results": [
                        {
                            "award_key": r.get("award_key"),
                            "user_id": str(r["user_id"]) if r.get("user_id") is not None else None,
                            "metric": r.get("metric"),
                        }
                        for r in weekly_results
                        if r.get("award_key")
                    ],
                }

            streak_row = streaks[0] if streaks else None
            return {
                "active_challenges": [row_dict(r) for r in active],
                "challenge_history": [row_dict(r) for r in history],
                "og_claims_count": int((og_claims[0]["count"] if og_claims else 0) or 0),
                "og_setup": row_dict(og_setup[0]) if og_setup else None,
                "badges_count": int((badges[0]["count"] if badges else 0) or 0),
                "streaks_count": int((streak_row["count"] if streak_row else 0) or 0),
                "max_streak": int((streak_row["max_streak"] if streak_row and streak_row["max_streak"] else 0) or 0),
                "latest_weekly": latest_weekly,
            }
        except Exception as exc:
            logger.error("[dashboard] engagement stats failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to fetch engagement stats") from exc

    @router.get("/dashboard/{guild_id}/custom-commands")
    async def list_custom_commands(
        guild_id: int,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM custom_commands WHERE guild_id = $1 ORDER BY name ASC",
                    guild_id,
                )
            return {"commands": [_serialize_command(row) for row in rows]}
        except Exception as exc:
            logger.error("[dashboard] list custom commands failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to fetch custom commands") from exc

    @router.post("/dashboard/{guild_id}/custom-commands", status_code=201)
    async def create_custom_command(
        guild_id: int,
        body: CreateCustomCommandBody,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        name = body.name.strip().lower()
        trigger_value = body.trigger_value.strip()
        response = body.response.strip()
        if not name or not trigger_value or not response:
            raise HTTPException(
                status_code=400,
                detail="name, trigger_type, trigger_value, and response are required",
            )
        if body.trigger_type not in VALID_TRIGGER_TYPES:
            raise HTTPException(status_code=400, detail="Invalid trigger_type")
        if len(name) > 50 or len(trigger_value) > 200 or len(response) > 1900:
            raise HTTPException(
                status_code=400,
                detail="name max 50 chars, trigger_value max 200 chars, response max 1900 chars",
            )
        if body.trigger_type == "regex":
            try:
                re.compile(trigger_value)
            except re.error as exc:
                raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM custom_commands WHERE guild_id = $1",
                    guild_id,
                )
                if int(count or 0) >= MAX_CUSTOM_COMMANDS_PER_GUILD:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Maximum of {MAX_CUSTOM_COMMANDS_PER_GUILD} custom commands per guild",
                    )
                existing = await conn.fetchval(
                    "SELECT id FROM custom_commands WHERE guild_id = $1 AND name = $2",
                    guild_id,
                    name,
                )
                if existing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"A command named '{name}' already exists",
                    )
                await conn.execute(
                    """
                    INSERT INTO custom_commands
                        (guild_id, name, trigger_type, trigger_value, response,
                         case_sensitive, delete_trigger, reply_to_user, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    guild_id,
                    name,
                    body.trigger_type,
                    trigger_value,
                    response,
                    body.case_sensitive,
                    body.delete_trigger,
                    body.reply_to_user,
                    discord_admin_id,
                )
                row = await conn.fetchrow(
                    "SELECT * FROM custom_commands WHERE guild_id = $1 AND name = $2",
                    guild_id,
                    name,
                )
            _invalidate_custom_commands_cache(guild_id)
            return {"command": _serialize_command(row)}
        except HTTPException:
            raise
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"A command named '{name}' already exists",
            ) from exc
        except Exception as exc:
            logger.error("[dashboard] create custom command failed guild=%s: %s", guild_id, exc)
            raise HTTPException(status_code=500, detail="Failed to create custom command") from exc

    @router.put("/dashboard/{guild_id}/custom-commands/{command_name}")
    async def update_custom_command(
        guild_id: int,
        command_name: str,
        body: UpdateCustomCommandBody,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        command_name = command_name.strip().lower()
        if body.trigger_type is not None and body.trigger_type not in VALID_TRIGGER_TYPES:
            raise HTTPException(status_code=400, detail="Invalid trigger_type")
        if body.trigger_value is not None and len(body.trigger_value) > 200:
            raise HTTPException(status_code=400, detail="trigger_value max 200 chars")
        if body.response is not None and len(body.response) > 1900:
            raise HTTPException(status_code=400, detail="response max 1900 chars")

        fields: dict[str, Any] = {}
        if body.trigger_type is not None:
            fields["trigger_type"] = body.trigger_type
        if body.trigger_value is not None:
            fields["trigger_value"] = body.trigger_value.strip()
        if body.response is not None:
            fields["response"] = body.response.strip()
        if body.case_sensitive is not None:
            fields["case_sensitive"] = body.case_sensitive
        if body.delete_trigger is not None:
            fields["delete_trigger"] = body.delete_trigger
        if body.reply_to_user is not None:
            fields["reply_to_user"] = body.reply_to_user
        if body.enabled is not None:
            fields["enabled"] = body.enabled
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT trigger_type, trigger_value FROM custom_commands "
                    "WHERE guild_id = $1 AND name = $2",
                    guild_id,
                    command_name,
                )
                if existing is None:
                    raise HTTPException(status_code=404, detail="Command not found")

                effective_type = (
                    body.trigger_type
                    if body.trigger_type is not None
                    else existing["trigger_type"]
                )
                effective_value = (
                    body.trigger_value.strip()
                    if body.trigger_value is not None
                    else existing["trigger_value"]
                )
                if effective_type == "regex":
                    try:
                        re.compile(effective_value)
                    except re.error as exc:
                        raise HTTPException(
                            status_code=400, detail=f"Invalid regex: {exc}"
                        ) from exc

                assignments = ["updated_at = NOW()"]
                values: list[Any] = []
                for idx, (key, value) in enumerate(fields.items(), start=1):
                    assignments.append(f"{key} = ${idx}")
                    values.append(value)
                values.extend([guild_id, command_name])
                sql = (
                    f"UPDATE custom_commands SET {', '.join(assignments)} "
                    f"WHERE guild_id = ${len(values) - 1} AND name = ${len(values)} RETURNING *"
                )
                row = await conn.fetchrow(sql, *values)
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found")
            _invalidate_custom_commands_cache(guild_id)
            return {"command": _serialize_command(row)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "[dashboard] update custom command failed guild=%s name=%s: %s",
                guild_id,
                command_name,
                exc,
            )
            raise HTTPException(status_code=500, detail="Failed to update custom command") from exc

    @router.delete("/dashboard/{guild_id}/custom-commands/{command_name}")
    async def delete_custom_command(
        guild_id: int,
        command_name: str,
        discord_admin_id: int = Depends(verify_dashboard_discord_admin),
    ):
        del discord_admin_id
        command_name = command_name.strip().lower()
        pool = _require_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "DELETE FROM custom_commands WHERE guild_id = $1 AND name = $2 RETURNING name",
                    guild_id,
                    command_name,
                )
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found")
            _invalidate_custom_commands_cache(guild_id)
            return {"success": True}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "[dashboard] delete custom command failed guild=%s name=%s: %s",
                guild_id,
                command_name,
                exc,
            )
            raise HTTPException(status_code=500, detail="Failed to delete custom command") from exc
