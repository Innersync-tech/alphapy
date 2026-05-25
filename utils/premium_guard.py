"""
Premium tier guard: check if a user has premium in a guild.

Uses Core-API POST /api/premium/verify when configured, with local premium_subs table
as fallback. In-memory cache with TTL to reduce load.
Fail closed: on error returns False.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import asyncpg
import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.db_helpers import acquire_safe, create_db_pool
from utils.logger import logger

CORE_VERIFY_TIMEOUT = 5.0
_DEFAULT_CACHE_TTL = 300  # seconds

# (user_id, guild_id) -> (is_premium: bool, expires_at: float)
# Lock protects _cache across bot thread (is_premium/_set_cache) and webhook thread (invalidate_premium_cache).
# Also used to serialise _stats_* counter increments (avoids a separate lock).
_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_guild_cache: dict[int, tuple[bool, float]] = {}
_cache_lock = threading.Lock()
_pool: asyncpg.Pool | None = None

# Persistent HTTP client — reused across all Core-API calls to avoid per-call TCP handshakes.
_http_client: httpx.AsyncClient | None = None

# Optional counters for observability (incremented in is_premium)
_stats_total = 0
_stats_cache_hits = 0
_stats_core_api = 0
_stats_local = 0
_stats_transfers = 0
_stats_guild_cache_hits = 0
_stats_guild_cache_misses = 0


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first call."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=CORE_VERIFY_TIMEOUT)
    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client. Call during bot shutdown."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def premium_required_message(feature_name: str) -> str:
    """Return a short Mockingbird-style message when a non-premium user hits a gated feature."""
    return (
        f"{feature_name} is premium. Powerful enough? Get power with /premium."
    )


def _cache_ttl_seconds() -> int:
    return int(getattr(config, "PREMIUM_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL))


def _get_cached(user_id: int, guild_id: int) -> bool | None:
    key = (user_id, guild_id)
    with _cache_lock:
        if key not in _cache:
            return None
        is_premium, expires_at = _cache[key]
        if time.monotonic() > expires_at:
            del _cache[key]
            return None
        return is_premium


def _set_cache(user_id: int, guild_id: int, is_premium: bool) -> None:
    ttl = _cache_ttl_seconds()
    with _cache_lock:
        _cache[(user_id, guild_id)] = (is_premium, time.monotonic() + ttl)


def _get_cached_guild(guild_id: int) -> bool | None:
    with _cache_lock:
        if guild_id not in _guild_cache:
            return None
        is_premium, expires_at = _guild_cache[guild_id]
        if time.monotonic() > expires_at:
            del _guild_cache[guild_id]
            return None
        return is_premium


def _set_guild_cache(guild_id: int, is_premium: bool) -> None:
    ttl = _cache_ttl_seconds()
    with _cache_lock:
        _guild_cache[guild_id] = (is_premium, time.monotonic() + ttl)


def _clear_cache_for_user(user_id: int) -> None:
    """Remove all cache entries for this user (e.g. after transfer)."""
    with _cache_lock:
        keys_to_del = [k for k in _cache if k[0] == user_id]
        for k in keys_to_del:
            del _cache[k]


def invalidate_premium_cache(user_id: int, guild_id: int | None = None) -> None:
    """
    Invalidate premium cache so the next is_premium() check refetches from Core/DB.

    Called by the premium-invalidate webhook when Core notifies of a subscription
    change (payment, cancellation, expiry). If guild_id is None, clears all
    entries for this user; otherwise clears only (user_id, guild_id).
    Thread-safe: safe to call from webhook thread while bot thread uses cache.
    """
    if guild_id is not None:
        with _cache_lock:
            _cache.pop((user_id, guild_id), None)
            _guild_cache.pop(guild_id, None)
    else:
        _clear_cache_for_user(user_id)
        with _cache_lock:
            _guild_cache.clear()


def get_premium_cache_size() -> int:
    """Return the number of entries in the premium in-memory cache (for status/health display)."""
    with _cache_lock:
        return len(_cache) + len(_guild_cache)


async def _ensure_pool() -> asyncpg.Pool | None:
    """Create premium pool on first use (uses db_helpers, so registered for cleanup)."""
    global _pool
    if _pool is not None and not _pool.is_closing():
        return _pool
    dsn = getattr(config, "DATABASE_URL", None) or ""
    if not dsn:
        return None
    try:
        _pool = await create_db_pool(
            dsn,
            name="premium",
            min_size=1,
            max_size=5,
            command_timeout=10.0,
        )
        logger.info("Premium guard: DB pool ready")
        return _pool
    except Exception as e:
        logger.warning("Premium guard: could not create DB pool: %s", e)
        return None


async def _check_core_api(user_id: int, guild_id: int) -> bool | None:
    """Return True if premium, False if not, None if Core not configured or request failed."""
    url = getattr(config, "CORE_API_URL", None) or ""
    key = getattr(config, "ALPHAPY_SERVICE_KEY", None)
    if not url or not key:
        return None
    endpoint = f"{url}/api/premium/verify"
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    payload = {"user_id": user_id, "guild_id": guild_id}
    try:
        client = _get_http_client()
        response = await client.post(endpoint, json=payload, headers=headers)
        if not response.is_success:
            logger.debug(
                "Premium verify API non-2xx: status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            return None
        data = response.json()
        if isinstance(data, dict) and "premium" in data:
            return bool(data["premium"])
        return None
    except Exception as e:
        logger.debug("Premium verify API error: %s", e)
        return None


async def _check_local_db(user_id: int, guild_id: int) -> bool:
    """Query premium_subs for active subscription. Returns False on error (fail closed)."""
    pool = await _ensure_pool()
    if pool is None:
        return False
    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM premium_subs
                WHERE user_id = $1 AND guild_id = $2
                  AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                guild_id,
            )
            return row is not None
    except Exception as e:
        logger.warning("Premium guard: local DB check failed: %s", e)
        return False


def get_premium_guard_stats() -> dict[str, Any]:
    """Return current premium guard counters for observability (same process only)."""
    with _cache_lock:
        cache_size = len(_cache)
        guild_cache_size = len(_guild_cache)
    return {
        "premium_checks_total": _stats_total,
        "premium_checks_core_api": _stats_core_api,
        "premium_checks_local": _stats_local,
        "premium_cache_hits": _stats_cache_hits,
        "premium_transfers_count": _stats_transfers,
        "premium_cache_size": cache_size,
        "premium_guild_cache_size": guild_cache_size,
        "premium_guild_cache_hits": _stats_guild_cache_hits,
        "premium_guild_cache_misses": _stats_guild_cache_misses,
    }


async def is_premium(user_id: int, guild_id: int) -> bool:
    """
    Return True if the user has an active premium subscription in the guild.

    Multi-guild: guild_id is required. Returns False when guild_id is None or 0 (e.g. DMs).
    Checks cache first, then Core-API /premium/verify (if configured), then
    local premium_subs table. Fail closed: on any error returns False.
    """
    global _stats_total, _stats_cache_hits, _stats_core_api, _stats_local
    with _cache_lock:
        _stats_total += 1
    if guild_id is None or guild_id == 0:
        return False
    cached = _get_cached(user_id, guild_id)
    if cached is not None:
        with _cache_lock:
            _stats_cache_hits += 1
        return cached

    # Try Core-API first when configured
    core_result = await _check_core_api(user_id, guild_id)
    if core_result is not None:
        with _cache_lock:
            _stats_core_api += 1
        _set_cache(user_id, guild_id, core_result)
        return core_result

    # Fallback to local DB
    with _cache_lock:
        _stats_local += 1
    result = await _check_local_db(user_id, guild_id)
    _set_cache(user_id, guild_id, result)
    return result


async def get_premium_status(user_id: int, guild_id: int) -> dict[str, Any]:
    """
    Return premium status and details for a user in a guild.

    Multi-guild: guild_id is required. Returns premium=False when guild_id is None or 0.
    Returns dict with keys: premium (bool), tier (str|None), expires_at (datetime|None).
    """
    result: dict[str, Any] = {"premium": False, "tier": None, "expires_at": None}
    if guild_id is None or guild_id == 0:
        return result
    pool = await _ensure_pool()
    if pool is None:
        result["premium"] = await is_premium(user_id, guild_id)  # use cache/core path
        return result
    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT tier, expires_at FROM premium_subs
                WHERE user_id = $1 AND guild_id = $2
                  AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                guild_id,
            )
        if row:
            result["premium"] = True
            result["tier"] = row.get("tier")
            result["expires_at"] = row.get("expires_at")
            _set_cache(user_id, guild_id, True)
        else:
            _set_cache(user_id, guild_id, False)
    except Exception as e:
        logger.warning("Premium guard: get_premium_status failed: %s", e)
    return result


async def get_active_premium_guild(user_id: int) -> int | None:
    """
    Return the guild_id where this user has an active premium subscription (local DB only).

    Used for transfer: "do you have premium somewhere?" When Core-API is source of truth,
    there may be no local row; transfer then is not available from the bot (use dashboard).
    """
    pool = await _ensure_pool()
    if pool is None:
        return None
    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT guild_id FROM premium_subs
                WHERE user_id = $1 AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
            )
        return int(row["guild_id"]) if row else None
    except Exception as e:
        logger.warning("Premium guard: get_active_premium_guild failed: %s", e)
        return None


async def guild_has_premium(guild_id: int) -> bool:
    """
    Return True if the guild has at least one active premium subscription assigned to it.

    Used to gate guild-level premium features (e.g. verification): only servers with
    premium can use the feature. Local DB only; no Core-API check.
    """
    if guild_id is None or guild_id == 0:
        return False

    global _stats_guild_cache_hits, _stats_guild_cache_misses
    cached = _get_cached_guild(guild_id)
    if cached is not None:
        with _cache_lock:
            _stats_guild_cache_hits += 1
        return cached
    with _cache_lock:
        _stats_guild_cache_misses += 1

    pool = await _ensure_pool()
    if pool is None:
        _set_guild_cache(guild_id, False)
        return False
    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM premium_subs
                WHERE guild_id = $1 AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                guild_id,
            )
        result = row is not None
        _set_guild_cache(guild_id, result)
        return result
    except Exception as e:
        logger.warning("Premium guard: guild_has_premium failed: %s", e)
        _set_guild_cache(guild_id, False)
        return False


async def transfer_premium_to_guild(user_id: int, new_guild_id: int) -> tuple[bool, str]:
    """
    Move the user's active premium subscription to the given guild (local DB only).

    Enforces one active subscription per user: the single active row's guild_id is updated.
    Cache for this user is cleared so is_premium reflects the new guild immediately.
    Returns (True, "transferred") or (False, reason).
    """
    pool = await _ensure_pool()
    if pool is None:
        return False, "database unavailable"
    try:
        async with acquire_safe(pool) as conn:
            old_row = await conn.fetchrow(
                """
                SELECT guild_id FROM premium_subs
                WHERE user_id = $1 AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                user_id,
            )
            row = await conn.fetchrow(
                """
                UPDATE premium_subs
                SET guild_id = $2
                WHERE user_id = $1 AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > NOW())
                RETURNING id
                """,
                user_id,
                new_guild_id,
            )
        if not row:
            return False, "no active subscription"
        _clear_cache_for_user(user_id)
        global _stats_transfers
        _stats_transfers += 1
        from_guild = int(old_row["guild_id"]) if old_row else None
        logger.info(
            "Premium transfer: user_id=%s from_guild=%s to_guild=%s",
            user_id, from_guild, new_guild_id,
        )
        return True, "transferred"
    except Exception as e:
        logger.warning("Premium guard: transfer_premium_to_guild failed: %s", e)
        return False, "transfer failed"


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

