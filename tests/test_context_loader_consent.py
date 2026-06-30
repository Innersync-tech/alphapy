"""Tests for consent-gated reflection context loading."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gpt import context_loader as cl


@pytest.mark.asyncio
async def test_load_agent_reflection_context_empty_without_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(cl, "_load_app_reflections", AsyncMock())

    result = await cl.load_agent_reflection_context(12345)

    assert result == ""
    cl._load_app_reflections.assert_not_called()


@pytest.mark.asyncio
async def test_load_agent_reflection_context_uses_consent_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consent = frozenset({"ref-a", "ref-b"})
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=consent))
    monkeypatch.setattr(
        cl,
        "_load_app_reflections",
        AsyncMock(return_value=("App context", 1)),
    )
    monkeypatch.setattr(
        cl,
        "_load_consented_reflections_shared",
        AsyncMock(return_value=("", 0)),
    )

    result = await cl.load_agent_reflection_context(12345, limit=5)

    assert result == "App context"
    cl._load_app_reflections.assert_awaited_once_with(
        12345,
        limit=5,
        allowed_reflection_ids=consent,
    )


@pytest.mark.asyncio
async def test_load_user_reflections_skips_app_without_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(cl, "_load_app_reflections", AsyncMock())
    monkeypatch.setattr(cl, "_supabase_get", AsyncMock(return_value=[]))

    result = await cl.load_user_reflections(999)

    assert result == ""
    cl._load_app_reflections.assert_not_called()
