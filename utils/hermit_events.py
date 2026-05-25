"""Emit operational events to Core for Hermit reflection (no PII)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.logger import logger

ALLOWED_EVENT_TYPES = frozenset(
    {
        "gpt_command",
        "premium_check",
        "link_completed",
        "verification_outcome",
        "error_spike",
        "hermes_escalation_requested",
    }
)


def _core_url() -> str:
    return (getattr(config, "CORE_API_URL", "") or "").rstrip("/")


def _service_key() -> str:
    return (getattr(config, "ALPHAPY_SERVICE_KEY", "") or "").strip()


def _events_secret() -> str:
    return (
        getattr(config, "HERMIT_EVENTS_WEBHOOK_SECRET", "")
        or getattr(config, "HERMIT_PUSH_WEBHOOK_SECRET", "")
        or ""
    ).strip()


def _events_enabled() -> bool:
    return bool(getattr(config, "HERMIT_EVENTS_ENABLED", True))


async def emit_hermit_event(
    event_type: str,
    user_id: int,
    *,
    guild_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """
    POST a lightweight event to Core /integrations/hermit/events.
    Returns True when accepted, False when skipped or failed.
    """
    if not _events_enabled():
        return False
    if event_type not in ALLOWED_EVENT_TYPES:
        logger.warning("emit_hermit_event: disallowed event_type=%s", event_type)
        return False

    base = _core_url()
    key = _service_key()
    if not base or not key:
        logger.debug("emit_hermit_event skipped: CORE_API_URL or ALPHAPY_SERVICE_KEY missing")
        return False

    body: dict[str, Any] = {
        "event_type": event_type,
        "user_id": str(user_id),
        "payload": payload or {},
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    if guild_id is not None:
        body["guild_id"] = str(guild_id)

    body_bytes = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
    headers: dict[str, str] = {
        "X-API-Key": key,
        "Content-Type": "application/json",
    }
    secret = _events_secret()
    if secret:
        import hashlib
        import hmac

        signature = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Hermit-Signature"] = signature

    url = f"{base}/integrations/hermit/events"
    timeout = float(getattr(config, "HERMIT_CONTEXT_TIMEOUT_SECONDS", 2.0))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body_bytes, headers=headers)
        if response.status_code == 201:
            return True
        logger.warning("emit_hermit_event failed: status=%s type=%s", response.status_code, event_type)
        return False
    except Exception as error:
        logger.warning("emit_hermit_event exception: %s", error)
        return False
