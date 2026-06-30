"""
Context loader for Grok personalization using App reflections.

Loads recent reflections from:
- Supabase reflection_alphapy_consent (per-reflection opt-in; canonical for agents)
- app_reflections (plaintext from App via Core webhook; consent-gated)
- reflections_shared (legacy plaintext copies; consent-gated only)
- Supabase reflections (Discord /growthcheckin submissions; user-self flows only)
"""

from __future__ import annotations

import json
import logging

import asyncpg

from utils.db_helpers import PoolT
from utils.innersync_identity import get_innersync_id_for_discord
from utils.sanitizer import safe_prompt
from utils.supabase_client import _supabase_get

logger = logging.getLogger(__name__)

_REFLECTION_TEXT_MAX_CHARS = 2048
_REFLECTION_DATE_MAX_CHARS = 128

_app_reflections_pool: PoolT | None = None


def _sanitize_reflection_field(value: object, max_chars: int = _REFLECTION_TEXT_MAX_CHARS) -> str:
    """Normalize reflection content before injecting into LLM context."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return safe_prompt(text[:max_chars])


async def _get_app_reflections_pool() -> PoolT | None:
    """Get or create a shared database pool for app_reflections.

    This is intentionally cached at module level so that every user-self flow
    (e.g. /growthcheckin) does *not* pay the cost of creating and tearing down
    a brand-new asyncpg pool for a single query.
    """
    global _app_reflections_pool

    # Reuse existing pool when available
    if _app_reflections_pool is not None and not _app_reflections_pool._closed:
        return _app_reflections_pool

    try:
        import config

        if not getattr(config, "DATABASE_URL", None):
            logger.debug("No DATABASE_URL configured for app_reflections")
            return None

        # Create pool with same settings as api.py (but smaller max_size)
        _app_reflections_pool = await asyncpg.create_pool(
            config.DATABASE_URL,
            min_size=1,
            max_size=5,  # Smaller pool for occasional context loading
            command_timeout=10.0,
        )
        return _app_reflections_pool
    except Exception as e:
        logger.debug("Failed to create app_reflections pool: %s", e)
        _app_reflections_pool = None
        return None


async def _resolve_innersync_user_id(discord_id: int | str) -> str | None:
    pool = await _get_app_reflections_pool()
    return await get_innersync_id_for_discord(
        pool,
        int(discord_id),
        allow_profile_fallback=False,
    )


async def _fetch_active_consent_reflection_ids(innersync_user_id: str) -> frozenset[str]:
    """Reflection IDs with active (non-revoked) Alphapy share consent."""
    try:
        rows = await _supabase_get(
            "reflection_alphapy_consent",
            {
                "select": "reflection_id",
                "user_id": f"eq.{innersync_user_id}",
                "revoked_at": "is.null",
                "limit": 500,
            },
        )
    except Exception as exc:
        logger.warning(
            "Failed to load reflection_alphapy_consent for user_id=%s: %s",
            innersync_user_id,
            exc,
        )
        return frozenset()
    ids = {str(row["reflection_id"]) for row in rows if row.get("reflection_id")}
    return frozenset(ids)


def _format_reflection_block(
    *,
    index: int,
    date_str: str,
    reflection_text: str = "",
    mantra: str = "",
    thoughts: str = "",
    future_message: str = "",
    label: str = "Reflection",
) -> tuple[str, bool]:
    """Format one reflection block; returns (text, has_content)."""
    has_content = bool(reflection_text or mantra or thoughts or future_message)
    if not has_content:
        return "", False
    parts = [f"{label} {index} ({date_str}):"]
    if reflection_text:
        parts.append(f"  Reflection: {reflection_text}")
    if mantra:
        parts.append(f"  Mantra: {mantra}")
    if thoughts:
        parts.append(f"  Thoughts: {thoughts}")
    if future_message:
        parts.append(f"  Future message: {future_message}")
    parts.append("")
    return "\n".join(parts), True


async def _load_app_reflections(
    discord_id: int | str,
    limit: int = 5,
    *,
    allowed_reflection_ids: frozenset[str] | None = None,
) -> tuple[str, int]:
    """
    Load recent reflections from app_reflections (plaintext from App via Core webhook).
    When allowed_reflection_ids is set, only those reflection_id rows are returned.
    Returns tuple of (formatted context string, actual count of valid reflections).
    """
    if allowed_reflection_ids is not None and not allowed_reflection_ids:
        return "", 0
    try:
        pool = await _get_app_reflections_pool()
        if not pool:
            return "", 0

        discord_id_int = int(discord_id)
        from utils.db_helpers import acquire_safe

        if allowed_reflection_ids is not None:
            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT reflection_id, plaintext_content, created_at
                    FROM app_reflections
                    WHERE user_id = $1
                      AND reflection_id = ANY($2::text[])
                      AND created_at >= NOW() - interval '30 days'
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    discord_id_int,
                    list(allowed_reflection_ids),
                    limit,
                )
        else:
            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT reflection_id, plaintext_content, created_at
                    FROM app_reflections
                    WHERE user_id = $1
                      AND created_at >= NOW() - interval '30 days'
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    discord_id_int,
                    limit,
                )
        if not rows:
            return "", 0
        blocks: list[str] = []
        display_idx = 0
        for row in rows:
            content = row["plaintext_content"]
            created = row["created_at"]
            date_str = created.strftime("%Y-%m-%d") if created else ""
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (ValueError, TypeError):
                    continue
            if not isinstance(content, dict):
                continue
            reflection_text = _sanitize_reflection_field(
                content.get("reflection_text") or content.get("reflection", "")
            )
            mantra = _sanitize_reflection_field(content.get("mantra"))
            thoughts = _sanitize_reflection_field(content.get("thoughts"))
            future_message = _sanitize_reflection_field(content.get("future_message"))
            date_val = _sanitize_reflection_field(
                content.get("date"), max_chars=_REFLECTION_DATE_MAX_CHARS
            )
            block, has_content = _format_reflection_block(
                index=display_idx + 1,
                date_str=date_str or date_val,
                reflection_text=reflection_text,
                mantra=mantra,
                thoughts=thoughts,
                future_message=future_message,
            )
            if has_content:
                display_idx += 1
                blocks.append(block)
        if not blocks:
            return "", 0
        return (
            "Recent reflections from the App (explicitly shared with Alphapy):\n\n"
            + "\n".join(blocks).strip(),
            display_idx,
        )
    except Exception as e:
        logger.debug("Failed to load app_reflections for discord_id=%s: %s", discord_id, e)
        return "", 0


async def _load_consented_reflections_shared(
    innersync_user_id: str,
    consent_ids: frozenset[str],
    limit: int,
) -> tuple[str, int]:
    """Load reflections_shared rows that match active per-reflection consent."""
    if not consent_ids or limit <= 0:
        return "", 0
    ids_csv = ",".join(consent_ids)
    try:
        reflection_rows = await _supabase_get(
            "reflections_shared",
            {
                "select": "reflection_id,reflection_text,mantra,thoughts,future_message,date",
                "user_id": f"eq.{innersync_user_id}",
                "reflection_id": f"in.({ids_csv})",
                "order": "date.desc",
                "limit": limit,
            },
        )
    except Exception as exc:
        logger.debug(
            "Failed reflections_shared for user_id=%s: %s",
            innersync_user_id,
            exc,
        )
        return "", 0
    if not reflection_rows:
        return "", 0
    context_parts = ["Recent reflections from the App (explicitly shared with Alphapy):", ""]
    valid_count = 0
    for reflection in reflection_rows:
        date_str = _sanitize_reflection_field(
            reflection.get("date", ""),
            max_chars=_REFLECTION_DATE_MAX_CHARS,
        )
        block, has_content = _format_reflection_block(
            index=valid_count + 1,
            date_str=date_str,
            reflection_text=_sanitize_reflection_field(reflection.get("reflection_text", "")),
            mantra=_sanitize_reflection_field(reflection.get("mantra")),
            thoughts=_sanitize_reflection_field(reflection.get("thoughts")),
            future_message=_sanitize_reflection_field(reflection.get("future_message")),
        )
        if has_content:
            valid_count += 1
            context_parts.append(block)
    if valid_count == 0:
        return "", 0
    return "\n".join(context_parts), valid_count


async def load_agent_reflection_context(
    discord_id: int | str,
    limit: int = 5,
) -> str:
    """
    Load App reflection context for Alphapy agents.

    Only reflections with an active row in reflection_alphapy_consent are included.
    Never reads bulk reflections_shared or app_reflections without matching consent.
    """
    if limit <= 0:
        return ""
    user_id = await _resolve_innersync_user_id(discord_id)
    if not user_id:
        logger.debug(
            "No linked Innersync user for discord_id=%s — agent reflection context empty",
            discord_id,
        )
        return ""
    consent_ids = await _fetch_active_consent_reflection_ids(user_id)
    if not consent_ids:
        logger.debug(
            "No active Alphapy reflection consent for user_id=%s (discord_id=%s)",
            user_id,
            discord_id,
        )
        return ""

    context_str = ""
    loaded_count = 0

    app_context, app_count = await _load_app_reflections(
        discord_id,
        limit=limit,
        allowed_reflection_ids=consent_ids,
    )
    if app_context:
        context_str = app_context
        loaded_count = app_count

    remaining = max(limit - loaded_count, 0)
    if remaining > 0:
        shared_context, shared_count = await _load_consented_reflections_shared(
            user_id,
            consent_ids,
            remaining,
        )
        if shared_context:
            context_str = (
                f"{context_str}\n\n{shared_context}".strip()
                if context_str
                else shared_context
            )
            loaded_count += shared_count

    return context_str or ""


async def load_user_reflections(
    discord_id: int | str,
    limit: int = 5,
) -> str:
    """
    Load recent reflections for a Discord user to use as Grok context (/growthcheckin).

    App journal plaintext requires per-reflection consent (reflection_alphapy_consent).
    Discord /growthcheckin submissions are always included when present.
    """
    if limit <= 0:
        return ""

    context_str = ""
    loaded_count = 0
    user_id = await _resolve_innersync_user_id(discord_id)

    if user_id:
        consent_ids = await _fetch_active_consent_reflection_ids(user_id)
        if consent_ids:
            app_context, app_count = await _load_app_reflections(
                discord_id,
                limit=limit,
                allowed_reflection_ids=consent_ids,
            )
            if app_context:
                context_str = app_context
                loaded_count = app_count

            remaining = max(limit - loaded_count, 0)
            if remaining > 0:
                shared_context, shared_count = await _load_consented_reflections_shared(
                    user_id,
                    consent_ids,
                    remaining,
                )
                if shared_context:
                    context_str = (
                        f"{context_str}\n\n{shared_context}".strip()
                        if context_str
                        else shared_context
                    )
                    loaded_count += shared_count
        else:
            logger.debug(
                "No active Alphapy consent for user_id=%s — skipping App reflection context",
                user_id,
            )

    if loaded_count < limit and user_id:
        try:
            remaining = limit - loaded_count
            discord_reflection_rows = await _supabase_get(
                "reflections",
                {
                    "select": "reflection,mantra,future_message,date",
                    "user_id": f"eq.{user_id}",
                    "order": "date.desc",
                    "limit": remaining,
                },
            )
            if discord_reflection_rows:
                dr_parts = ["Recent Discord check-ins (via /growthcheckin):", ""]
                dr_count = 0
                for row in discord_reflection_rows:
                    date_str = _sanitize_reflection_field(
                        row.get("date", ""), max_chars=_REFLECTION_DATE_MAX_CHARS
                    )
                    reflection_text = _sanitize_reflection_field(row.get("reflection", ""))
                    mantra = _sanitize_reflection_field(row.get("mantra"))
                    future_message = _sanitize_reflection_field(row.get("future_message"))
                    has_content = bool(reflection_text or mantra or future_message)
                    if has_content:
                        dr_count += 1
                        dr_parts.append(f"Check-in {dr_count} ({date_str}):")
                        if reflection_text:
                            dr_parts.append(f"  {reflection_text}")
                        if mantra:
                            dr_parts.append(f"  Mantra: {mantra}")
                        if future_message:
                            dr_parts.append(f"  Future message: {future_message}")
                        dr_parts.append("")
                if dr_count > 0:
                    dr_context = "\n".join(dr_parts).strip()
                    context_str = (
                        f"{context_str}\n\n{dr_context}".strip() if context_str else dr_context
                    )
                    loaded_count += dr_count
        except Exception as e:
            logger.debug("Failed to load Discord check-ins for discord_id=%s: %s", discord_id, e)

    return context_str or ""


__all__ = [
    "load_agent_reflection_context",
    "load_user_reflections",
]
