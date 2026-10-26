"""Service-key hard-delete for a remote App reflection copy.

Core / App should await this after consent revoke or journal delete so the
Railway plaintext cannot outlive the share. HMAC ``POST /webhooks/revoke-reflection``
remains for the existing fire-and-forget path.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from utils.app_reflection_purge import purge_app_reflection_copy
from webhooks.reflection_payload import ReflectionWebhookPayloadError, resolve_discord_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reflections", tags=["reflections"])


class RevokeReflectionRequest(BaseModel):
    reflection_id: str = Field(..., min_length=1)
    user_id: int | str | None = None
    purge_patterns: bool = False


class RevokeReflectionResponse(BaseModel):
    status: str
    count: int
    remaining: int
    tier2_purged: int


async def require_reflection_revoke_service_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Require Alphapy ``API_KEY`` — same gate as Hermit broker (no anonymous)."""
    configured_key = getattr(config, "API_KEY", None)
    if not configured_key:
        raise HTTPException(
            status_code=503,
            detail="Reflection revoke unavailable: API key is not configured",
        )
    provided = (x_api_key or "").encode("utf-8")
    expected = str(configured_key).encode("utf-8")
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post(
    "/revoke",
    response_model=RevokeReflectionResponse,
    dependencies=[Depends(require_reflection_revoke_service_key)],
)
async def revoke_app_reflection_copy_endpoint(
    request: Request,
    body: RevokeReflectionRequest,
) -> RevokeReflectionResponse | JSONResponse:
    """Hard-delete Railway ``app_reflections`` for one journal id. Idempotent."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        import api as alphapy_api

        pool = getattr(alphapy_api, "db_pool", None)
    if not pool or getattr(pool, "is_closing", lambda: False)():
        logger.error("Database pool not available for reflection revoke API")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable.")

    reflection_id = body.reflection_id.strip()
    if not reflection_id:
        raise HTTPException(status_code=400, detail="Missing required field: reflection_id.")

    discord_user_id: int | None = None
    innersync_user_id: str | None = None
    if body.user_id is not None and str(body.user_id).strip():
        raw = body.user_id
        try:
            discord_user_id = await resolve_discord_user_id(pool, raw)
        except ReflectionWebhookPayloadError:
            raw_str = str(raw).strip()
            if "-" in raw_str:
                innersync_user_id = raw_str
            else:
                logger.warning(
                    "Reflection revoke API: unresolved user_id=%s; deleting by reflection_id only",
                    raw_str,
                )

    result = await purge_app_reflection_copy(
        pool,
        reflection_id=reflection_id,
        discord_user_id=discord_user_id,
        innersync_user_id=innersync_user_id,
        purge_patterns=bool(body.purge_patterns),
    )
    remaining = int(result["remaining"])
    payload = RevokeReflectionResponse(
        status="deleted",
        count=int(result["deleted"]),
        remaining=remaining,
        tier2_purged=int(result["tier2_purged"]),
    )
    if remaining > 0:
        logger.error(
            "Reflection revoke left remaining rows: reflection_id=%s deleted=%s remaining=%s",
            reflection_id,
            payload.count,
            remaining,
        )
        return JSONResponse(status_code=500, content=payload.model_dump())
    return payload


__all__ = ["require_reflection_revoke_service_key", "router"]
