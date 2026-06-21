"""
Engagement Service

Business logic for the Engagement module:
  - Challenges  (message-count leaderboard or random-draw contests)
  - Weekly Awards (motivator, foodfluencer, sharpshooter, star — configurable per guild)
  - Streaks     (daily activity streak with optional nickname suffix)
  - Badges      (per-guild role-linked badge history)
  - OG Claims   (reaction-based limited claim with cap + deadline)

All operations are multi-guild: every function accepts guild_id.
DB access uses the asyncpg pool surfaced via utils.db_helpers.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import discord

from utils.embed_builder import EmbedBuilder
from utils.logger import logger
from utils.sanitizer import safe_embed_text

# ---------------------------------------------------------------------------
# Streak helpers (nickname logic, no DB)
# ---------------------------------------------------------------------------

_STREAK_SUFFIX_RE = re.compile(r"\s*\|\s*(🐣 day \d+|🔥 week \d+|👑 month \d+)$")


def _strip_streak_suffix(name: str) -> str:
    match = _STREAK_SUFFIX_RE.search(name)
    if match:
        base = name[: match.start()].rstrip()
        return base if base else name
    return name.strip()


def _compute_suffix(days: int) -> str | None:
    if days <= 0:
        return None
    if days < 7:
        return f"🐣 day {days}"
    if days < 30:
        weeks = max(1, days // 7)
        return f"🔥 week {weeks}"
    months = max(1, days // 30)
    return f"👑 month {months}"


def _build_nickname(base: str, suffix: str | None) -> tuple[str, str]:
    base = (base or "").strip() or "Member"
    if suffix:
        desired = f"{base} | {suffix}"
        if len(desired) <= 32:
            return desired, base
        available = max(1, 32 - len(" | " + suffix))
        trimmed = base[:available].rstrip() or base[:available]
        return f"{trimmed} | {suffix}", trimmed
    return base[:32], base[:32]


# ---------------------------------------------------------------------------
# Streaks
# ---------------------------------------------------------------------------


async def get_streak(
    pool: asyncpg.Pool, guild_id: int, user_id: int
) -> tuple[date | None, int, str | None] | None:
    """Returns (last_day, current_days, base_nickname) or None if no record."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_day, current_days, base_nickname "
                "FROM engagement_streaks WHERE guild_id=$1 AND user_id=$2",
                guild_id,
                user_id,
            )
            if not row:
                return None
            return row["last_day"], int(row["current_days"]), row["base_nickname"]
    except Exception as exc:
        logger.warning(f"[engagement] get_streak error: {exc}")
        return None


