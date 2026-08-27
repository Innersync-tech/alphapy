"""Opt-in Discord DM check-in nudges (Phase 5A).

No Grok, no journal text — fixed English invite to `/agent start` / App agent home.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

from agents.fatigue import load_raw_agent_prefs
from agents.profile import load_agent_prefs, merge_agent_prefs_fields
from utils.db_helpers import acquire_safe
from utils.settings_helpers import is_module_enabled
from utils.supabase_client import _supabase_get

logger = logging.getLogger("alphapy.agents.nudges")

NUDGE_COOLDOWN = timedelta(hours=24)
NUDGE_BATCH_LIMIT = 25

_DEFAULT_APP_BASE = "https://app.innersync.tech"


@dataclass(frozen=True)
class NudgeCandidate:
    innersync_user_id: str
    discord_user_id: int


def app_agent_home_url() -> str:
    """Public App agent surface for DM deep-link."""
    try:
        import config
    except ImportError:  # pragma: no cover
        return f"{_DEFAULT_APP_BASE}/dashboard/agent"

    from utils.core_discord_integration import normalize_http_url

    raw = getattr(config, "INNERSYNC_APP_URL", None) or _DEFAULT_APP_BASE
    base = normalize_http_url(str(raw).strip().rstrip("/")) or _DEFAULT_APP_BASE
    return f"{base.rstrip('/')}/dashboard/agent"


def build_nudge_dm_text(*, app_url: str | None = None) -> str:
    """Fixed English DM body (no journal / LLM content)."""
    url = app_url or app_agent_home_url()
    return (
        "🪞 **Alphapy check-in**\n\n"
        "When you're ready, run `/agent start` in a server where agents are enabled, "
        f"or open your agent in the App: {url}\n\n"
        "Disable these reminders anytime with `/agent nudges disable` "
        "or in Innersync App → Settings → Alphapy → Check-ins."
    )


def agent_nudges_enabled(prefs: dict[str, str | bool]) -> bool:
    return bool(prefs.get("agent_nudges_enabled"))


def is_due_for_nudge(last_sent_at: datetime | None, *, now: datetime | None = None) -> bool:
    """True when never sent or last send is older than the cooldown."""
    if last_sent_at is None:
        return True
    current = now or datetime.now(UTC)
    if last_sent_at.tzinfo is None:
        last_sent_at = last_sent_at.replace(tzinfo=UTC)
    return current - last_sent_at >= NUDGE_COOLDOWN


async def set_agent_nudges_enabled(innersync_user_id: str, enabled: bool) -> dict[str, str | bool]:
    """Merge agent_nudges_enabled into Tier 1 prefs (fail-closed if prefs unloadable)."""
    raw = await load_raw_agent_prefs(innersync_user_id)
    if raw is None:
        raise RuntimeError(
            f"Refusing nudge prefs write for {innersync_user_id}: "
            "existing agent_prefs could not be loaded"
        )
    merged = {**raw, "agent_nudges_enabled": enabled}
    return await merge_agent_prefs_fields(innersync_user_id, merged)


async def fetch_opted_in_user_ids(*, limit: int = 200) -> list[str]:
    """Supabase users with agent_nudges_enabled=true.

    Uses jsonb **contains** (`cs.{"agent_nudges_enabled":true}`) instead of
    `->>…=eq.true`. The text extractor path often returns zero rows for JSON
    booleans under PostgREST while still HTTP 200 — silent empty ticks.
    """
    try:
        rows = await _supabase_get(
            "app_user_settings",
            {
                "select": "user_id,agent_prefs",
                "agent_prefs": 'cs.{"agent_nudges_enabled":true}',
                "limit": str(limit),
            },
        )
    except Exception as exc:
        logger.warning("Failed to list nudge opt-ins: %s", exc)
        return []

    out: list[str] = []
    for row in rows:
        uid = row.get("user_id")
        if not uid:
            continue
        prefs = row.get("agent_prefs")
        if isinstance(prefs, dict) and not bool(prefs.get("agent_nudges_enabled")):
            continue
        out.append(str(uid))
    return out


async def load_discord_links_for_users(
    pool: Any,
    innersync_user_ids: list[str],
) -> dict[str, int]:
    """Map innersync UUID → discord snowflake for linked users."""
    if not innersync_user_ids:
        return {}
    async with acquire_safe(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT innersync_user_id::text AS uid, discord_user_id
            FROM alphapy_discord_links
            WHERE innersync_user_id = ANY($1::uuid[])
            """,
            innersync_user_ids,
        )
    return {str(r["uid"]): int(r["discord_user_id"]) for r in rows}


