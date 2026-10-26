from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from reflection_revoke_api import router


def _client(pool: MagicMock | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    if pool is not None:
        app.state.db_pool = pool
    return TestClient(app)


def _pool() -> MagicMock:
    pool = MagicMock()
    pool.is_closing.return_value = False
    return pool


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "svc-secret", raising=False)


def test_revoke_api_requires_api_key() -> None:
    client = _client(_pool())
    response = client.post("/api/reflections/revoke", json={"reflection_id": "ref-1"})
    assert response.status_code == 401


def test_revoke_api_rejects_wrong_key() -> None:
    client = _client(_pool())
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "nope"},
    )
    assert response.status_code == 401


def test_revoke_api_503_when_key_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "API_KEY", None, raising=False)
    client = _client(_pool())
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 503


def test_revoke_api_hard_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    monkeypatch.setattr(
        "reflection_revoke_api.resolve_discord_user_id",
        AsyncMock(return_value=77),
    )
    purge = AsyncMock(
        return_value={"deleted": 1, "remaining": 0, "tier2_purged": 1, "user_ids": [77]}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1", "user_id": 77},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "deleted",
        "count": 1,
        "remaining": 0,
        "tier2_purged": 1,
    }
    assert purge.await_args.kwargs["reflection_id"] == "ref-1"
    assert purge.await_args.kwargs["discord_user_id"] == 77
    assert purge.await_args.kwargs["purge_patterns"] is False


def test_revoke_api_503_without_pool() -> None:
    client = _client(None)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 503


def test_revoke_api_400_for_blank_reflection_id() -> None:
    client = _client(_pool())
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "   "},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 400


def test_revoke_api_unresolved_uuid_still_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    from webhooks.reflection_payload import ReflectionWebhookPayloadError

    pool = _pool()
    monkeypatch.setattr(
        "reflection_revoke_api.resolve_discord_user_id",
        AsyncMock(side_effect=ReflectionWebhookPayloadError("unlinked")),
    )
    purge = AsyncMock(
        return_value={"deleted": 1, "remaining": 0, "tier2_purged": 1, "user_ids": []}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1", "user_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 200
    assert purge.await_args.kwargs["innersync_user_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_revoke_api_unresolved_snowflake_deletes_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from webhooks.reflection_payload import ReflectionWebhookPayloadError

    pool = _pool()
    monkeypatch.setattr(
        "reflection_revoke_api.resolve_discord_user_id",
        AsyncMock(side_effect=ReflectionWebhookPayloadError("bad")),
    )
    purge = AsyncMock(
        return_value={"deleted": 1, "remaining": 0, "tier2_purged": 0, "user_ids": []}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1", "user_id": "12345"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 200
    assert purge.await_args.kwargs["innersync_user_id"] is None


def test_revoke_api_accepts_reflection_id_only(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    purge = AsyncMock(
        return_value={"deleted": 0, "remaining": 0, "tier2_purged": 0, "user_ids": []}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "already-gone"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "deleted",
        "count": 0,
        "remaining": 0,
        "tier2_purged": 0,
    }
    assert purge.await_args.kwargs["discord_user_id"] is None
    assert purge.await_args.kwargs["purge_patterns"] is False


def test_revoke_api_idempotent_re_revoke(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    purge = AsyncMock(
        return_value={"deleted": 0, "remaining": 0, "tier2_purged": 0, "user_ids": []}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    first = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "svc-secret"},
    )
    second = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["remaining"] == 0
    assert second.json() == {
        "status": "deleted",
        "count": 0,
        "remaining": 0,
        "tier2_purged": 0,
    }


def test_revoke_api_remaining_rows_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    purge = AsyncMock(
        return_value={"deleted": 1, "remaining": 2, "tier2_purged": 0, "user_ids": [77]}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1"},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 500
    assert response.json() == {
        "status": "deleted",
        "count": 1,
        "remaining": 2,
        "tier2_purged": 0,
    }


def test_revoke_api_passes_purge_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _pool()
    purge = AsyncMock(
        return_value={"deleted": 1, "remaining": 0, "tier2_purged": 1, "user_ids": []}
    )
    monkeypatch.setattr("reflection_revoke_api.purge_app_reflection_copy", purge)

    client = _client(pool)
    response = client.post(
        "/api/reflections/revoke",
        json={"reflection_id": "ref-1", "purge_patterns": True},
        headers={"X-API-Key": "svc-secret"},
    )
    assert response.status_code == 200
    assert purge.await_args.kwargs["purge_patterns"] is True
