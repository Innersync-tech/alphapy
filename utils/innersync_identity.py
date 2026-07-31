"""
Innersync identity: map Supabase Auth `sub` (UUID) ↔ Discord snowflake.

Primary store: Railway table `alphapy_discord_links`.
Optional fallback: Supabase `profiles` via `get_discord_id_for_user` for
migration when the explicit link row is not yet created.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import asyncpg

from utils.db_helpers import acquire_safe
from utils.logger import logger

_CACHE_TTL_SECONDS = 60.0
_cache_lock = threading.Lock()
# innersync_user_id (str) -> (discord_user_id, expires_at)
_by_innersync: dict[str, tuple[int, float]] = {}
# discord_user_id -> (innersync_user_id str, expires_at)
_by_discord: dict[int, tuple[str, float]] = {}

# Process-local observability (mirrors utils.premium_guard)
_stats_resolve_total = 0
_stats_hit_links = 0
_stats_miss = 0
_stats_db_error = 0
_stats_profile_fallback_used = 0
_stats_jwt_unlinked_403 = 0
_stats_link_webhook_ok = 0
_stats_link_webhook_conflict = 0
_stats_link_webhook_503 = 0


def _inc(name: str) -> None:
    global _stats_resolve_total, _stats_hit_links, _stats_miss, _stats_db_error
    global _stats_profile_fallback_used, _stats_jwt_unlinked_403
    global _stats_link_webhook_ok, _stats_link_webhook_conflict, _stats_link_webhook_503
    with _cache_lock:
        if name == "resolve_total":
            _stats_resolve_total += 1
        elif name == "hit_links":
            _stats_hit_links += 1
        elif name == "miss":
            _stats_miss += 1
        elif name == "db_error":
            _stats_db_error += 1
        elif name == "profile_fallback_used":
            _stats_profile_fallback_used += 1
        elif name == "jwt_unlinked_403":
            _stats_jwt_unlinked_403 += 1
        elif name == "link_webhook_ok":
            _stats_link_webhook_ok += 1
        elif name == "link_webhook_conflict":
            _stats_link_webhook_conflict += 1
        elif name == "link_webhook_503":
            _stats_link_webhook_503 += 1


def get_identity_stats() -> dict[str, Any]:
    """Return identity resolve counters for observability (same process only)."""
    with _cache_lock:
        return {
            "identity_resolve_total": _stats_resolve_total,
            "identity_resolve_hit_links": _stats_hit_links,
            "identity_resolve_miss": _stats_miss,
            "identity_resolve_db_error": _stats_db_error,
            "identity_profile_fallback_used": _stats_profile_fallback_used,
            "identity_jwt_unlinked_403": _stats_jwt_unlinked_403,
            "identity_link_webhook_ok": _stats_link_webhook_ok,
            "identity_link_webhook_conflict": _stats_link_webhook_conflict,
            "identity_link_webhook_503": _stats_link_webhook_503,
        }


def record_jwt_unlinked_403() -> None:
    _inc("jwt_unlinked_403")


def record_link_webhook(status: str) -> None:
    if status == "ok":
        _inc("link_webhook_ok")
    elif status == "conflict":
        _inc("link_webhook_conflict")
    elif status == "503":
        _inc("link_webhook_503")


def format_identity_telemetry_notes(stats: dict[str, Any] | None = None) -> str:
    s = stats or get_identity_stats()
    return (
        "identity:"
        f"hit={s.get('identity_resolve_hit_links', 0)}"
        f"/miss={s.get('identity_resolve_miss', 0)}"
        f"/fb={s.get('identity_profile_fallback_used', 0)}"
        f"/403={s.get('identity_jwt_unlinked_403', 0)}"
    )


def invalidate_identity_cache(
    *,
    innersync_user_id: str | None = None,
    discord_user_id: int | None = None,
) -> None:
    """Clear cached resolution for the given ids (both directions)."""
    with _cache_lock:
        if innersync_user_id:
            _by_innersync.pop(str(innersync_user_id).lower(), None)
        if discord_user_id is not None:
            _by_discord.pop(int(discord_user_id), None)
        if innersync_user_id:
            iu = str(innersync_user_id).lower()
            for did, (sid, _) in list(_by_discord.items()):
                if sid.lower() == iu:
                    _by_discord.pop(did, None)
        if discord_user_id is not None:
            for iu_key, (did, _) in list(_by_innersync.items()):
                if did == discord_user_id:
                    _by_innersync.pop(iu_key, None)


def _cache_get_by_innersync(innersync_user_id: str) -> int | None:
    now = time.monotonic()
    with _cache_lock:
        hit = _by_innersync.get(innersync_user_id.lower())
        if hit and hit[1] > now:
            return hit[0]
    return None


def _cache_set_by_innersync(innersync_user_id: str, discord_user_id: int) -> None:
    exp = time.monotonic() + _CACHE_TTL_SECONDS
    iu = innersync_user_id.lower()
    with _cache_lock:
        _by_innersync[iu] = (discord_user_id, exp)
        _by_discord[discord_user_id] = (iu, exp)


def _cache_get_by_discord(discord_user_id: int) -> str | None:
    now = time.monotonic()
    with _cache_lock:
        hit = _by_discord.get(discord_user_id)
        if hit and hit[1] > now:
            return hit[0]
    return None


def _cache_set_by_discord(discord_user_id: int, innersync_user_id: str) -> None:
    exp = time.monotonic() + _CACHE_TTL_SECONDS
    iu = innersync_user_id.lower()
    with _cache_lock:
        _by_discord[discord_user_id] = (iu, exp)
        _by_innersync[iu] = (discord_user_id, exp)


async def get_discord_id_for_innersync(
    pool: asyncpg.Pool | None,
    innersync_user_id: str,
    *,
    allow_profile_fallback: bool = False,
) -> int | None:
    """Return Discord snowflake for a Supabase Auth user id, or None."""
    _inc("resolve_total")
    try:
        uuid.UUID(innersync_user_id)
    except (ValueError, TypeError, AttributeError):
        _inc("miss")
        return None

    cached = _cache_get_by_innersync(innersync_user_id)
    if cached is not None:
        _inc("hit_links")
        return cached

    if pool is None:
        if allow_profile_fallback:
            out = await _fallback_discord_from_supabase_profiles(innersync_user_id)
            if out is None:
                _inc("miss")
            return out
        _inc("miss")
        return None

    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT discord_user_id
                FROM alphapy_discord_links
                WHERE innersync_user_id = $1::uuid
                LIMIT 1
                """,
                innersync_user_id,
            )
    except Exception as e:
        logger.warning("alphapy_discord_links lookup by innersync id failed: %s", e)
        _inc("db_error")
        if allow_profile_fallback:
            out = await _fallback_discord_from_supabase_profiles(innersync_user_id)
            if out is None:
                _inc("miss")
            return out
        _inc("miss")
        return None

    if row and row["discord_user_id"] is not None:
        did = int(row["discord_user_id"])
        _cache_set_by_innersync(innersync_user_id, did)
        _inc("hit_links")
        return did

    if allow_profile_fallback:
        out = await _fallback_discord_from_supabase_profiles(innersync_user_id)
        if out is None:
            _inc("miss")
        return out
    _inc("miss")
    return None


