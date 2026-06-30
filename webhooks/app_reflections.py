"""
Webhook handler for plaintext reflections from App via Core-API.

Stores reflection content in app_reflections for use in user-self flows (e.g.
/growthcheckin only; not used for ticket "Suggest reply" for privacy).
Consent is validated by Core before the webhook is sent.
"""

import json
import logging

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status

from utils.dashboard_webhooks import forward_reflection
from webhooks.common import get_app_reflections_secret, validate_webhook_signature
from webhooks.reflection_payload import (
    ReflectionWebhookPayloadError,
    extract_plaintext_content,
    resolve_discord_user_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/app-reflections", tags=["app-reflections"])


@router.post("")
async def handle_app_reflection_webhook(request: Request) -> dict[str, str]:
    """
    Handle plaintext reflection payload from Core-API.

    Expected payload:
    {
        "user_id": 123456789,  // Discord user ID
        "reflection_id": "uuid",
        "plaintext_content": { ... }  // JSON object with reflection fields
    }
    """
    body = await request.body()
    signature = (
        request.headers.get("X-Webhook-Signature")
        or request.headers.get("x-webhook-signature")
    )
    try:
        validate_webhook_signature(
            body, signature, get_app_reflections_secret(), log_name="app-reflections"
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

    if reflection_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required field: reflection_id.",
        )

    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if not pool or pool.is_closing():
        logger.error("Database pool not available for app-reflections webhook")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable.",
        )

    try:
        discord_user_id = await resolve_discord_user_id(pool, user_id)
        plaintext_content = extract_plaintext_content(payload)
    except ReflectionWebhookPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_reflections (user_id, reflection_id, plaintext_content)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (user_id, reflection_id) DO UPDATE SET
                    plaintext_content = EXCLUDED.plaintext_content,
                    created_at = NOW()
                """,
                discord_user_id,
                reflection_id,
                json.dumps(plaintext_content),
            )
    except Exception as e:
        logger.exception("Failed to upsert app_reflections: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store reflection.",
        ) from e

    logger.info(
        "App reflection webhook: user_id=%s, reflection_id=%s",
        discord_user_id,
        reflection_id,
    )
    forward_reflection(
        {
            "user_id": discord_user_id,
            "reflection_id": reflection_id,
            "plaintext_content": plaintext_content,
        }
    )
    return {"status": "acknowledged", "reflection_id": reflection_id}


__all__ = ["router"]
