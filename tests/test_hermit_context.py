from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_fetch_hermit_context_returns_none_when_disabled(monkeypatch) -> None:
    import utils.hermit_context as hermit_context

    monkeypatch.setattr(hermit_context.config, "HERMIT_CONTEXT_ENABLED", False)
    out = await hermit_context.fetch_hermit_context(123)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_hermit_context_uses_cache_when_fresh(monkeypatch) -> None:
    import utils.hermit_context as hermit_context

    monkeypatch.setattr(hermit_context.config, "HERMIT_CONTEXT_ENABLED", True)
    hermit_context._cache.clear()
    hermit_context._set_cache(
        user_id=42,
        context_text="Cached strategic context",
        updated_at="2026-01-01T00:00:00+00:00",
        staleness_minutes=1,
    )

    out = await hermit_context.fetch_hermit_context(42)
    assert out == "Cached strategic context"


@pytest.mark.asyncio
async def test_fetch_hermit_context_returns_stale_on_error(monkeypatch) -> None:
    import utils.hermit_context as hermit_context

    monkeypatch.setattr(hermit_context.config, "HERMIT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(hermit_context.config, "CORE_API_URL", "https://core.example")
    monkeypatch.setattr(hermit_context.config, "ALPHAPY_SERVICE_KEY", "secret")
    hermit_context._cache.clear()
    hermit_context._cache[7] = {
        "context_text": "Stale but acceptable context",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "staleness_minutes": 30,
        "cached_at": hermit_context.time.time(),
        "expires_at": hermit_context.time.monotonic() - 1,
    }

    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(hermit_context.httpx, "AsyncClient", _FailingClient)
    out = await hermit_context.fetch_hermit_context(7)
    assert out == "Stale but acceptable context"


@pytest.mark.asyncio
async def test_fetch_hermit_context_truncates_payload(monkeypatch) -> None:
    import utils.hermit_context as hermit_context

    monkeypatch.setattr(hermit_context.config, "HERMIT_CONTEXT_ENABLED", True)
    monkeypatch.setattr(hermit_context.config, "CORE_API_URL", "https://core.example")
    monkeypatch.setattr(hermit_context.config, "ALPHAPY_SERVICE_KEY", "secret")
    hermit_context._cache.clear()

    long_text = "x" * 5000

    class _Resp:
        status_code = 200
        is_success = True

        def json(self):
            return {
                "context_text": long_text,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "staleness_minutes": 0,
                "version": "v1",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(hermit_context.httpx, "AsyncClient", _Client)
    out = await hermit_context.fetch_hermit_context(99)
    assert out is not None
    assert len(out) == hermit_context.MAX_INJECT_CHARS


@pytest.mark.asyncio
async def test_ask_gpt_injects_hermit_context(monkeypatch) -> None:
    import gpt.helpers as helpers

    async def _fake_fetch(_user_id):
        return "Hermit says: focus on long-term consistency."

    monkeypatch.setattr(helpers, "_api_key_missing", False)
    monkeypatch.setattr(helpers, "fetch_hermit_context", _fake_fetch)
    monkeypatch.setattr(helpers, "_get_settings_values", lambda _model: ("grok-3", None))
    monkeypatch.setattr(helpers, "log_gpt_success", lambda **kwargs: None)
    monkeypatch.setattr(helpers, "record_prompt_usage", lambda applied: None)

    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class _Usage:
                total_tokens = 10

            class _Msg:
                content = "ok"

            class _Choice:
                message = _Msg()

            class _Resp:
                usage = _Usage()
                choices = [_Choice()]

            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(helpers, "llm_client", _Client())

    result = await helpers.ask_gpt(
        [{"role": "user", "content": "hello"}],
        user_id=123,
        guild_id=None,
        include_reflections=False,
    )
    assert result == "ok"
    system_content = captured["messages"][0]["content"]
    assert "Strategic context from Hermit" in system_content
    assert "Hermit says: focus on long-term consistency." in system_content