async def get_user_tier(user_id: int, guild_id: int) -> str:
    """
    Return the user's current tier: 'free', 'monthly', 'yearly', or 'lifetime'.

    Falls back to 'free' on any error or when no active subscription is found.
    """
    status = await get_premium_status(user_id, guild_id)
    if not status.get("premium"):
        return "free"
    tier = status.get("tier") or "monthly"
    from utils.premium_tiers import TIER_RANK
    return tier if tier in TIER_RANK else "monthly"


async def user_has_tier(user_id: int, guild_id: int, min_tier: str) -> bool:
    """Return True if the user's tier rank is >= min_tier's rank."""
    from utils.premium_tiers import TIER_RANK
    tier = await get_user_tier(user_id, guild_id)
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(min_tier, 0)


# ---------------------------------------------------------------------------
# GPT daily quota
# ---------------------------------------------------------------------------

async def check_and_increment_gpt_quota(
    user_id: int, guild_id: int
) -> tuple[bool, int, int | None]:
    """
    Check whether the user is within their daily GPT call quota and increment if so.

    Returns (allowed, current_count, limit).
    - allowed=True  → call is permitted; count has been incremented.
    - allowed=False → quota exceeded; count is the current value; limit is the cap.
    - limit=None    → unlimited tier (always allowed).

    Fails open on DB error: returns (True, 0, limit) so a DB outage never blocks users.
    """
    from utils.premium_tiers import GPT_DAILY_LIMIT
    tier = await get_user_tier(user_id, guild_id)
    limit = GPT_DAILY_LIMIT.get(tier)

    if limit is None:
        return True, 0, None  # Unlimited tier — skip DB entirely

    pool = await _ensure_pool()
    if pool is None:
        logger.warning("GPT quota: DB pool unavailable — failing open for user %s", user_id)
        return True, 0, limit

    try:
        async with acquire_safe(pool) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gpt_usage (user_id, guild_id, usage_date, call_count)
                VALUES ($1, $2, CURRENT_DATE, 1)
                ON CONFLICT (user_id, guild_id, usage_date)
                DO UPDATE SET call_count = gpt_usage.call_count + 1
                RETURNING call_count
                """,
                user_id,
                guild_id,
            )
            count = row["call_count"] if row else 1
        if count > limit:
            # Decrement back — we over-incremented past the cap
            async with acquire_safe(pool) as conn:
                await conn.execute(
                    """
                    UPDATE gpt_usage SET call_count = call_count - 1
                    WHERE user_id = $1 AND guild_id = $2 AND usage_date = CURRENT_DATE
                    """,
                    user_id,
                    guild_id,
                )
            return False, count - 1, limit
        return True, count, limit
    except Exception as e:
        logger.warning("GPT quota: DB check failed for user %s — failing open: %s", user_id, e)
        return True, 0, limit
