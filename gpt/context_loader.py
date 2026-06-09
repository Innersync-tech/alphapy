"""
Context loader for Grok personalization using App reflections.

Loads recent reflections from:
- Supabase reflections_shared (for users who opted in via bot_sharing_enabled)
- app_reflections (plaintext from App via Core webhook, last 30 days)
"""

from __future__ import annotations

import json
import logging

import asyncpg

from utils.db_helpers import PoolT
from utils.sanitizer import safe_prompt
from utils.innersync_identity import get_innersync_id_for_discord
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


async def _load_app_reflections(discord_id: int | str, limit: int = 5) -> tuple[str, int]:
    """
    Load recent reflections from app_reflections (plaintext from App via Core webhook).
    Returns tuple of (formatted context string, actual count of valid reflections).
    """
    try:
        pool = await _get_app_reflections_pool()
        if not pool:
            return "", 0

        discord_id_int = int(discord_id)
        from utils.db_helpers import acquire_safe

        async with acquire_safe(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT plaintext_content, created_at
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
            # JSONB may be returned as str by asyncpg when no custom codec is used
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (ValueError, TypeError):
                    continue
            if not isinstance(content, dict):
                continue
            display_idx += 1
            blocks.append(f"Reflection {display_idx} ({date_str}):")
            for key in ("reflection_text", "reflection", "mantra", "thoughts", "future_message"):
                val = content.get(key)
                safe_val = _sanitize_reflection_field(val)
                if safe_val:
                    label = key.replace("_", " ").title()
                    blocks.append(f"  {label}: {safe_val}")
            date_val = content.get("date")
            safe_date = _sanitize_reflection_field(date_val, max_chars=_REFLECTION_DATE_MAX_CHARS)
            if safe_date:
                blocks.append(f"  Date: {safe_date}")
            blocks.append("")
        if not blocks:
            return "", 0
        return (
            "Recent reflections from the App (shared via webhook):\n\n"
            + "\n".join(blocks).strip(),
            display_idx,
        )
    except Exception as e:
        logger.debug("Failed to load app_reflections for discord_id=%s: %s", discord_id, e)
        return "", 0


async def load_user_reflections(
    discord_id: int | str,
    limit: int = 5,
) -> str:
    """
    Load recent reflections for a Discord user to use as Grok context.
    
    Args:
        discord_id: Discord user ID
        limit: Maximum total number of reflections to load across all sources
               (default: 5)
    
    Returns:
        Formatted context string with reflections, or empty string if:
        - User hasn't opted in (bot_sharing_enabled = false)
        - No reflections found
        - Error occurred (logged but not raised)
    """
    context_str = ""
    loaded_count = 0
    if limit <= 0:
        return ""
    try:
        # Supabase: get user_id and check bot_sharing_enabled
        user_id = await _resolve_innersync_user_id(discord_id)
        if not user_id:
            logger.debug(
                "No Supabase profile linked to discord_id=%s - skipping Supabase reflection context",
                discord_id,
            )
        else:
            profile_rows = await _supabase_get(
                "profiles",
                {
                    "select": "bot_sharing_enabled",
                    "user_id": f"eq.{user_id}",
                    "limit": 1,
                },
            )
            if not profile_rows:
                logger.debug("No profile found for user_id=%s", user_id)
            else:
                bot_sharing_enabled = profile_rows[0].get("bot_sharing_enabled", False)
                if not bot_sharing_enabled:
                    logger.debug(
                        "User %s (discord_id=%s) has not opted in to reflection sharing",
                        user_id,
                        discord_id,
                    )
                else:
                    reflection_rows = await _supabase_get(
                        "reflections_shared",
                        {
                            "select": "reflection_text,mantra,thoughts,future_message,date",
                            "user_id": f"eq.{user_id}",
                            "order": "date.desc",
                            "limit": limit,
                        },
                    )
                    if reflection_rows:
                        context_parts = ["Recent reflections from the user:", ""]
                        valid_count = 0
                        for _idx, reflection in enumerate(reflection_rows, 1):
                            date_str = _sanitize_reflection_field(
                                reflection.get("date", ""),
                                max_chars=_REFLECTION_DATE_MAX_CHARS,
                            )
                            reflection_text = _sanitize_reflection_field(reflection.get("reflection_text", ""))
                            mantra = _sanitize_reflection_field(reflection.get("mantra"))
                            thoughts = _sanitize_reflection_field(reflection.get("thoughts"))
                            future_message = _sanitize_reflection_field(reflection.get("future_message"))
                            
                            # Only count reflections that have actual content
                            has_content = bool(reflection_text or mantra or thoughts or future_message)
                            if has_content:
                                valid_count += 1
                                context_parts.append(f"Reflection {valid_count} ({date_str}):")
                                if reflection_text:
                                    context_parts.append(f"  Reflection: {reflection_text}")
                                if mantra:
                                    context_parts.append(f"  Mantra: {mantra}")
                                if thoughts:
                                    context_parts.append(f"  Thoughts: {thoughts}")
                                if future_message:
                                    context_parts.append(f"  Future message: {future_message}")
                                context_parts.append("")
                        
                        if valid_count > 0:
                            context_str = "\n".join(context_parts)
                            loaded_count = valid_count  # Use actual valid count, not raw row count
                            logger.debug(
                                "Loaded %s valid Supabase reflections for user_id=%s (discord_id=%s) from %s raw rows",
                                valid_count,
                                user_id,
                                discord_id,
                                len(reflection_rows),
                            )
                        else:
                            logger.debug(
                                "No valid Supabase reflections found for user_id=%s (discord_id=%s) from %s raw rows",
                                user_id,
                                discord_id,
                                len(reflection_rows),
                            )
                    else:
                        logger.debug("No shared reflections found for user_id=%s", user_id)
    except Exception as e:
        logger.warning(
            "Failed to load Supabase reflection context for discord_id=%s: %s",
            discord_id,
            e,
            exc_info=True,
        )

    # Load Discord check-ins from the `reflections` table (written by /growthcheckin).
    # These are the user's own bot submissions — no bot_sharing_enabled gate needed.
    if loaded_count < limit:
        try:
            if not user_id:
                user_id = await _resolve_innersync_user_id(discord_id)
            if user_id:
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
                        context_str = f"{context_str}\n\n{dr_context}".strip() if context_str else dr_context
                        loaded_count += dr_count
                        logger.debug(
                            "Loaded %s Discord check-ins from reflections for discord_id=%s",
                            dr_count,
                            discord_id,
                        )
        except Exception as e:
            logger.debug("Failed to load Discord check-ins for discord_id=%s: %s", discord_id, e)

    # Always try app_reflections (plaintext from App via Core webhook), but
    # enforce the global limit across both sources. loaded_count is the number
    # of reflections that actually produced context (valid_count from Supabase,
    # app_count from app_reflections), not raw row counts, so remaining_limit
    # correctly leaves room for app reflections when some rows were empty/invalid.
    remaining_limit = max(limit - loaded_count, 0)
    if remaining_limit > 0:
        app_context, app_count = await _load_app_reflections(discord_id, limit=remaining_limit)
        if app_context:
            context_str = f"{context_str}\n\n{app_context}".strip() if context_str else app_context
            loaded_count += app_count  # Add actual count from app reflections
            logger.debug(
                "Loaded %s app reflections for discord_id=%s, total now: %s",
                app_count,
                discord_id,
                loaded_count,
            )
    else:
        logger.debug(
            "Skipping app_reflections for discord_id=%s because limit=%s is already reached (loaded: %s)",
            discord_id,
            limit,
            loaded_count,
        )

    return context_str or ""


__all__ = ["load_user_reflections"]