async def load_last_sent_map(pool: Any, innersync_user_ids: list[str]) -> dict[str, datetime | None]:
    if not innersync_user_ids:
        return {}
    async with acquire_safe(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT innersync_user_id::text AS uid, last_sent_at
            FROM agent_nudge_state
            WHERE innersync_user_id = ANY($1::uuid[])
            """,
            innersync_user_ids,
        )
    return {str(r["uid"]): r["last_sent_at"] for r in rows}


async def mark_nudge_sent(
    pool: Any,
    *,
    innersync_user_id: str,
    discord_user_id: int,
    sent_at: datetime | None = None,
) -> None:
    when = sent_at or datetime.now(UTC)
    async with acquire_safe(pool) as conn:
        await conn.execute(
            """
            INSERT INTO agent_nudge_state (innersync_user_id, discord_user_id, last_sent_at)
            VALUES ($1::uuid, $2, $3)
            ON CONFLICT (innersync_user_id)
            DO UPDATE SET
                discord_user_id = EXCLUDED.discord_user_id,
                last_sent_at = EXCLUDED.last_sent_at
            """,
            innersync_user_id,
            discord_user_id,
            when,
        )


def guild_has_agents_enabled(bot: discord.Client, guild_id: int) -> bool:
    """True when guild `agents.enabled` is on (default False)."""
    return is_module_enabled(bot, guild_id, "agents")


async def user_in_guild(guild: discord.Guild, discord_user_id: int) -> bool:
    """True if discord_user_id is a member (cache first, then API fetch)."""
    if guild.get_member(discord_user_id) is not None:
        return True
    try:
        await guild.fetch_member(discord_user_id)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException as exc:
        logger.debug(
            "fetch_member failed guild=%s user=%s: %s",
            guild.id,
            discord_user_id,
            exc,
        )
        return False


async def user_has_agents_enabled_guild(bot: discord.Client, discord_user_id: int) -> bool:
    """True if the user is in at least one mutual guild with agents.enabled."""
    for guild in getattr(bot, "guilds", []) or []:
        if not guild_has_agents_enabled(bot, guild.id):
            continue
        if await user_in_guild(guild, discord_user_id):
            return True
    return False


async def list_due_nudge_candidates(
    bot: discord.Client,
    pool: Any,
    *,
    batch_limit: int = NUDGE_BATCH_LIMIT,
    now: datetime | None = None,
) -> list[NudgeCandidate]:
    """Opted-in + linked + cooldown elapsed + agents-enabled guild."""
    opted_in = await fetch_opted_in_user_ids(limit=max(batch_limit * 4, 100))
    if not opted_in:
        logger.info("Nudge tick: opted_in=0 (no candidates)")
        return []

    links = await load_discord_links_for_users(pool, opted_in)
    if not links:
        logger.info(
            "Nudge tick: opted_in=%s linked=0 (missing alphapy_discord_links)",
            len(opted_in),
        )
        return []

    last_sent = await load_last_sent_map(pool, list(links.keys()))
    current = now or datetime.now(UTC)
    due: list[NudgeCandidate] = []
    skipped_cooldown = 0
    skipped_prefs = 0
    skipped_guild = 0

    for uid, discord_id in links.items():
        if not is_due_for_nudge(last_sent.get(uid), now=current):
            skipped_cooldown += 1
            continue
        # Double-check prefs in case PostgREST JSON filter is unavailable.
        prefs = await load_agent_prefs(uid)
        if not agent_nudges_enabled(prefs):
            skipped_prefs += 1
            continue
        if not await user_has_agents_enabled_guild(bot, discord_id):
            skipped_guild += 1
            continue
        due.append(NudgeCandidate(innersync_user_id=uid, discord_user_id=discord_id))
        if len(due) >= batch_limit:
            break

    logger.info(
        "Nudge tick: opted_in=%s linked=%s due=%s "
        "skipped_cooldown=%s skipped_prefs=%s skipped_guild=%s",
        len(opted_in),
        len(links),
        len(due),
        skipped_cooldown,
        skipped_prefs,
        skipped_guild,
    )
    return due


async def send_nudge_dm(bot: discord.Client, discord_user_id: int) -> bool:
    """Send the fixed nudge DM. Returns True on success."""
    try:
        user = await bot.fetch_user(discord_user_id)
        if user is None:
            return False
        await user.send(build_nudge_dm_text())
        return True
    except discord.Forbidden:
        logger.info("Nudge DM blocked (closed DMs) for discord_user_id=%s", discord_user_id)
        return False
    except Exception as exc:
        logger.warning("Nudge DM failed for discord_user_id=%s: %s", discord_user_id, exc)
        return False


async def run_nudge_tick(bot: discord.Client, pool: Any) -> int:
    """Process one batch of due nudges. Returns number of successful DMs."""
    try:
        import config as cfg
    except ImportError:  # pragma: no cover
        return 0
    if not getattr(cfg, "ALPHAPY_AGENTS_ENABLED", False):
        return 0

    candidates = await list_due_nudge_candidates(bot, pool)
    sent = 0
    failed = 0
    for candidate in candidates:
        ok = await send_nudge_dm(bot, candidate.discord_user_id)
        if not ok:
            failed += 1
            continue
        try:
            await mark_nudge_sent(
                pool,
                innersync_user_id=candidate.innersync_user_id,
                discord_user_id=candidate.discord_user_id,
            )
            sent += 1
        except Exception as exc:
            logger.warning(
                "Failed to record nudge send for %s: %s",
                candidate.innersync_user_id,
                exc,
            )
    if candidates:
        logger.info(
            "Agent nudge tick done: due=%s sent=%s failed_dm=%s",
            len(candidates),
            sent,
            failed,
        )
    return sent


# Re-export for callers that already import load_raw from fatigue path
__all__ = [
    "NudgeCandidate",
    "NUDGE_BATCH_LIMIT",
    "NUDGE_COOLDOWN",
    "agent_nudges_enabled",
    "app_agent_home_url",
    "build_nudge_dm_text",
    "is_due_for_nudge",
    "list_due_nudge_candidates",
    "run_nudge_tick",
    "send_nudge_dm",
    "set_agent_nudges_enabled",
    "user_has_agents_enabled_guild",
    "user_in_guild",
]
