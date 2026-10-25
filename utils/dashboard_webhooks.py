"""Signed outbound webhook calls from Alphapy to the alphapy-dashboard.

The bot forwards relevant events to the dashboard so its UI can update in
real-time. Every outbound request is signed with HMAC-SHA256 so the dashboard
can verify the payload origin.

Usage:
    from utils.dashboard_webhooks import forward_reflection, forward_revoke_reflection

    # Call from within an async handler (fire-and-forget; never raises):
    forward_reflection(payload)
    forward_revoke_reflection(payload)

Env vars required (set in Railway):
    DASHBOARD_BASE_URL        — e.g. https://dashboard.alphapy.innersync.tech
    REFLECTION_WEBHOOK_SECRET — HMAC secret shared with dashboard for reflections
    GDPR_WEBHOOK_SECRET       — HMAC secret shared with dashboard for revoke-reflection
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

_DASHBOARD_TIMEOUT = 8.0


def sign_payload(body: bytes, secret: str) -> str:
    """Return HMAC-SHA256 signature in 'sha256=<hex>' format."""
    digest = hmac.HMAC(
        secret.encode("utf-8"), body, digestmod=hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


async def _post(path: str, payload: dict[str, Any], secret: str, header_name: str) -> None:
    """POST payload to the dashboard with HMAC signature. Logs on failure, never raises."""
    base_url = getattr(config, "DASHBOARD_BASE_URL", "").rstrip("/")
    if not base_url or not secret:
        return

    url = f"{base_url}{path}"

    try:
        body = json.dumps(payload).encode("utf-8")
        signature = sign_payload(body, secret)
        async with httpx.AsyncClient(timeout=_DASHBOARD_TIMEOUT) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    header_name: signature,
                },
            )
        if not response.is_success:
            logger.warning(
                "Dashboard webhook %s returned %s: %s",
                path,
                response.status_code,
                response.text[:200],
            )
    except Exception as exc:
        logger.warning("Dashboard webhook %s failed: %s", path, exc)


def forward_reflection(payload: dict[str, Any]) -> None:
    """Fire-and-forget: notify dashboard of a new/updated reflection.

    Endpoint : POST /api/webhooks/reflections
    Header   : x-reflection-signature
    Secret   : REFLECTION_WEBHOOK_SECRET
    """
    secret = getattr(config, "REFLECTION_WEBHOOK_SECRET", None) or ""
    if not secret:
        return
    asyncio.create_task(
        _post("/api/webhooks/reflections", payload, secret, "x-reflection-signature")
    )


def forward_revoke_reflection(payload: dict[str, Any]) -> None:
    """Fire-and-forget: notify dashboard that a reflection was revoked (GDPR delete).

    Endpoint : POST /api/webhooks/revoke-reflection
    Header   : x-gdpr-signature
    Secret   : GDPR_WEBHOOK_SECRET
    """
    secret = getattr(config, "GDPR_WEBHOOK_SECRET", None) or ""
    if not secret:
        return
    asyncio.create_task(
        _post("/api/webhooks/revoke-reflection", payload, secret, "x-gdpr-signature")
    )


__all__ = [
    "sign_payload",
    "forward_reflection",
    "forward_revoke_reflection",
]
