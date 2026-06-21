"""
Shared utilities for webhook handlers.

Centralizes HMAC signature validation so fixes and improvements apply to all
webhooks (app-reflections, revoke-reflection, reflections, supabase).
"""

import hashlib
import hmac
import logging
import os

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


def validate_webhook_signature(
    body: bytes,
    signature: str | None,
    secret: str | None,
    *,
    log_name: str | None = None,
    missing_detail: str = "Missing webhook signature header.",
    invalid_detail: str = "Invalid webhook signature.",
) -> None:
    """
    Validate webhook HMAC-SHA256 signature.

    If secret is None or empty, skips validation (optionally logs when log_name
    is set). Otherwise requires the X-Webhook-Signature header and validates
    it against HMAC-SHA256(secret, body). Supports both raw hex and "sha256=..."
    prefix.

    Raises:
        HTTPException: 401 if secret is set and signature is missing or invalid.
    """
    if not secret:
        app_env = os.environ.get("APP_ENV", "development")
        strict = os.environ.get("STRICT_SECURITY_MODE", "0")
        if app_env == "production" or strict == "1":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook secret not configured. Set the required *_WEBHOOK_SECRET environment variable.",
            )
        if log_name:
            logger.warning(
                "No %s webhook secret configured - skipping signature validation (non-production only)",
                log_name,
            )
        return

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=missing_detail,
        )

    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()
    provided = provided.lower()

    hmac_obj = hmac.HMAC(
        secret.encode("utf-8"), body, digestmod=hashlib.sha256
    )
    computed = hmac_obj.hexdigest()
    if not hmac.compare_digest(provided, computed):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=invalid_detail,
        )


def get_app_reflections_secret() -> str | None:
    """Secret for app-reflections and revoke-reflection webhooks (Core-API)."""
    import config

    return (
        getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None)
        or getattr(config, "WEBHOOK_SECRET", None)
        or getattr(config, "SUPABASE_WEBHOOK_SECRET", None)
    )


def get_reflections_webhook_secret() -> str | None:
    """Secret for reflections webhook (reflection.created events)."""
    import config

    return getattr(config, "WEBHOOK_SECRET", None) or getattr(
        config, "SUPABASE_WEBHOOK_SECRET", None
    )


def get_premium_invalidate_secret() -> str | None:
    """Secret for premium-invalidate webhook (Core notifies on subscription change)."""
    import config

    return (
        getattr(config, "PREMIUM_INVALIDATE_WEBHOOK_SECRET", None)
        or getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None)
        or getattr(config, "WEBHOOK_SECRET", None)
        or getattr(config, "SUPABASE_WEBHOOK_SECRET", None)
    )


def get_founder_webhook_secret() -> str | None:
    """Secret for founder webhook (Core notifies for early-bird founder welcome DM)."""
    import config

    return (
        getattr(config, "FOUNDER_WEBHOOK_SECRET", None)
        or getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None)
        or getattr(config, "WEBHOOK_SECRET", None)
        or getattr(config, "SUPABASE_WEBHOOK_SECRET", None)
    )


def get_legal_update_webhook_secret() -> str | None:
    """Secret for legal-update webhook (GitHub Action notifies on PP/ToS change)."""
    import config

    return (
        getattr(config, "LEGAL_UPDATE_WEBHOOK_SECRET", None)
        or getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None)
        or getattr(config, "WEBHOOK_SECRET", None)
    )


def get_discord_link_webhook_secret() -> str | None:
    """Secret for Core → Alphapy Discord/Innersync link confirmation webhook."""
    import config

    return (
        getattr(config, "DISCORD_LINK_WEBHOOK_SECRET", None)
        or getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None)
        or getattr(config, "WEBHOOK_SECRET", None)
        or getattr(config, "SUPABASE_WEBHOOK_SECRET", None)
    )


__all__ = [
    "validate_webhook_signature",
    "get_app_reflections_secret",
    "get_reflections_webhook_secret",
    "get_premium_invalidate_secret",
    "get_founder_webhook_secret",
    "get_legal_update_webhook_secret",
    "get_discord_link_webhook_secret",
]
