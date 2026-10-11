"""Hard-delete Railway copies of App-shared reflections and derived Alphapy data.

Never SELECTs ``plaintext_content``. Journal text lives only in
``app_reflections.plaintext_content`` on Railway; there are no embeddings or
Discord message copies of the share payload.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from utils.innersync_identity import get_innersync_id_for_discord

logger = logging.getLogger(__name__)


async def delete_app_reflection_rows(
    pool: asyncpg.Pool,
    *,
    reflection_id: str,
) -> tuple[int, list[int]]:
    """DELETE Railway rows for ``reflection_id``. Returns (count, discord user ids).

    Scoped by reflection id only. The App journal id is a UUID; requiring
    ``user_id`` as well left orphans when Discord snowflakes drifted or the
    caller could not resolve a link.
    """
    rid = str(reflection_id).strip()
    if not rid:
        return 0, []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            DELETE FROM app_reflections
            WHERE reflection_id = $1
            RETURNING user_id
            """,
            rid,
        )

    user_ids: list[int] = []
    for row in rows:
        try:
            user_ids.append(int(row["user_id"]))
        except (TypeError, ValueError):
            continue
    return len(rows), user_ids


async def purge_derived_for_reflection(
    pool: asyncpg.Pool,
    *,
    reflection_id: str,
    discord_user_ids: list[int],
    innersync_user_id: str | None = None,
) -> int:
    """Drop Tier-2 insight labels linked to this reflection (Supabase ``agent_memory``)."""
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
) -> dict[str, Any]:
    """Hard-delete the Railway copy and derived labels. Idempotent."""
    deleted, user_ids = await delete_app_reflection_rows(
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
    )

    if notify_dashboard:
        from utils.dashboard_webhooks import forward_revoke_reflection

        forward_user = discord_user_id if discord_user_id is not None else (user_ids[0] if user_ids else None)
        forward_revoke_reflection(
            {"user_id": forward_user, "reflection_id": str(reflection_id)}
        )

    logger.info(
        "Purged app_reflections copy: reflection_id=%s deleted=%s tier2=%s",
        reflection_id,
        deleted,
        tier2,
    )
    return {
        "deleted": deleted,
        "tier2_purged": tier2,
        "user_ids": user_ids,
    }


__all__ = [
    "delete_app_reflection_rows",
    "purge_app_reflection_copy",
    "purge_derived_for_reflection",
]
