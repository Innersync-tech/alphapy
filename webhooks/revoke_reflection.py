"""
Webhook handler for reflection revoke from Core-API.

Hard-deletes the Railway copy when the user revokes consent or deletes the
journal in the App. ``user_id`` is optional: a missing or unlinkable identity
must not leave plaintext behind.
"""

import json
import logging

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status

from utils.app_reflection_purge import coerce_purge_patterns, purge_app_reflection_copy
from webhooks.common import get_app_reflections_secret, validate_webhook_signature
from webhooks.reflection_payload import ReflectionWebhookPayloadError, resolve_discord_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/revoke-reflection", tags=["revoke-reflection"])


@router.post("")
async def handle_revoke_reflection_webhook(request: Request) -> dict:
    """
    Handle revoke reflection from Core-API.

    Expected payload:
    {
        "user_id": 123456789,  // Discord snowflake or Innersync UUID (optional)
        "reflection_id": "uuid",
        "purge_patterns": false  // true only when the journal itself is deleted
    }
    """
    body = await request.body()
    signature = (
        request.headers.get("X-Webhook-Signature")
        or request.headers.get("x-webhook-signature")
    )
    try:
        validate_webhook_signature(
            body, signature, get_app_reflections_secret(), log_name="revoke-reflection"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Unexpected signature validation error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        ) from e

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    user_id = payload.get("user_id")
    reflection_id = payload.get("reflection_id")

    if reflection_id is None or not str(reflection_id).strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required field: reflection_id.",
        )

    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if not pool or pool.is_closing():
        logger.error("Database pool not available for revoke-reflection webhook")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable.",
        )

    discord_user_id: int | None = None
    innersync_user_id: str | None = None
    if user_id is not None and str(user_id).strip():
        try:
            discord_user_id = await resolve_discord_user_id(pool, user_id)
        except ReflectionWebhookPayloadError as exc:
            raw = str(user_id).strip()
            if "-" in raw:
                innersync_user_id = raw
            logger.warning(
                "Revoke reflection webhook: unresolved user_id=%s (%s); deleting by reflection_id",
                raw,
                exc,
            )

    try:
        result = await purge_app_reflection_copy(
            pool,
            reflection_id=str(reflection_id).strip(),
            discord_user_id=discord_user_id,
            innersync_user_id=innersync_user_id,
            purge_patterns=coerce_purge_patterns(payload.get("purge_patterns")),
        )
    except Exception as e:
        logger.exception("Failed to delete from app_reflections: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke reflection.",
        ) from e

    return {"status": "deleted", "count": int(result["deleted"])}


__all__ = ["router"]