async def get_innersync_id_for_discord(
    pool: asyncpg.Pool | None,
    discord_user_id: int,
    *,
    allow_profile_fallback: bool = False,
) -> str | None:
    """
    Return Innersync (Supabase) user id string for a Discord snowflake.

    Runtime SoT is Railway ``alphapy_discord_links`` (via ``/link``). Profile fallback
    (Supabase ``profiles.discord_id``) is **off by default**. Pass
    ``allow_profile_fallback=True`` only for documented one-off UX (e.g. ``/unlink``
    legacy hint). Run ``scripts/backfill_discord_links_from_profiles.py`` before
    relying on links-only resolution in production.
    """
    _inc("resolve_total")
    cached = _cache_get_by_discord(discord_user_id)
    if cached is not None:
        _inc("hit_links")
        return cached

    if pool is None:
        if allow_profile_fallback:
            out = await _fallback_innersync_from_supabase_profiles(discord_user_id)
            if out is None:
                _inc("miss")
            return out
        _inc("miss")
        return None

    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT innersync_user_id::text AS iid
                FROM alphapy_discord_links
                WHERE discord_user_id = $1
                LIMIT 1
                """,
                discord_user_id,
            )
    except Exception as e:
        logger.warning("alphapy_discord_links lookup by discord id failed: %s", e)
        _inc("db_error")
        if allow_profile_fallback:
            out = await _fallback_innersync_from_supabase_profiles(discord_user_id)
            if out is None:
                _inc("miss")
            return out
        _inc("miss")
        return None

    if row and row["iid"]:
        iid = str(row["iid"])
        _cache_set_by_discord(discord_user_id, iid)
        _inc("hit_links")
        return iid

    if allow_profile_fallback:
        out = await _fallback_innersync_from_supabase_profiles(discord_user_id)
        if out is None:
            _inc("miss")
        return out
    _inc("miss")
    return None


async def _fallback_discord_from_supabase_profiles(innersync_user_id: str) -> int | None:
    from utils.supabase_client import get_discord_id_for_user

    _inc("profile_fallback_used")
    raw = await get_discord_id_for_user(innersync_user_id)
    if not raw:
        return None
    try:
        did = int(raw)
    except (TypeError, ValueError):
        return None
    _cache_set_by_innersync(innersync_user_id, did)
    return did


async def _fallback_innersync_from_supabase_profiles(discord_user_id: int) -> str | None:
    from utils.supabase_client import get_user_id_for_discord

    _inc("profile_fallback_used")
    uid = await get_user_id_for_discord(discord_user_id)
    if uid:
        _cache_set_by_discord(discord_user_id, uid)
    return uid


async def resolve_innersync_jwt_sub_to_discord_int(
    pool: asyncpg.Pool | None,
    innersync_user_id: str,
    *,
    allow_profile_fallback: bool = False,
) -> int | None:
    """Resolve JWT `sub` (UUID) to Discord id for Railway tables and automod."""
    return await get_discord_id_for_innersync(
        pool,
        innersync_user_id,
        allow_profile_fallback=allow_profile_fallback,
    )


async def upsert_discord_link(
    pool: asyncpg.Pool,
    *,
    innersync_user_id: str,
    discord_user_id: int,
    link_source: str | None = None,
) -> tuple[str, str | None]:
    """
    Insert or validate a link row. Returns (status, error_detail).

    status: "ok" | "conflict" | "noop"
    """
    try:
        iu = str(uuid.UUID(innersync_user_id))
    except (ValueError, TypeError, AttributeError):
        return "conflict", "innersync_user_id must be a UUID"

    src = (link_source or "webhook")[:64] if link_source else "webhook"

    async with acquire_safe(pool) as conn:
        row_d = await conn.fetchrow(
            """
            SELECT innersync_user_id::text AS iu
            FROM alphapy_discord_links
            WHERE discord_user_id = $1
            """,
            discord_user_id,
        )
        row_i = await conn.fetchrow(
            "SELECT discord_user_id FROM alphapy_discord_links WHERE innersync_user_id = $1::uuid",
            iu,
        )

        if row_d and str(row_d["iu"]).lower() == iu.lower():
            invalidate_identity_cache(innersync_user_id=iu, discord_user_id=discord_user_id)
            return "noop", None

        if row_d:
            return "conflict", "This Discord account is already linked to another Innersync user."

        if row_i and int(row_i["discord_user_id"]) == discord_user_id:
            invalidate_identity_cache(innersync_user_id=iu, discord_user_id=discord_user_id)
            return "noop", None

        if row_i:
            return "conflict", "This Innersync user is already linked to another Discord account."

        await conn.execute(
            """
            INSERT INTO alphapy_discord_links (innersync_user_id, discord_user_id, link_source)
            VALUES ($1::uuid, $2, $3)
            """,
            iu,
            discord_user_id,
            src,
        )

    invalidate_identity_cache(innersync_user_id=iu, discord_user_id=discord_user_id)
    return "ok", None


async def delete_discord_link_for_discord_user(
    pool: asyncpg.Pool,
    discord_user_id: int,
) -> bool:
    """Remove link for this Discord user. Returns True if a row was deleted."""
    async with acquire_safe(pool) as conn:
        result = await conn.execute(
            "DELETE FROM alphapy_discord_links WHERE discord_user_id = $1",
            discord_user_id,
        )
    parts = str(result).split()
    n = 0
    if parts:
        try:
            n = int(parts[-1])
        except ValueError:
            n = 0
    invalidate_identity_cache(discord_user_id=discord_user_id)
    return n > 0
