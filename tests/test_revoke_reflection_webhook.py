from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webhooks.revoke_reflection import router


def _client(pool: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.db_pool = pool
    return TestClient(app)


def _pool() -> MagicMock:
    pool = MagicMock()
    pool.is_closing.return_value = False
    return pool


@pytest.fixture(autouse=True)
def _skip_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webhooks.revoke_reflection.validate_webhook_signature",
        lambda *args, **kwargs: None,
    )


@pytest.mark.asyncio
async def test_revoke_webhook_deletes_by_reflection_id(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    monkeypatch.setattr(
        "webhooks.revoke_reflection.resolve_discord_user_id",
        AsyncMock(return_value=99),
    )
    purge = AsyncMock(return_value={"deleted": 1, "tier2_purged": 1, "user_ids": [99]})
    monkeypatch.setattr("webhooks.revoke_reflection.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"user_id": 99, "reflection_id": "ref-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "count": 1}
    purge.assert_awaited_once()
    kwargs = purge.await_args.kwargs
    assert kwargs["reflection_id"] == "ref-1"
    assert kwargs["discord_user_id"] == 99
    assert kwargs["purge_patterns"] is False


@pytest.mark.asyncio
async def test_revoke_webhook_deletes_when_user_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    resolve = AsyncMock()
    monkeypatch.setattr("webhooks.revoke_reflection.resolve_discord_user_id", resolve)
    purge = AsyncMock(return_value={"deleted": 1, "tier2_purged": 0, "user_ids": []})
    monkeypatch.setattr("webhooks.revoke_reflection.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"reflection_id": "ref-orphan"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    resolve.assert_not_awaited()
    assert purge.await_args.kwargs["discord_user_id"] is None
    assert purge.await_args.kwargs["reflection_id"] == "ref-orphan"


@pytest.mark.asyncio
async def test_revoke_webhook_still_deletes_when_user_unlinked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from webhooks.reflection_payload import ReflectionWebhookPayloadError

    pool = _pool()
    monkeypatch.setattr(
        "webhooks.revoke_reflection.resolve_discord_user_id",
        AsyncMock(side_effect=ReflectionWebhookPayloadError("Discord account not linked")),
    )
    purge = AsyncMock(return_value={"deleted": 1, "tier2_purged": 1, "user_ids": []})
    monkeypatch.setattr("webhooks.revoke_reflection.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps(
            {
                "user_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "reflection_id": "ref-1",
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    kwargs = purge.await_args.kwargs
    assert kwargs["innersync_user_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert kwargs["discord_user_id"] is None


def test_revoke_webhook_requires_reflection_id() -> None:
    client = _client(_pool())
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"user_id": 1}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_revoke_webhook_invalid_json() -> None:
    client = _client(_pool())
    response = client.post(
        "/webhooks/revoke-reflection",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_revoke_webhook_503_without_pool() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"reflection_id": "ref-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503


def test_revoke_webhook_500_when_purge_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    monkeypatch.setattr(
        "webhooks.revoke_reflection.purge_app_reflection_copy",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    client = _client(pool)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"reflection_id": "ref-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 500


def test_revoke_webhook_passes_purge_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    purge = AsyncMock(return_value={"deleted": 1, "tier2_purged": 1, "user_ids": []})
    monkeypatch.setattr("webhooks.revoke_reflection.purge_app_reflection_copy", purge)
    client = _client(pool)
    response = client.post(
        "/webhooks/revoke-reflection",
        content=json.dumps({"reflection_id": "ref-1", "purge_patterns": True}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert purge.await_args.kwargs["purge_patterns"] is True
