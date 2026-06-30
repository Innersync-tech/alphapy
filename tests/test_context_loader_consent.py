"""Tests for consent-gated reflection context loading."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from gpt import context_loader as cl


def test_format_reflection_block_requires_content() -> None:
    text, ok = cl._format_reflection_block(index=1, date_str="2026-06-18")
    assert ok is False
    assert text == ""

    text, ok = cl._format_reflection_block(
        index=1,
        date_str="2026-06-18",
        reflection_text="Inner voice",
        mantra="Breathe",
    )
    assert ok is True
    assert "Inner voice" in text
    assert "Breathe" in text


@pytest.mark.asyncio
async def test_fetch_active_consent_reflection_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cl,
        "_supabase_get",
        AsyncMock(return_value=[{"reflection_id": "r1"}, {"reflection_id": "r2"}]),
    )
    ids = await cl._fetch_active_consent_reflection_ids("user-uuid")
    assert ids == frozenset({"r1", "r2"})


@pytest.mark.asyncio
async def test_fetch_active_consent_reflection_ids_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cl, "_supabase_get", AsyncMock(side_effect=RuntimeError("db down")))
    ids = await cl._fetch_active_consent_reflection_ids("user-uuid")
    assert ids == frozenset()


@pytest.mark.asyncio
async def test_fetch_active_consent_reflection_ids_without_revoked_at_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prod schemas without revoked_at should still return consent rows."""

    async def _fake_get(table: str, params: dict | None = None) -> list[dict]:
        if params and "revoked_at" in params:
            raise RuntimeError("column reflection_alphapy_consent.revoked_at does not exist")
        return [{"reflection_id": "r1"}]

    monkeypatch.setattr(cl, "_supabase_get", _fake_get)
    ids = await cl._fetch_active_consent_reflection_ids("user-uuid")
    assert ids == frozenset({"r1"})


@pytest.mark.asyncio
async def test_load_consented_reflections_shared_formats_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cl,
        "_supabase_get",
        AsyncMock(
            return_value=[
                {
                    "reflection_id": "r1",
                    "reflection_text": "Shared entry",
                    "mantra": "Focus",
                    "thoughts": "",
                    "future_message": "",
                    "date": "2026-06-18",
                }
            ]
        ),
    )
    text, count = await cl._load_consented_reflections_shared(
        "user-uuid",
        frozenset({"r1"}),
        5,
    )
    assert count == 1
    assert "Shared entry" in text
    assert "explicitly shared" in text


@pytest.mark.asyncio
async def test_load_app_reflections_filters_by_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    created = datetime(2026, 6, 18, tzinfo=UTC)
    row = {
        "reflection_id": "r1",
        "plaintext_content": {
            "reflection_text": "From webhook",
            "mantra": "Go",
            "date": "2026-06-18",
        },
        "created_at": created,
    }

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[row])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool._closed = False

    monkeypatch.setattr(cl, "_get_app_reflections_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr("utils.db_helpers.acquire_safe", lambda _pool: ctx)

    text, count = await cl._load_app_reflections(
        12345,
        limit=3,
        allowed_reflection_ids=frozenset({"r1"}),
    )
    assert count == 1
    assert "From webhook" in text


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
async def test_load_agent_reflection_context_no_linked_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value=None))
    assert await cl.load_agent_reflection_context(12345) == ""


@pytest.mark.asyncio
async def test_load_agent_reflection_context_zero_limit() -> None:
    assert await cl.load_agent_reflection_context(12345, limit=0) == ""


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
async def test_load_agent_reflection_context_falls_back_to_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consent = frozenset({"ref-a"})
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=consent))
    monkeypatch.setattr(cl, "_load_app_reflections", AsyncMock(return_value=("", 0)))
    monkeypatch.setattr(
        cl,
        "_load_consented_reflections_shared",
        AsyncMock(return_value=("Shared only", 1)),
    )

    result = await cl.load_agent_reflection_context(99, limit=5)
    assert result == "Shared only"


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


@pytest.mark.asyncio
async def test_load_user_reflections_includes_discord_checkins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(cl, "_load_app_reflections", AsyncMock())

    async def _fake_get(table: str, params: dict | None = None) -> list[dict]:
        if table == "reflections":
            return [
                {
                    "reflection": "Discord check-in",
                    "mantra": "",
                    "future_message": "",
                    "date": "2026-06-10",
                }
            ]
        return []

    monkeypatch.setattr(cl, "_supabase_get", _fake_get)

    result = await cl.load_user_reflections(999)
    assert "Discord check-in" in result
    assert "/growthcheckin" in result


@pytest.mark.asyncio
async def test_load_user_reflections_with_consent_and_app_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consent = frozenset({"r1"})
    monkeypatch.setattr(cl, "_resolve_innersync_user_id", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(cl, "_fetch_active_consent_reflection_ids", AsyncMock(return_value=consent))
    monkeypatch.setattr(
        cl,
        "_load_app_reflections",
        AsyncMock(return_value=("Consented app text", 1)),
    )
    monkeypatch.setattr(
        cl,
        "_load_consented_reflections_shared",
        AsyncMock(return_value=("", 0)),
    )
    monkeypatch.setattr(cl, "_supabase_get", AsyncMock(return_value=[]))

    result = await cl.load_user_reflections(42)
    assert result == "Consented app text"
