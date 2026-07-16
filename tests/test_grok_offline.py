"""Unit tests for Grok failure classification."""

import pytest

from gpt.errors import (
    GrokFailureKind,
    GrokUnavailableError,
    classify_from_http,
    classify_grok_error,
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


@pytest.mark.asyncio
async def test_ask_gpt_raises_offline_never_returns_fallback(monkeypatch) -> None:
    """Callers must not treat unavailable text as model output (growth/agents/tickets)."""
    import gpt.helpers as helpers

    class _StatusError(Exception):
        status_code = 403

        def __init__(self) -> None:
            super().__init__("credits exhausted")

    class _Completions:
        async def create(self, **_kwargs):
            raise _StatusError()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(helpers, "llm_client", _Client())
    monkeypatch.setattr(helpers, "_api_key_missing", False)
    monkeypatch.setattr(helpers, "log_gpt_error", lambda **_kwargs: None)
    monkeypatch.setattr(helpers, "log_gpt_success", lambda **_kwargs: None)

    with pytest.raises(GrokUnavailableError) as exc_info:
        await helpers.ask_gpt(
            [{"role": "user", "content": "hello"}],
            user_id=1,
            guild_id=None,
            include_reflections=False,
        )

    err = exc_info.value
    assert err.kind is GrokFailureKind.OFFLINE
    assert err.user_message == ERR_GROK_OFFLINE
    assert "credit" not in err.user_message.lower()


def test_growth_catch_path_skips_persist_contract() -> None:
    """Growth stores grok_response only after a successful ask_gpt return.

    GrokUnavailableError is caught before Railway/Supabase writes (cogs/growth.py).
    """
    import ast
    from pathlib import Path

    source = Path("cogs/growth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found_handler = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
            if node.type.id == "GrokUnavailableError":
                found_handler = True
                handler_src = ast.get_source_segment(source, node) or ""
                assert "INSERT INTO growth_checkins" not in handler_src
                assert "grok_user_message" in handler_src
    assert found_handler, "growth must catch GrokUnavailableError before persisting"
