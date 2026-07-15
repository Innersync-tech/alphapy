"""Unit tests for Grok failure classification."""

from gpt.errors import (
    GrokFailureKind,
    GrokUnavailableError,
    classify_grok_error,
    classify_from_http,
    grok_user_message,
    should_enqueue_retry,
)
from utils.user_messages import ERR_GROK_OFFLINE, ERR_GROK_RATE_LIMITED


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def test_classify_missing_key() -> None:
    kind, detail = classify_grok_error(RuntimeError("GROK_API_KEY is missing"))
    assert kind is GrokFailureKind.OFFLINE
    assert detail == "missing_key"


def test_classify_unauthorized() -> None:
    kind, detail = classify_grok_error(_StatusError(401, "Unauthorized"))
    assert kind is GrokFailureKind.OFFLINE
    assert detail == "unauthorized"


def test_classify_credits() -> None:
    kind, detail = classify_grok_error(_StatusError(403, "credits exhausted"))
    assert kind is GrokFailureKind.OFFLINE
    assert detail == "credits"


def test_classify_rate_limit() -> None:
    kind, detail = classify_grok_error(_StatusError(429, "rate limit exceeded"))
    assert kind is GrokFailureKind.RATE_LIMITED
    assert detail == "rate_limit"
    assert should_enqueue_retry(kind) is True


def test_classify_5xx_is_offline_not_retry_enqueue() -> None:
    kind, detail = classify_grok_error(_StatusError(503, "Service Unavailable"))
    assert kind is GrokFailureKind.OFFLINE
    assert detail == "upstream_503"
    assert should_enqueue_retry(kind) is False


def test_user_messages_never_leak_ops() -> None:
    offline = GrokUnavailableError(
        GrokFailureKind.OFFLINE,
        operator_detail="credits",
        user_message=ERR_GROK_OFFLINE,
    )
    rate = GrokUnavailableError(
        GrokFailureKind.RATE_LIMITED,
        operator_detail="rate_limit",
        user_message=ERR_GROK_RATE_LIMITED,
    )
    assert "credit" not in grok_user_message(offline).lower()
    assert "api" not in grok_user_message(offline).lower()
    assert grok_user_message(rate) == ERR_GROK_RATE_LIMITED


def test_classify_from_http_credits_body() -> None:
    kind, detail = classify_from_http(403, {"error": {"message": "Insufficient credits"}})
    assert kind is GrokFailureKind.OFFLINE
    assert detail == "credits"