async def set_streak(
    pool: asyncpg.Pool,
    guild_id: int,
    user_id: int,
    last_day: date,
    current_days: int,
    base_nickname: str | None = None,
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO engagement_streaks(guild_id, user_id, last_day, current_days, base_nickname)
                VALUES($1, $2, $3, $4, $5)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    last_day       = EXCLUDED.last_day,
                    current_days   = EXCLUDED.current_days,
                    base_nickname  = CASE
                        WHEN EXCLUDED.base_nickname IS NOT NULL THEN EXCLUDED.base_nickname
                        ELSE engagement_streaks.base_nickname
                    END
                """,
                guild_id,
                user_id,
                last_day,
                current_days,
                base_nickname,
            )
    except Exception as exc:
        logger.warning(f"[engagement] set_streak error: {exc}")


async def ensure_streak_nickname(
    member: discord.Member,
    stored_base: str | None,
    streak_days: int,
) -> tuple[str, bool, str]:
    """
    Update the member's nickname suffix based on their streak.
    Returns (base_for_db, success, message).
    """
    display_name = member.display_name or member.name
    base_candidate = _strip_streak_suffix(display_name)
    base_for_db = base_candidate or stored_base or member.name
    suffix = _compute_suffix(streak_days)
    new_nick, normalized_base = _build_nickname(base_for_db, suffix)
    base_for_db = normalized_base or base_for_db

    guild = member.guild
    if not guild:
        return base_for_db, False, "No guild"
    bot_member = guild.me
    if not bot_member or not bot_member.guild_permissions.manage_nicknames:
        return base_for_db, False, "Missing Manage Nicknames permission"

    target_nick = new_nick if (suffix or new_nick != member.name) else None
    if member.nick == target_nick:
        return base_for_db, True, "Unchanged"
    try:
        await member.edit(nick=target_nick, reason="Engagement streak update")
        return base_for_db, True, "Nickname updated"
    except Exception as exc:
        logger.warning(f"[engagement] nickname edit failed for {member.id}: {exc}")
        return base_for_db, False, f"Edit failed ({exc})"


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


async def add_badge(pool: asyncpg.Pool, guild_id: int, user_id: int, badge_key: str) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO engagement_badges(guild_id, user_id, badge_key) VALUES($1, $2, $3)",
                guild_id,
                user_id,
                badge_key,
            )
    except Exception as exc:
        logger.warning(f"[engagement] add_badge error: {exc}")


async def get_user_badges(pool: asyncpg.Pool, guild_id: int, user_id: int) -> list[str]:
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT badge_key FROM engagement_badges "
                "WHERE guild_id=$1 AND user_id=$2 ORDER BY assigned_at DESC",
                guild_id,
                user_id,
            )
            return [str(r["badge_key"]) for r in rows]
    except Exception as exc:
        logger.warning(f"[engagement] get_user_badges error: {exc}")
        return []


# ---------------------------------------------------------------------------
# OG Claims
# ---------------------------------------------------------------------------


async def og_count_claims(pool: asyncpg.Pool, guild_id: int) -> int:
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM engagement_og_claims WHERE guild_id=$1", guild_id
            )
            return int(val or 0)
    except Exception as exc:
        logger.warning(f"[engagement] og_count_claims error: {exc}")
        return 0


async def og_has_claim(pool: asyncpg.Pool, guild_id: int, user_id: int) -> bool:
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT 1 FROM engagement_og_claims WHERE guild_id=$1 AND user_id=$2",
                guild_id,
                user_id,
            )
            return bool(val)
    except Exception as exc:
        logger.warning(f"[engagement] og_has_claim error: {exc}")
        return False


async def og_insert_claim(pool: asyncpg.Pool, guild_id: int, user_id: int) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO engagement_og_claims(guild_id, user_id) VALUES($1, $2) "
                "ON CONFLICT (guild_id, user_id) DO NOTHING",
                guild_id,
                user_id,
            )
    except Exception as exc:
        logger.warning(f"[engagement] og_insert_claim error: {exc}")


async def og_get_setup(
    pool: asyncpg.Pool, guild_id: int
) -> tuple[int, int | None] | None:
    """Returns (message_id, channel_id) or None."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT message_id, channel_id FROM engagement_og_setup WHERE guild_id=$1",
                guild_id,
            )
            if not row:
                return None
            return int(row["message_id"]), (int(row["channel_id"]) if row["channel_id"] else None)
    except Exception as exc:
        logger.warning(f"[engagement] og_get_setup error: {exc}")
        return None


async def og_set_setup(
    pool: asyncpg.Pool, guild_id: int, message_id: int, channel_id: int | None
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO engagement_og_setup(guild_id, message_id, channel_id)
                VALUES($1, $2, $3)
                ON CONFLICT (guild_id) DO UPDATE SET
                    message_id = EXCLUDED.message_id,
                    channel_id = EXCLUDED.channel_id,
                    updated_at = NOW()
                """,
                guild_id,
                message_id,
                channel_id,
            )
    except Exception as exc:
        logger.warning(f"[engagement] og_set_setup error: {exc}")


# ---------------------------------------------------------------------------
# Challenges — runtime state (in-memory per process)
# ---------------------------------------------------------------------------

# { challenge_id: { mode, title, start_ts, end_ts, channel_id, guild_id, task, message_counts } }
_active_challenges: dict[int, dict[str, Any]] = {}
_challenge_lock = asyncio.Lock()


def get_active_challenges() -> dict[int, dict[str, Any]]:
    return _active_challenges


def get_guild_challenges(guild_id: int) -> dict[int, dict[str, Any]]:
    return {cid: rt for cid, rt in _active_challenges.items() if rt.get("guild_id") == guild_id}


async def challenge_create(
    pool: asyncpg.Pool,
    guild_id: int,
    channel_id: int,
    mode: str,
    duration_seconds: int,
    title: str | None,
) -> int | None:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO engagement_challenges
                    (guild_id, channel_id, mode, title, active, started_at, ends_at)
                VALUES($1, $2, $3, $4, TRUE, NOW(), NOW() + ($5::int) * INTERVAL '1 second')
                RETURNING id
                """,
                guild_id,
                channel_id,
                mode,
                title,
                duration_seconds,
            )
            return int(row["id"]) if row else None
    except Exception as exc:
        logger.error(f"[engagement] challenge_create error: {exc}")
        return None


