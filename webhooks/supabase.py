import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

import config
from utils.dashboard_webhooks import forward_supabase_auth
from utils.db_helpers import acquire_safe
from utils.supabase_client import SupabaseConfigurationError, upsert_profile
from webhooks.common import validate_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/supabase", tags=["supabase"])


def _extract_user_id(payload: dict[str, Any]) -> str | None:
    record = payload.get("record") or payload.get("user") or {}
    return record.get("id")


def _extract_discord_id(payload: dict[str, Any]) -> int | None:
    """Extract the Discord user ID (BIGINT) from a Supabase auth payload."""
    record = payload.get("record") or payload.get("user") or {}
    raw_meta = record.get("raw_user_meta_data") or {}
    if raw_meta.get("provider") == "discord":
        provider_id = raw_meta.get("provider_id")
        if provider_id:
            try:
                return int(provider_id)
            except (ValueError, TypeError):
                pass
    return None


async def _purge_railway_data(pool, discord_id: int, supabase_user_id: str) -> None:
    """Delete all personal data for a user from Railway PostgreSQL (GDPR erasure)."""
    tables_to_delete = [
        ("alphapy_discord_links", "discord_user_id"),
        ("onboarding", "user_id"),
        ("support_tickets", "user_id"),
        # faq_search_logs excluded: the table has no user_id column (queries are stored anonymously)
        ("audit_logs", "user_id"),
        ("terms_acceptance", "user_id"),
        ("gdpr_acceptance", "user_id"),      # GDPR button acceptance record
        ("gpt_usage", "user_id"),             # daily GPT quota counters
        ("automod_logs", "user_id"),
        ("automod_user_history", "user_id"),
        ("app_reflections", "user_id"),
    ]
    tables_to_anonymize = [
        ("reminders", "created_by"),
        ("custom_commands", "created_by"),
    ]
    # NOTE: `premium_subs` is intentionally excluded from GDPR erasure.
    # Belgian tax law (Wetboek van inkomstenbelastingen / Belgian Income Tax Code)
    # requires retention of financial records for 7 years. Subscription tier, status,
    # and transaction identifiers qualify as such records. See docs/privacy-policy.md §6.

    async with acquire_safe(pool) as conn:
        async with conn.transaction():
            # Erase ticket_summaries before support_tickets is deleted — the subquery
            # join would find no rows if the parent tickets are already gone.
            result = await conn.execute(
                """
                DELETE FROM ticket_summaries
                WHERE ticket_id IN (
                    SELECT id FROM support_tickets WHERE user_id = $1
                )
                """,  # noqa: S608
                discord_id,
            )
            logger.info(
                "GDPR purge: %s from ticket_summaries (discord_id=%s)", result, discord_id
            )

            for table, col in tables_to_delete:
                result = await conn.execute(
                    f"DELETE FROM {table} WHERE {col} = $1", discord_id  # noqa: S608
                )
                logger.info(
                    "GDPR purge: %s from %s (discord_id=%s)", result, table, discord_id
                )
            for table, col in tables_to_anonymize:
                result = await conn.execute(
                    f"UPDATE {table} SET {col} = NULL WHERE {col} = $1",  # noqa: S608
                    discord_id,
                )
                logger.info(
                    "GDPR anonymize: %s in %s (discord_id=%s)", result, table, discord_id
                )

    logger.info(
        "GDPR erasure complete: supabase_user_id=%s discord_id=%s",
        supabase_user_id,
        discord_id,
    )


@router.post("/auth")
async def supabase_auth_webhook(request: Request) -> dict[str, str]:
    """Handle Supabase Auth webhooks for user lifecycle events."""
    body = await request.body()
    signature = (
        request.headers.get("supabase-signature")
        or request.headers.get("x-supabase-signature")
        or request.headers.get("x-signature")
    )
    validate_webhook_signature(
        body,
        signature,
        getattr(config, "SUPABASE_WEBHOOK_SECRET", None),
        missing_detail="Missing Supabase signature header.",
        invalid_detail="Invalid Supabase signature.",
    )

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    event_type = (
        str(
            payload.get("type")
            or payload.get("eventType")
            or payload.get("event_type")
            or ""
        ).upper()
    )
    user_id = _extract_user_id(payload)

    logger.info(
        "Supabase auth webhook received: type=%s, user_id=%s",
        event_type or "UNKNOWN",
        user_id,
    )

    if event_type in {"USER_CREATED", "USER_SIGNED_UP", "USER_UPDATED"} and user_id:
        profile_payload: dict[str, Any] = {"user_id": user_id}
        record = payload.get("record") or payload.get("user") or {}
        raw_meta = record.get("raw_user_meta_data") or {}

        nickname = raw_meta.get("full_name") or raw_meta.get("user_name")
        discord_id = (
            raw_meta.get("provider_id")
            if raw_meta.get("provider") == "discord"
            else None
        )

        if nickname:
            profile_payload["nickname"] = nickname
        if discord_id:
            profile_payload["discord_id"] = str(discord_id)

        try:
            await upsert_profile(profile_payload)
        except SupabaseConfigurationError:
            logger.debug(
                "Supabase service role key not configured; skipping profile sync."
            )
        except Exception as exc:  # pragma: no cover - network path
            logger.warning(
                "Failed to upsert profile for user_id=%s: %s", user_id, exc
            )

    if event_type in {"USER_DELETED", "USER_DESTROYED"} and user_id:
        try:
            from agents.memory import purge_agent_user_data

            await purge_agent_user_data(innersync_user_id=user_id)
        except Exception as exc:
            logger.error(
                "GDPR agent purge failed for supabase_user_id=%s: %s", user_id, exc
            )

        discord_id = _extract_discord_id(payload)
        if discord_id is None:
            logger.warning(
                "GDPR erasure requested for supabase_user_id=%s but no Discord ID found "
                "in payload (non-Discord provider or missing metadata). "
                "Railway data was not purged.",
                user_id,
            )
        else:
            pool = getattr(request.app.state, "db_pool", None)
            if pool is None:
                logger.error(
                    "GDPR erasure for discord_id=%s failed: Railway DB pool not available.",
                    discord_id,
                )
            else:
                try:
                    await _purge_railway_data(pool, discord_id, user_id)
                except Exception as exc:
                    logger.error(
                        "GDPR erasure failed for discord_id=%s: %s", discord_id, exc
                    )

    forward_supabase_auth(payload)
    return {"status": "acknowledged"}
