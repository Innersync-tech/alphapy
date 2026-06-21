from unittest.mock import patch

import pytest
from fastapi import HTTPException

import config
from webhooks.common import get_discord_link_webhook_secret, validate_webhook_signature


def test_get_discord_link_webhook_secret_prefers_dedicated():
    with patch.object(config, "DISCORD_LINK_WEBHOOK_SECRET", "dedicated", create=True):
        with patch.object(config, "APP_REFLECTIONS_WEBHOOK_SECRET", "other", create=True):
            assert get_discord_link_webhook_secret() == "dedicated"


def test_get_discord_link_webhook_secret_fallback_chain():
    with patch.object(config, "DISCORD_LINK_WEBHOOK_SECRET", None, create=True):
        with patch.object(config, "APP_REFLECTIONS_WEBHOOK_SECRET", "app", create=True):
            with patch.object(config, "WEBHOOK_SECRET", None, create=True):
                with patch.object(config, "SUPABASE_WEBHOOK_SECRET", None, create=True):
                    assert get_discord_link_webhook_secret() == "app"


def test_validate_webhook_signature_fail_closed_production():
    """No secret in production env must raise HTTP 503 (fail-closed P0 security fix)."""
    with patch.dict("os.environ", {"APP_ENV": "production", "STRICT_SECURITY_MODE": "0"}):
        with pytest.raises(HTTPException) as exc_info:
            validate_webhook_signature(body=b"payload", signature=None, secret=None)
    assert exc_info.value.status_code == 503


def test_validate_webhook_signature_fail_closed_strict_mode():
    """No secret with STRICT_SECURITY_MODE=1 must raise HTTP 503 regardless of APP_ENV."""
    with patch.dict("os.environ", {"APP_ENV": "development", "STRICT_SECURITY_MODE": "1"}):
        with pytest.raises(HTTPException) as exc_info:
            validate_webhook_signature(body=b"payload", signature=None, secret=None)
    assert exc_info.value.status_code == 503