async def challenge_add_participant(
    pool: asyncpg.Pool | None,
    challenge_id: int,
    user_id: int,
    increment: bool,
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            if increment:
                await conn.execute(
                    """
                    INSERT INTO engagement_participants(challenge_id, user_id, message_count)
                    VALUES($1, $2, 1)
                    ON CONFLICT (challenge_id, user_id)
                    DO UPDATE SET message_count = engagement_participants.message_count + 1
                    """,
                    challenge_id,
                    user_id,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO engagement_participants(challenge_id, user_id)
                    VALUES($1, $2)
                    ON CONFLICT (challenge_id, user_id) DO NOTHING
                    """,
                    challenge_id,
                    user_id,
                )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_add_participant error: {exc}")


async def challenge_get_top(
    pool: asyncpg.Pool | None, challenge_id: int, limit: int = 5
) -> list[tuple[int, int]]:
    if pool is None:
        rt = _active_challenges.get(challenge_id, {})
        counts: dict[int, int] = rt.get("message_counts", {})
        return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, message_count FROM engagement_participants
                WHERE challenge_id=$1
                ORDER BY message_count DESC, user_id ASC
                LIMIT $2
                """,
                challenge_id,
                limit,
            )
            return [(int(r["user_id"]), int(r["message_count"])) for r in rows]
    except Exception as exc:
        logger.warning(f"[engagement] challenge_get_top error: {exc}")
        return []


async def challenge_get_participants_count(
    pool: asyncpg.Pool | None, challenge_id: int
) -> int:
    if pool is None:
        rt = _active_challenges.get(challenge_id, {})
        return len(rt.get("message_counts", {}))
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM engagement_participants WHERE challenge_id=$1",
                challenge_id,
            )
            return int(val or 0)
    except Exception as exc:
        logger.warning(f"[engagement] challenge_get_participants_count error: {exc}")
        return 0


async def challenge_update_participant_count(
    pool: asyncpg.Pool | None, challenge_id: int, user_id: int, new_count: int
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO engagement_participants(challenge_id, user_id, message_count)
                VALUES($1, $2, $3)
                ON CONFLICT (challenge_id, user_id)
                DO UPDATE SET message_count = EXCLUDED.message_count
                """,
                challenge_id,
                user_id,
                new_count,
            )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_update_participant_count error: {exc}")


async def challenge_remove_participant(
    pool: asyncpg.Pool | None, challenge_id: int, user_id: int
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM engagement_participants WHERE challenge_id=$1 AND user_id=$2",
                challenge_id,
                user_id,
            )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_remove_participant error: {exc}")


async def challenge_end(
    pool: asyncpg.Pool | None, challenge_id: int, mode: str
) -> tuple[int, int] | None:
    """Mark challenge inactive, pick winner. Returns (winner_id, count) or None."""
    if pool is None:
        rt = _active_challenges.get(challenge_id, {})
        counts: dict[int, int] = rt.get("message_counts", {})
        if not counts:
            return None
        winner_id = max(counts, key=counts.__getitem__)
        return winner_id, counts[winner_id]
    try:
        async with pool.acquire() as conn:
            if mode == "leaderboard":
                row = await conn.fetchrow(
                    """
                    SELECT user_id, message_count FROM engagement_participants
                    WHERE challenge_id=$1
                    ORDER BY message_count DESC, user_id ASC
                    LIMIT 1
                    """,
                    challenge_id,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT user_id, message_count FROM engagement_participants
                    WHERE challenge_id=$1
                    ORDER BY random() LIMIT 1
                    """,
                    challenge_id,
                )
            if not row:
                await conn.execute(
                    "UPDATE engagement_challenges SET active=FALSE, ended_at=NOW() WHERE id=$1",
                    challenge_id,
                )
                return None
            winner_id = int(row["user_id"])
            winner_count = int(row["message_count"])
            await conn.execute(
                """
                UPDATE engagement_challenges
                SET active=FALSE, ended_at=NOW(), winner_id=$2, messages_count=$3
                WHERE id=$1
                """,
                challenge_id,
                winner_id,
                winner_count,
            )
            return winner_id, winner_count
    except Exception as exc:
        logger.error(f"[engagement] challenge_end error: {exc}")
        return None


