"""Shared Railway GDPR erasure for Discord-scoped personal data.

Used by ``/delete_my_data`` and the Supabase ``USER_DELETED`` webhook so the
two paths cannot drift. Never SELECTs journal or reminder body text.
"""

from __future__ import annotations

import asyncpg

from utils.db_helpers import acquire_safe
from utils.logger import logger

# premium_subs is excluded — Belgian tax law requires 7-year retention of
# financial records (Wetboek van inkomstenbelastingen). See privacy-policy.md §6.
# faq_search_logs excluded: the table has no user_id column.

_TABLES_TO_DELETE: tuple[tuple[str, str], ...] = (
    ("alphapy_discord_links", "discord_user_id"),
    ("onboarding", "user_id"),
    ("support_tickets", "user_id"),
    ("audit_logs", "user_id"),
    ("terms_acceptance", "user_id"),
    ("gdpr_acceptance", "user_id"),
    ("gpt_usage", "user_id"),
    ("agent_session_usage", "user_id"),
    ("automod_logs", "user_id"),
    ("automod_user_history", "user_id"),
    ("app_reflections", "user_id"),
    ("growth_checkins", "user_id"),
    ("agent_nudge_state", "discord_user_id"),
)


async def purge_railway_user_data(
    pool: asyncpg.Pool,
    user_id: int,
    *,
    log_prefix: str = "GDPR purge",
) -> None:
    """Delete or anonymize Railway rows for ``user_id`` (Discord snowflake).

    Reminders stay as guild schedule rows but ``message`` is cleared (P11).
    Custom-command ``response`` text is guild operational config and is left
    intact; only ``created_by`` is nulled.
    """
    async with acquire_safe(pool) as conn:
        async with conn.transaction():
            result = await conn.execute(
                """
                DELETE FROM ticket_summaries
                WHERE ticket_id IN (
                    SELECT id FROM support_tickets WHERE user_id = $1
                )
                """,  # noqa: S608
                user_id,
            )
            logger.info("%s: %s from ticket_summaries (user_id=%s)", log_prefix, result, user_id)

            for table, col in _TABLES_TO_DELETE:
                result = await conn.execute(
                    f"DELETE FROM {table} WHERE {col} = $1",  # noqa: S608
                    user_id,
                )
                logger.info("%s: %s from %s (user_id=%s)", log_prefix, result, table, user_id)

            result = await conn.execute(
                """
                UPDATE reminders
                SET created_by = NULL, message = NULL
                WHERE created_by = $1
                """,
                user_id,
            )
            logger.info("%s: anonymized reminders (user_id=%s) %s", log_prefix, user_id, result)

            result = await conn.execute(
                """
                UPDATE custom_commands
                SET created_by = NULL
                WHERE created_by = $1
                """,
                user_id,
            )
            logger.info(
                "%s: anonymized custom_commands (user_id=%s) %s",
                log_prefix,
                user_id,
                result,
            )


__all__ = ["purge_railway_user_data"]
