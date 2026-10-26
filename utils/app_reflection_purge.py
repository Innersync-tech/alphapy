"""Hard-delete Railway copies of App-shared reflections and derived Alphapy data.

Never SELECTs ``plaintext_content``. Journal text lives only in
``app_reflections.plaintext_content`` on Railway; there are no embeddings or
Discord message copies of the share payload.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

from utils.innersync_identity import get_innersync_id_for_discord
from utils.supabase_client import (
    SupabaseConfigurationError,
    _supabase_get,
    _supabase_patch,
)

logger = logging.getLogger(__name__)


def coerce_purge_patterns(value: Any) -> bool:
    """Parse ``purge_patterns`` from API/webhook bodies. Default is false."""
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    return False


async def has_active_patterns_consent(
    innersync_user_id: str,
    reflection_id: str,
) -> bool | None:
    """Whether ``reflection_patterns_consent`` has a row for this journal.

    Returns ``True`` / ``False`` when the lookup succeeds. Returns ``None``
    when Supabase is unconfigured or the request fails so callers can
    fail-open for Patterns (do not strip insights we cannot verify).
    """
    uid = str(innersync_user_id or "").strip()
    rid = str(reflection_id or "").strip()
    if not uid or not rid:
        return None
    try:
        rows = await _supabase_get(
            "reflection_patterns_consent",
            {
                "select": "reflection_id",
                "user_id": f"eq.{uid}",
                "reflection_id": f"eq.{rid}",
                "limit": 1,
            },
        )
    except SupabaseConfigurationError:
        logger.warning(
            "Patterns consent lookup skipped: Supabase not configured "
            "(user=%s reflection=%s)",
            uid,
            rid,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Patterns consent lookup failed user=%s reflection=%s: %s",
            uid,
            rid,
            exc,
        )
        return None
    return bool(rows)


async def revoke_alphapy_share_consent_for_user(innersync_user_id: str) -> int:
    """Soft-revoke every active ``reflection_alphapy_consent`` row for this user.

    Sets ``revoked_at`` (Core revoke-share contract). Raises on write failure
    so unlink can fail-closed. ``SupabaseConfigurationError`` is not swallowed.
    """
    uid = str(innersync_user_id or "").strip()
    if not uid:
        return 0
    now = datetime.now(UTC).isoformat()
    try:
        rows = await _supabase_patch(
            "reflection_alphapy_consent",
            {"revoked_at": now},
            {
                "user_id": f"eq.{uid}",
                "revoked_at": "is.null",
            },
        )
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "")[:300]
        logger.error(
            "Alphapy consent revoke failed user=%s status=%s body=%s",
            uid,
            exc.response.status_code,
            body,
        )
        raise
    return len(rows)


async def count_app_reflection_rows(
    conn: asyncpg.Connection,
    reflection_id: str,
) -> int:
    """``SELECT count(*)`` for ``reflection_id``. Never reads ``plaintext_content``."""
    rid = str(reflection_id).strip()
    if not rid:
        return 0
    value = await conn.fetchval(
        """
        SELECT count(*) FROM app_reflections
        WHERE reflection_id = $1
        """,
        rid,
    )
    return int(value or 0)


async def delete_app_reflection_rows(
    pool: asyncpg.Pool,
    *,
    reflection_id: str,
) -> tuple[int, list[int], int]:
    """DELETE Railway rows for ``reflection_id``.

    Returns ``(deleted, discord user ids, remaining)``. ``remaining`` is a
    post-delete ``SELECT count(*)`` for the same ``reflection_id`` so callers
    can prove the copy is gone (a sent DELETE is not enough).

    Scoped by reflection id only. The App journal id is a UUID; requiring
    ``user_id`` as well left orphans when Discord snowflakes drifted or the
    caller could not resolve a link.
    """
    rid = str(reflection_id).strip()
    if not rid:
        return 0, [], 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            DELETE FROM app_reflections
            WHERE reflection_id = $1
            RETURNING user_id
            """,
            rid,
        )
        remaining = await count_app_reflection_rows(conn, rid)

    user_ids: list[int] = []
    for row in rows:
        try:
            user_ids.append(int(row["user_id"]))
        except (TypeError, ValueError):
            continue
    return len(rows), user_ids, remaining


async def delete_app_reflection_rows_for_user(
    pool: asyncpg.Pool,
    *,
    discord_user_id: int,
) -> tuple[int, list[str]]:
    """DELETE every Railway copy for a Discord user. Returns (count, reflection ids)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            DELETE FROM app_reflections
            WHERE user_id = $1
            RETURNING reflection_id
            """,
            int(discord_user_id),
        )
    reflection_ids = [
        str(row["reflection_id"]) for row in rows if row.get("reflection_id")
    ]
    return len(rows), reflection_ids