async def challenge_cancel(pool: asyncpg.Pool | None, challenge_id: int) -> None:
    rt = _active_challenges.get(challenge_id)
    if rt:
        task = rt.get("task")
        if task and not task.done():
            task.cancel()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE engagement_challenges SET active=FALSE, ended_at=NOW() WHERE id=$1",
                    challenge_id,
                )
        except Exception as exc:
            logger.warning(f"[engagement] challenge_cancel DB error: {exc}")
    _active_challenges.pop(challenge_id, None)


async def challenge_update_mode(
    pool: asyncpg.Pool | None, challenge_id: int, mode: str
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE engagement_challenges SET mode=$2 WHERE id=$1", challenge_id, mode
            )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_update_mode error: {exc}")


async def challenge_update_title(
    pool: asyncpg.Pool | None, challenge_id: int, title: str
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE engagement_challenges SET title=$2 WHERE id=$1", challenge_id, title
            )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_update_title error: {exc}")


async def challenge_update_ends_at(
    pool: asyncpg.Pool | None, challenge_id: int, duration_seconds: int
) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE engagement_challenges "
                "SET ends_at = NOW() + ($2::int) * INTERVAL '1 second' WHERE id=$1",
                challenge_id,
                duration_seconds,
            )
    except Exception as exc:
        logger.warning(f"[engagement] challenge_update_ends_at error: {exc}")


def parse_duration_to_seconds(value: str | None) -> int | None:
    """Parse human-readable duration: '10d', '3h30m', '900' (seconds)."""
    if value is None:
        return None
    v = value.strip()
    if v.isdigit():
        return int(v)
    pattern = re.compile(
        r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE
    )
    m = pattern.match(v)
    if not m or not any(m.groups()):
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable string."""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs:
        parts.append(f"{secs}s")
    return "".join(parts) or "0s"


# ---------------------------------------------------------------------------
# Challenge runtime scheduling
# ---------------------------------------------------------------------------


async def _run_challenge_timer(
    bot: discord.Client, challenge_id: int, duration_seconds: int
) -> None:
    try:
        await asyncio.sleep(duration_seconds)
    except asyncio.CancelledError:
        return
    await finalize_and_announce_challenge(bot, challenge_id)


async def schedule_challenge(
    bot: discord.Client,
    pool: asyncpg.Pool | None,
    challenge_id: int,
    guild_id: int,
    mode: str,
    title: str | None,
    duration_seconds: int,
    channel_id: int,
) -> None:
    now_ts = time.time()
    task = bot.loop.create_task(_run_challenge_timer(bot, challenge_id, duration_seconds))
    _active_challenges[challenge_id] = {
        "guild_id": guild_id,
        "mode": mode,
        "title": title,
        "start_ts": now_ts,
        "end_ts": now_ts + duration_seconds,
        "channel_id": channel_id,
        "task": task,
        "message_counts": {},
    }


async def reschedule_challenge(
    bot: discord.Client,
    pool: asyncpg.Pool | None,
    challenge_id: int,
    new_duration_seconds: int,
) -> None:
    rt = _active_challenges.get(challenge_id)
    if not rt:
        return
    task = rt.get("task")
    if task and not task.done():
        task.cancel()
    now_ts = time.time()
    rt["start_ts"] = now_ts
    rt["end_ts"] = now_ts + new_duration_seconds
    rt["task"] = bot.loop.create_task(
        _run_challenge_timer(bot, challenge_id, new_duration_seconds)
    )


async def rehydrate_challenges(bot: discord.Client, pool: asyncpg.Pool | None) -> None:
    """Restore active challenges from DB after bot restart."""
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, guild_id, mode, started_at, ends_at, title, channel_id
                FROM engagement_challenges
                WHERE active=TRUE
                ORDER BY started_at ASC NULLS LAST, id ASC
                """
            )
        now = datetime.now(UTC)
        for row in rows:
            challenge_id = int(row["id"])
            ends_at: datetime | None = row["ends_at"]
            remaining = 0
            if isinstance(ends_at, datetime):
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=UTC)
                remaining = int((ends_at - now).total_seconds())
            if remaining <= 0:
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE engagement_challenges SET active=FALSE, ended_at=NOW() WHERE id=$1",
                            challenge_id,
                        )
                except Exception:
                    pass
                continue
            counts: dict[int, int] = {}
            try:
                async with pool.acquire() as conn:
                    prows = await conn.fetch(
                        "SELECT user_id, message_count FROM engagement_participants WHERE challenge_id=$1",
                        challenge_id,
                    )
                    for pr in prows:
                        counts[int(pr["user_id"])] = int(pr["message_count"])
            except Exception as exc:
                logger.warning(f"[engagement] rehydrate counts error: {exc}")
            # Derive start_ts from the DB started_at so the progress bar is correct
            # after a bot restart. Fall back to derived value if started_at is missing.
            started_at: datetime | None = row["started_at"]
            if isinstance(started_at, datetime):
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                real_start_ts = started_at.timestamp()
            else:
                real_start_ts = time.time() - (
                    (ends_at.timestamp() - time.time()) if isinstance(ends_at, datetime) else 0
                )
            task = bot.loop.create_task(_run_challenge_timer(bot, challenge_id, remaining))
            _active_challenges[challenge_id] = {
                "guild_id": int(row["guild_id"]),
                "mode": str(row["mode"] or "leaderboard"),
                "title": row["title"],
                "start_ts": real_start_ts,
                "end_ts": ends_at.timestamp() if isinstance(ends_at, datetime) else time.time() + remaining,
                "channel_id": int(row["channel_id"]) if row["channel_id"] else 0,
                "task": task,
                "message_counts": counts,
            }
            logger.info(
                f"[engagement] Restored challenge id={challenge_id}, remaining={remaining}s"
            )
    except Exception as exc:
        logger.error(f"[engagement] rehydrate_challenges error: {exc}")


