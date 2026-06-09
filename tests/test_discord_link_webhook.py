"""Tests for POST /webhooks/discord-link."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webhooks import discord_link as dl


@pytest.fixture
def client_and_pool():
    app = FastAPI()
    app.include_router(dl.router)

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *args):
            return None

    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.acquire.return_value = _Acq()
    app.state.db_pool = pool
    return TestClient(app), pool


def test_discord_link_webhook_ok(client_and_pool):
    client, _pool = client_and_pool
    payload = {
        "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
        "discord_user_id": 123456789012345678,
        "link_source": "magic_link",
    }
    with (
        patch.object(dl, "get_discord_link_webhook_secret", return_value=None),
        patch.object(dl, "upsert_discord_link", new=AsyncMock(return_value=("ok", None))),
        patch.object(dl, "_try_dm_user", new=AsyncMock()),
    ):
        r = client.post("/webhooks/discord-link", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_discord_link_webhook_conflict_returns_409(client_and_pool):
    client, _pool = client_and_pool
    payload = {
        "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
        "discord_user_id": 123456789012345678,
    }
    with (
        patch.object(dl, "get_discord_link_webhook_secret", return_value=None),
        patch.object(
            dl,
            "upsert_discord_link",
            new=AsyncMock(return_value=("conflict", "Discord already linked")),
        ),
    ):
        r = client.post("/webhooks/discord-link", json=payload)
    assert r.status_code == 409


def test_discord_link_webhook_missing_fields_400(client_and_pool):
    client, _ = client_and_pool
    with patch.object(dl, "get_discord_link_webhook_secret", return_value=None):
        r = client.post("/webhooks/discord-link", json={"innersync_user_id": "550e8400-e29b-41d4-a716-446655440000"})
    assert r.status_code == 400


def test_discord_link_webhook_invalid_uuid_400(client_and_pool):
    client, _ = client_and_pool
    with patch.object(dl, "get_discord_link_webhook_secret", return_value=None):
        r = client.post(
            "/webhooks/discord-link",
            json={"innersync_user_id": "nope", "discord_user_id": 1},
        )
    assert r.status_code == 400


def test_discord_link_webhook_unlink_ok(client_and_pool):
    client, _pool = client_and_pool
    payload = {
        "event": "unlink",
        "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
        "discord_user_id": 123456789012345678,
    }
    with (
        patch.object(dl, "get_discord_link_webhook_secret", return_value=None),
        patch.object(dl, "delete_discord_link_for_discord_user", new=AsyncMock(return_value=True)),
        patch.object(dl, "_try_dm_user", new=AsyncMock()),
    ):
        r = client.post("/webhooks/discord-link", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_discord_link_webhook_no_pool_503():
    app = FastAPI()
    app.include_router(dl.router)
    app.state.db_pool = None
    client = TestClient(app)
    with patch.object(dl, "get_discord_link_webhook_secret", return_value=None):
        r = client.post(
            "/webhooks/discord-link",
            json={
                "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
                "discord_user_id": 1,
            },
        )
    assert r.status_code == 503