async def purge_derived_for_reflection(
    pool: asyncpg.Pool,
    *,
    reflection_id: str,
    discord_user_ids: list[int],
    innersync_user_id: str | None = None,
    purge_patterns: bool = False,
) -> int:
    """Drop Tier-2 insight labels linked to this reflection (Supabase ``agent_memory``).

    When ``purge_patterns`` is false (default), skip users who still have an
    active ``reflection_patterns_consent`` row — Alphapy-share revoke / unlink
    must not override Patterns consent. Pass ``purge_patterns=True`` only when
    the journal itself is deleted.
    """
    from agents.memory import purge_tier2_for_reflection

    targets: set[str] = set()
    if innersync_user_id and str(innersync_user_id).strip():
        targets.add(str(innersync_user_id).strip())
    for did in discord_user_ids:
        linked = await get_innersync_id_for_discord(
            pool,
            int(did),
            allow_profile_fallback=False,
        )
        if linked:
            targets.add(str(linked))

    purged = 0
    for uid in targets:
        if not purge_patterns:
            consent = await has_active_patterns_consent(uid, str(reflection_id))
            if consent is not False:
                logger.info(
                    "Skipping Tier-2 purge user=%s reflection=%s "
                    "(patterns_consent=%s purge_patterns=false)",
                    uid,
                    reflection_id,
                    consent,
                )
                continue
        try:
            await purge_tier2_for_reflection(uid, "reflection", str(reflection_id))
            purged += 1
        except Exception as exc:
            logger.warning(
                "Tier 2 purge after revoke failed user=%s reflection=%s: %s",
                uid,
                reflection_id,
                exc,
            )
    return purged


async def purge_app_reflection_copy(
    pool: asyncpg.Pool,
    *,
    reflection_id: str,
    discord_user_id: int | None = None,
    innersync_user_id: str | None = None,
    notify_dashboard: bool = True,
    purge_patterns: bool = False,
) -> dict[str, Any]:
    """Hard-delete the Railway copy and (conditionally) derived labels. Idempotent."""
    deleted, user_ids, remaining = await delete_app_reflection_rows(
        pool,
        reflection_id=reflection_id,
    )
    if discord_user_id is not None and int(discord_user_id) not in user_ids:
        user_ids = [*user_ids, int(discord_user_id)]

    tier2 = await purge_derived_for_reflection(
        pool,
        reflection_id=reflection_id,
        discord_user_ids=user_ids,
        innersync_user_id=innersync_user_id,
        purge_patterns=purge_patterns,
    )

    if notify_dashboard:
        from utils.dashboard_webhooks import forward_revoke_reflection

        forward_user = discord_user_id if discord_user_id is not None else (user_ids[0] if user_ids else None)
        forward_revoke_reflection(
            {"user_id": forward_user, "reflection_id": str(reflection_id)}
        )

    logger.info(
        "Purged app_reflections copy: reflection_id=%s deleted=%s remaining=%s "
        "tier2=%s purge_patterns=%s",
        reflection_id,
        deleted,
        remaining,
        tier2,
        purge_patterns,
    )
    return {
        "deleted": deleted,
        "remaining": remaining,
        "tier2_purged": tier2,
        "user_ids": user_ids,
    }


async def purge_app_reflections_for_user(
    pool: asyncpg.Pool,
    *,
    discord_user_id: int,
    innersync_user_id: str | None = None,
    purge_patterns: bool = False,
    revoke_alphapy_consent: bool = True,
) -> dict[str, Any]:
    """Hard-delete every Railway copy for this Discord user. Awaited, not best-effort.

    Callers that complete unlink must treat any exception as fail-closed.
    Tier-2 Patterns insights are left in place unless ``purge_patterns`` is true.
    """
    deleted, reflection_ids = await delete_app_reflection_rows_for_user(
        pool,
        discord_user_id=discord_user_id,
    )

    iid = (innersync_user_id or "").strip() or None
    if iid is None:
        iid = await get_innersync_id_for_discord(
            pool,
            int(discord_user_id),
            allow_profile_fallback=False,
        )

    tier2 = 0
    for rid in reflection_ids:
        tier2 += await purge_derived_for_reflection(
            pool,
            reflection_id=rid,
            discord_user_ids=[int(discord_user_id)],
            innersync_user_id=iid,
            purge_patterns=purge_patterns,
        )

    consent_revoked = 0
    if revoke_alphapy_consent and iid:
        try:
            consent_revoked = await revoke_alphapy_share_consent_for_user(iid)
        except SupabaseConfigurationError:
            logger.warning(
                "Unlink consent revoke skipped: Supabase not configured user=%s",
                iid,
            )
        except Exception:
            logger.exception(
                "Unlink consent revoke failed user=%s discord=%s",
                iid,
                discord_user_id,
            )
            raise

    logger.info(
        "Purged app_reflections for user discord=%s deleted=%s consent_revoked=%s",
        discord_user_id,
        deleted,
        consent_revoked,
    )
    return {
        "deleted": deleted,
        "reflection_ids": reflection_ids,
        "tier2_purged": tier2,
        "consent_revoked": consent_revoked,
        "innersync_user_id": iid,
    }


__all__ = [
    "coerce_purge_patterns",
    "count_app_reflection_rows",
    "delete_app_reflection_rows",
    "delete_app_reflection_rows_for_user",
    "has_active_patterns_consent",
    "purge_app_reflection_copy",
    "purge_app_reflections_for_user",
    "purge_derived_for_reflection",
    "revoke_alphapy_share_consent_for_user",
]