async def finalize_and_announce_challenge(
    bot: discord.Client, challenge_id: int
) -> None:
    """Determine winner, announce result, assign badge+role, clean runtime state."""
    from utils.db_helpers import get_bot_db_pool

    rt = _active_challenges.get(challenge_id, {})
    mode: str = rt.get("mode", "leaderboard")
    channel_id: int = rt.get("channel_id", 0)
    guild_id: int | None = rt.get("guild_id")
    title: str = rt.get("title") or "Challenge"

    pool = get_bot_db_pool(bot)
    res = await challenge_end(pool, challenge_id, mode)

    channel = bot.get_channel(channel_id)
    guild: discord.Guild | None = None
    if isinstance(channel, discord.TextChannel):
        guild = channel.guild
    if guild is None and guild_id:
        guild = bot.get_guild(guild_id)

    if res is not None:
        winner_id, winner_count = res
        winner_member: discord.Member | None = None
        if guild:
            winner_member = guild.get_member(winner_id)
        mention = winner_member.mention if winner_member else f"<@{winner_id}>"
        embed = EmbedBuilder.success(
            title=f"🏆 {safe_embed_text(title, 250)}",
            description=f"Congratulations {mention}! 🎉\nWon with **{winner_count} messages**!",
        )
        target = channel if isinstance(channel, discord.TextChannel) else None
        if target is None and guild:
            target = guild.system_channel
        if target:
            try:
                await target.send(embed=embed)
            except Exception as exc:
                logger.warning(f"[engagement] announce challenge result error: {exc}")

        # Award badge
        if pool and guild_id:
            await add_badge(pool, guild_id, winner_id, "winner")

        # Assign winner role if configured
        if guild:
            try:
                settings = getattr(bot, "settings", None)
                if settings:
                    role_id = await settings.get(guild_id, "engagement", "challenge_winner_role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role and winner_member:
                            await winner_member.add_roles(role, reason="Challenge winner")
            except Exception as exc:
                logger.warning(f"[engagement] assign winner role error: {exc}")
    else:
        target = channel if isinstance(channel, discord.TextChannel) else None
        if target:
            try:
                await target.send("No participants in this challenge.")
            except Exception:
                pass

    _active_challenges.pop(challenge_id, None)


# ---------------------------------------------------------------------------
# Weekly messages indexing
# ---------------------------------------------------------------------------


async def weekly_index_message(
    pool: asyncpg.Pool,
    guild_id: int,
    message_id: int,
    channel_id: int,
    user_id: int,
    created_at_ts: int,
    has_image: bool,
    is_food: bool,
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO engagement_weekly_messages
                    (guild_id, message_id, channel_id, user_id, created_at, has_image, is_food)
                VALUES($1, $2, $3, $4, TO_TIMESTAMP($5), $6, $7)
                ON CONFLICT (guild_id, message_id) DO NOTHING
                """,
                guild_id,
                message_id,
                channel_id,
                user_id,
                created_at_ts,
                has_image,
                is_food,
            )
    except Exception as exc:
        logger.warning(f"[engagement] weekly_index_message error: {exc}")


async def weekly_increment_reaction(
    pool: asyncpg.Pool, guild_id: int, message_id: int
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE engagement_weekly_messages SET reactions_count = reactions_count + 1 "
                "WHERE guild_id=$1 AND message_id=$2",
                guild_id,
                message_id,
            )
    except Exception as exc:
        logger.warning(f"[engagement] weekly_increment_reaction error: {exc}")


# ---------------------------------------------------------------------------
# Weekly Awards computation
# ---------------------------------------------------------------------------


async def compute_weekly_awards(
    bot: discord.Client,
    guild_id: int,
    award_channel_id: int | None,
    award_configs: list[dict[str, Any]],
) -> None:
    """
    Compute and announce weekly awards for a guild.

    award_configs is a list of dicts, each with:
        key       — unique award key (e.g. "motivator")
        label     — display label (e.g. "📣 Motivator")
        subtitle  — description shown in announcement embed
        filter    — "non_food" | "food" | "image" | "reactions"
        role_id   — optional Discord role ID to assign winner (int or None)
    """
    from utils.db_helpers import get_bot_db_pool

    pool = get_bot_db_pool(bot)
    if pool is None:
        logger.warning("[engagement] compute_weekly_awards: no DB pool")
        return

    now = datetime.now(UTC)
    weekday = now.weekday()  # Monday = 0
    this_monday = (now - timedelta(days=weekday)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    week_end = this_monday
    week_start = week_end - timedelta(days=7)

    try:
        # Upsert the week record
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM engagement_weekly_awards "
                "WHERE guild_id=$1 AND week_start=$2 AND week_end=$3",
                guild_id,
                week_start.date(),
                week_end.date(),
            )
            if existing:
                week_id = int(existing["id"])
            else:
                row = await conn.fetchrow(
                    "INSERT INTO engagement_weekly_awards(guild_id, week_start, week_end) "
                    "VALUES($1, $2, $3) RETURNING id",
                    guild_id,
                    week_start.date(),
                    week_end.date(),
                )
                week_id = int(row["id"]) if row else None
        if week_id is None:
            logger.error("[engagement] compute_weekly_awards: could not create week record")
            return

        results: list[dict[str, Any]] = []
        for cfg in award_configs:
            award_key: str = cfg["key"]
            filt: str = cfg.get("filter", "non_food")

            async with pool.acquire() as conn:
                if filt == "reactions":
                    row = await conn.fetchrow(
                        """
                        SELECT user_id, message_id, reactions_count AS cnt
                        FROM engagement_weekly_messages
                        WHERE guild_id=$1 AND created_at >= $2 AND created_at < $3
                            AND has_image = TRUE
                        ORDER BY reactions_count DESC NULLS LAST
                        LIMIT 1
                        """,
                        guild_id,
                        week_start,
                        week_end,
                    )
                elif filt == "food":
                    row = await conn.fetchrow(
                        """
                        SELECT user_id, COUNT(*) AS cnt
                        FROM engagement_weekly_messages
                        WHERE guild_id=$1 AND created_at >= $2 AND created_at < $3
                            AND is_food = TRUE
                        GROUP BY user_id ORDER BY cnt DESC, user_id ASC LIMIT 1
                        """,
                        guild_id,
                        week_start,
                        week_end,
                    )
                elif filt == "image":
                    row = await conn.fetchrow(
                        """
                        SELECT user_id, COUNT(*) AS cnt
                        FROM engagement_weekly_messages
                        WHERE guild_id=$1 AND created_at >= $2 AND created_at < $3
                            AND has_image = TRUE
                        GROUP BY user_id ORDER BY cnt DESC, user_id ASC LIMIT 1
                        """,
                        guild_id,
                        week_start,
                        week_end,
                    )
                else:  # non_food (default)
                    row = await conn.fetchrow(
                        """
                        SELECT user_id, COUNT(*) AS cnt
                        FROM engagement_weekly_messages
                        WHERE guild_id=$1 AND created_at >= $2 AND created_at < $3
                            AND is_food = FALSE
                        GROUP BY user_id ORDER BY cnt DESC, user_id ASC LIMIT 1
                        """,
                        guild_id,
                        week_start,
                        week_end,
                    )

            if not row:
                continue

            user_id = int(row["user_id"])
            metric = int(row["cnt"])
            message_id: int | None = None
            if filt == "reactions" and row["message_id"]:
                message_id = int(row["message_id"])

            # Upsert result
            async with pool.acquire() as conn:
                existing_r = await conn.fetchrow(
                    "SELECT id FROM engagement_weekly_results WHERE week_id=$1 AND award_key=$2",
                    week_id,
                    award_key,
                )
                if existing_r:
                    await conn.execute(
                        "UPDATE engagement_weekly_results "
                        "SET user_id=$3, metric=$4, message_id=$5 "
                        "WHERE week_id=$1 AND award_key=$2",
                        week_id,
                        award_key,
                        user_id,
                        metric,
                        message_id,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO engagement_weekly_results"
                        "(week_id, award_key, user_id, metric, message_id) "
                        "VALUES($1, $2, $3, $4, $5)",
                        week_id,
                        award_key,
                        user_id,
                        metric,
                        message_id,
                    )

            results.append({
                **cfg,
                "user_id": user_id,
                "metric": metric,
                "message_id": message_id,
            })

        # Assign roles + badges
        guild: discord.Guild | None = bot.get_guild(guild_id)
        for entry in results:
            await add_badge(pool, guild_id, int(entry["user_id"]), str(entry["key"]))
            role_id = entry.get("role_id")
            if guild and role_id:
                try:
                    role = guild.get_role(int(role_id))
                    if role:
                        # Remove from current holders first
                        for holder in list(guild.members):
                            if role in holder.roles:
                                try:
                                    await holder.remove_roles(
                                        role,
                                        reason=f"Weekly award {entry['key']} to new winner",
                                    )
                                except Exception:
                                    pass
                        member = guild.get_member(int(entry["user_id"]))
                        if member:
                            await member.add_roles(
                                role, reason=f"Weekly award: {entry['key']}"
                            )
                except Exception as exc:
                    logger.warning(f"[engagement] weekly role assign error: {exc}")

        # Announce
        if award_channel_id and results:
            ch = bot.get_channel(award_channel_id)
            if isinstance(ch, discord.TextChannel):
                period_end = week_end - timedelta(seconds=1)
                period_text = (
                    f"Week of {week_start.strftime('%d %b %H:%M')} "
                    f"to {period_end.strftime('%d %b %H:%M')} (Monday to Monday)"
                )
                guild_name = ch.guild.name if ch.guild else "Weekly"
                embed = discord.Embed(
                    title=f"🏆 {guild_name} Weekly Awards",
                    description=period_text,
                    color=discord.Color.gold(),
                )
                sections: list[str] = []
                for entry in results:
                    uid = int(entry["user_id"])
                    metric = int(entry["metric"])
                    label: str = safe_embed_text(entry.get("label", entry["key"].title()), 256)
                    subtitle: str = safe_embed_text(entry.get("subtitle", ""), 256)
                    lines = [f"**{label}**", f"<@{uid}>", f"Score: **{metric}**"]
                    if subtitle:
                        lines.append(subtitle)
                    if entry.get("message_id"):
                        lines.append(f"Message ID: `{entry['message_id']}`")
                    sections.append("\n".join(lines))
                embed.description = f"{period_text}\n\n" + "\n\n".join(sections)
                embed.set_footer(text="Congratulations to all winners! 💪")
                try:
                    await ch.send(embed=embed)
                except Exception as exc:
                    logger.warning(f"[engagement] weekly announce error: {exc}")
        elif not results:
            logger.info(f"[engagement] weekly awards: no candidates found for guild {guild_id}")

    except Exception as exc:
        logger.error(f"[engagement] compute_weekly_awards error: {exc}")
