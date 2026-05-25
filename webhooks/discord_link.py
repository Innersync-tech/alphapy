"""
Webhook: Core confirms a Discord user completed Innersync linking.

Stores the mapping in `alphapy_discord_links` and optionally notifies the user by DM.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from utils.innersync_identity import upsert_discord_link
from utils.logger import logger
from webhooks.common import get_discord_link_webhook_secret, validate_webhook_signature

router = APIRouter(prefix="/webhooks/discord-link", tags=["discord-link"])


async def _try_dm_user(discord_user_id: int, body: str) -> None:
    try:
        from gpt.helpers import bot_instance
    except Exception:
        bot_instance = None  # type: ignore[assignment]
    if bot_instance is None:
        return
    try:
        user = await bot_instance.fetch_user(discord_user_id)
        await user.send(body)
    except Exception as exc:
        logger.debug("discord-link webhook: could not DM user %s: %s", discord_user_id, exc)


@router.post("")
async def handle_discord_link_webhook(request: Request) -> dict[str, str]:
    """
    Confirm link between Innersync user id (UUID) and Discord snowflake.

    Expected JSON:
        {"innersync_user_id": "<uuid>", "discord_user_id": <int>, "link_source": "magic_link"}
    """
    raw = await request.body()
    signature = (
        request.headers.get("X-Webhook-Signature")
        or request.headers.get("x-webhook-signature")
    )
    try:
        validate_webhook_signature(
            raw,
            signature,
            get_discord_link_webhook_secret(),
            log_name="discord-link",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("discord-link signature validation error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        ) from e

    try:
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    iu_raw = payload.get("innersync_user_id")
    du_raw = payload.get("discord_user_id")
    if iu_raw is None or du_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: innersync_user_id, discord_user_id.",
        )

    try:
        uuid.UUID(str(iu_raw))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="innersync_user_id must be a UUID string.",
        ) from exc

    try:
        discord_user_id = int(du_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="discord_user_id must be an integer.",
        ) from exc

    link_source = payload.get("link_source")
    if link_source is not None and not isinstance(link_source, str):
        link_source = str(link_source)

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not available.",
        )

    status_str, err = await upsert_discord_link(
        pool,
        innersync_user_id=str(iu_raw),
        discord_user_id=discord_user_id,
        link_source=link_source,
    )

    if status_str == "conflict":
        logger.info(
            "discord-link webhook conflict: discord=%s innersync=%s detail=%s",
            discord_user_id,
            iu_raw,
            err,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=err or "Link conflict.",
        )

    if status_str == "ok":
        await _try_dm_user(
            discord_user_id,
            "Your Innersync account is now linked to this Discord account. "
            "Use `/profile` to see your central profile.",
        )
        import asyncio
        from utils.hermit_events import emit_hermit_event

        asyncio.create_task(
            emit_hermit_event(
                "link_completed",
                discord_user_id,
                payload={"source": "discord_link_webhook"},
            )
        )

    logger.info(
        "discord-link webhook: status=%s discord_user_id=%s innersync_user_id=%s",
        status_str,
        discord_user_id,
        iu_raw,
    )
    return {"status": status_str, "discord_user_id": str(discord_user_id)}
