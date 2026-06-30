from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from webhooks import reflection_payload as rp


@pytest.mark.asyncio
async def test_resolve_discord_user_id_accepts_integer() -> None:
    assert await rp.resolve_discord_user_id(None, 123456789) == 123456789


@pytest.mark.asyncio
async def test_resolve_discord_user_id_resolves_innersync_uuid(monkeypatch) -> None:
    monkeypatch.setattr(
        rp,
        "get_discord_id_for_innersync",
        AsyncMock(return_value=987654321),
    )
    out = await rp.resolve_discord_user_id(
        MagicMock(),
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    assert out == 987654321


@pytest.mark.asyncio
async def test_resolve_discord_user_id_rejects_unlinked_uuid(monkeypatch) -> None:
    monkeypatch.setattr(
        rp,
        "get_discord_id_for_innersync",
        AsyncMock(return_value=None),
    )
    with pytest.raises(rp.ReflectionWebhookPayloadError):
        await rp.resolve_discord_user_id(
            MagicMock(),
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


def test_extract_plaintext_content_canonical() -> None:
    content = rp.extract_plaintext_content(
        {
            "plaintext_content": {
                "reflection_text": "Hello",
                "mantra": "Go",
                "date": "2026-06-10",
            }
        }
    )
    assert content["reflection_text"] == "Hello"
    assert content["mantra"] == "Go"


def test_extract_plaintext_content_legacy_flat_fields() -> None:
    content = rp.extract_plaintext_content(
        {
            "reflection": "Shared entry",
            "mantra": "Breathe",
            "date": "2026-06-10",
        }
    )
    assert content["reflection_text"] == "Shared entry"
    assert content["reflection"] == "Shared entry"


@pytest.mark.asyncio
async def test_app_reflections_webhook_stores_normalized_payload(monkeypatch) -> None:
    from webhooks.app_reflections import handle_app_reflection_webhook

    monkeypatch.setattr(
        "webhooks.app_reflections.validate_webhook_signature",
        lambda *args, **kwargs: None,
    )

    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    request = MagicMock()
    request.app.state.db_pool = pool
    request.headers.get.return_value = None
    request.body = AsyncMock(
        return_value=json.dumps(
            {
                "user_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "reflection_id": "ref-1",
                "reflection": "Today I shared this",
                "mantra": "Stay",
                "date": "2026-06-10",
            }
        ).encode()
    )

    monkeypatch.setattr(
        "webhooks.app_reflections.resolve_discord_user_id",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr("webhooks.app_reflections.forward_reflection", lambda _p: None)

    result = await handle_app_reflection_webhook(request)
    assert result["status"] == "acknowledged"
    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    assert args[1] == 42
    assert args[2] == "ref-1"
