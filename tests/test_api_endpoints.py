"""
Tests for critical API endpoints.

Uses a minimal FastAPI app with the main router to avoid importing the full
api.py startup logic. Dependencies (API key, auth, guild admin) are overridden
via app.dependency_overrides; db_pool is patched at the module level.
"""

from concurrent.futures import Future
from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api as api_module
from api import (
    get_authenticated_user_id,
    require_observability_api_key,
    router,
    verify_api_key,
    verify_dashboard_discord_admin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GUILD_ID = 111111111111111111
# JWT `sub` must be a UUID for Innersync resolution; Discord snowflake used in DB rows.
AUTH_SUB = "550e8400-e29b-41d4-a716-446655440000"
DISCORD_USER_ID = 999999999999999999


def make_app(auth_user: str = AUTH_SUB) -> FastAPI:
    """Build a fresh FastAPI instance with auth/api-key deps bypassed."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_key] = lambda: None
    app.dependency_overrides[get_authenticated_user_id] = lambda: auth_user
    return app


def _fake_record(**kwargs):
    """Return a dict that behaves like an asyncpg Record for these tests."""
    defaults = {
        "id": 1,
        "name": "Test Reminder",
        "time": time(10, 0),
        "call_time": None,
        "days": ["monday"],
        "message": "Hello",
        "channel_id": 123,
        "location": None,
        "event_time": None,
        "created_by": DISCORD_USER_ID,
    }
    defaults.update(kwargs)

    class _Record(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    return _Record(defaults)


def _mock_pool(*rows):
    """Return a mock db_pool whose acquire() yields a connection returning rows."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=list(rows))
    conn.execute = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool, conn


def _patch_resolve_innersync_to_discord():
    """JWT sub (UUID) resolves to DISCORD_USER_ID for reminder DB queries."""
    return patch(
        "utils.innersync_identity.resolve_innersync_jwt_sub_to_discord_int",
        new=AsyncMock(return_value=DISCORD_USER_ID),
    )


# ---------------------------------------------------------------------------
# GET /api/reminders/{user_id}
# ---------------------------------------------------------------------------


class TestGetUserReminders:
    """Tests for GET /api/reminders/{user_id}."""

    def test_happy_path_returns_reminder_list(self):
        pool, _ = _mock_pool(_fake_record())
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
        ):
            client = TestClient(app)
            response = client.get(f"/api/reminders/{AUTH_SUB}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Reminder"
        assert data[0]["user_id"] == str(DISCORD_USER_ID)

    def test_empty_list_when_no_reminders(self):
        pool, _ = _mock_pool()  # no rows
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
        ):
            client = TestClient(app)
            response = client.get(f"/api/reminders/{AUTH_SUB}")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_503_when_db_unavailable(self):
        app = make_app()
        with patch.object(api_module, "db_pool", None):
            client = TestClient(app)
            response = client.get(f"/api/reminders/{AUTH_SUB}")
        assert response.status_code == 503

    def test_returns_403_when_user_id_mismatch(self):
        """Authenticated user can only access their own reminders."""
        app = make_app(auth_user="other_user")
        client = TestClient(app)
        response = client.get(f"/api/reminders/{AUTH_SUB}")
        assert response.status_code == 403

    def test_returns_401_without_auth(self):
        """Without auth override the dependency raises 401."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_api_key] = lambda: None
        # No get_authenticated_user_id override → real dependency raises 401
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"/api/reminders/{AUTH_SUB}")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/reminders
# ---------------------------------------------------------------------------


class TestAddReminder:
    """Tests for POST /api/reminders."""

    _valid_payload = {
        "id": 1,
        "name": "Daily standup",
        "time": "09:00",
        "days": ["monday", "tuesday"],
        "message": "Time to sync",
        "channel_id": 456,
        "user_id": AUTH_SUB,
    }

    def test_creates_reminder_and_returns_success(self):
        pool, conn = _mock_pool()
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
        ):
            client = TestClient(app)
            response = client.post("/api/reminders", json=self._valid_payload)
        assert response.status_code == 200
        assert response.json() == {"success": True}
        conn.execute.assert_awaited_once()

    def test_returns_503_when_db_unavailable(self):
        app = make_app()
        with patch.object(api_module, "db_pool", None):
            client = TestClient(app)
            response = client.post("/api/reminders", json=self._valid_payload)
        assert response.status_code == 503

    def test_returns_403_when_user_id_mismatch(self):
        """user_id in payload must match authenticated user."""
        app = make_app(auth_user="someone_else")
        pool, _ = _mock_pool()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post("/api/reminders", json=self._valid_payload)
        assert response.status_code == 403

    def test_returns_422_for_missing_required_fields(self):
        app = make_app()
        client = TestClient(app)
        response = client.post("/api/reminders", json={"name": "incomplete"})
        assert response.status_code == 422

    def test_idempotency_key_reuses_previous_response(self):
        pool, conn = _mock_pool()
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
            patch.object(api_module, "_idempotency_cache", {}),
        ):
            client = TestClient(app)
            headers = {"Idempotency-Key": "abc-123"}
            first = client.post("/api/reminders", json=self._valid_payload, headers=headers)
            second = client.post("/api/reminders", json=self._valid_payload, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        conn.execute.assert_awaited_once()


class TestEditReminder:
    """Tests for PUT /api/reminders."""

    _valid_payload = {
        "id": 5,
        "name": "Daily standup",
        "time": "09:00",
        "days": ["monday", "tuesday"],
        "message": "Time to sync",
        "channel_id": 456,
        "user_id": AUTH_SUB,
    }

    def test_updates_reminder_and_returns_success(self):
        pool, conn = _mock_pool()
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
        ):
            client = TestClient(app)
            response = client.put("/api/reminders", json=self._valid_payload)
        assert response.status_code == 200
        assert response.json() == {"success": True}
        conn.execute.assert_awaited_once()


class TestRemoveReminder:
    """Tests for DELETE /api/reminders/{reminder_id}/{created_by}."""

    def test_deletes_reminder_and_returns_success(self):
        pool, conn = _mock_pool()
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            _patch_resolve_innersync_to_discord(),
        ):
            client = TestClient(app)
            response = client.delete(f"/api/reminders/5/{AUTH_SUB}")
        assert response.status_code == 200
        assert response.json() == {"success": True}
        conn.execute.assert_awaited_once()


class TestApiObservability:
    def test_observability_endpoint_includes_latency_and_success_rate(self):
        pool, _ = _mock_pool(_fake_record())
        with (
            patch.object(api_module, "db_pool", pool),
            patch.object(api_module, "_api_total_requests", 10),
            patch.object(api_module, "_api_success_requests", 9),
            patch.object(api_module, "_webhook_total_requests", 4),
            patch.object(api_module, "_webhook_success_requests", 3),
            patch.object(api_module, "_api_latencies_ms", api_module.deque([12.0, 18.0, 27.0, 31.0], maxlen=2000)),
            patch.object(api_module, "_webhook_latencies_ms", api_module.deque([9.0, 16.0, 24.0], maxlen=2000)),
        ):
            data = api_module.get_observability()
        assert "api" in data
        assert "webhooks" in data
        assert "hermit_context" in data
        assert data["api"]["requests"] == 10
        assert data["api"]["success_rate"] == 0.9
        assert "p95" in data["api"]["latency_ms"]


class TestRequireObservabilityApiKey:
    @pytest.mark.asyncio
    async def test_returns_503_when_api_key_not_configured(self):
        with patch.object(api_module.config, "API_KEY", None):
            with pytest.raises(api_module.HTTPException) as exc_info:
                await require_observability_api_key(x_api_key="any-value")
        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_returns_401_when_api_key_mismatch(self):
        with patch.object(api_module.config, "API_KEY", "expected-key"):
            with pytest.raises(api_module.HTTPException) as exc_info:
                await require_observability_api_key(x_api_key="wrong-key")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    @pytest.mark.asyncio
    async def test_allows_request_when_api_key_matches(self):
        with patch.object(api_module.config, "API_KEY", "expected-key"):
            await require_observability_api_key(x_api_key="expected-key")


# ---------------------------------------------------------------------------
# GET /api/dashboard/settings/{guild_id}
# ---------------------------------------------------------------------------


class TestGetGuildSettings:
    """Tests for GET /api/dashboard/settings/{guild_id}."""

    def test_requires_auth(self):
        """Endpoint must return 401 when not authenticated."""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_api_key] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"/api/dashboard/settings/{GUILD_ID}")
        assert response.status_code == 401

    def test_returns_503_when_db_unavailable(self):
        app = make_app()
        with (
            patch.object(api_module, "db_pool", None),
            patch("api.verify_guild_admin_access", new=AsyncMock()),
        ):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/settings/{GUILD_ID}")
        assert response.status_code == 503

    def test_returns_settings_for_guild(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[
            {"scope": "system", "key": "log_channel_id", "value": "123"},
            {"scope": "embedwatcher", "key": "enabled", "value": "true"},
        ])
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            patch("api.verify_guild_admin_access", new=AsyncMock()),
        ):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/settings/{GUILD_ID}")
        assert response.status_code == 200
        data = response.json()
        assert data["system"]["log_channel_id"] == 123
        assert data["embedwatcher"]["enabled"] is True

    def test_non_admin_gets_403(self):
        """verify_guild_admin_access raises 403 for non-admins."""
        from fastapi import HTTPException

        app = make_app()
        with (
            patch.object(api_module, "db_pool", MagicMock()),
            patch(
                "api.verify_guild_admin_access",
                new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden")),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(f"/api/dashboard/settings/{GUILD_ID}")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/dashboard/{guild_id}/automod/rules
# ---------------------------------------------------------------------------


class TestGetAutomodRules:
    """Tests for GET /api/dashboard/{guild_id}/automod/rules."""

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_api_key] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"/api/dashboard/{GUILD_ID}/automod/rules")
        assert response.status_code == 401

    def test_returns_503_when_db_unavailable(self):
        app = make_app()
        with (
            patch.object(api_module, "db_pool", None),
            patch("api.verify_guild_admin_access", new=AsyncMock()),
        ):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/automod/rules")
        assert response.status_code == 503

    def test_returns_empty_list_when_no_rules(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        app = make_app()
        with (
            patch.object(api_module, "db_pool", pool),
            patch("api.verify_guild_admin_access", new=AsyncMock()),
        ):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/automod/rules")
        assert response.status_code == 200
        assert response.json() == []

    def test_non_admin_gets_403(self):
        from fastapi import HTTPException

        app = make_app()
        with (
            patch.object(api_module, "db_pool", MagicMock()),
            patch(
                "api.verify_guild_admin_access",
                new=AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden")),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(f"/api/dashboard/{GUILD_ID}/automod/rules")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Dashboard verification queue / resolve
# ---------------------------------------------------------------------------


def _verification_queue_row(**kwargs):
    defaults = {
        "id": 7,
        "user_id": DISCORD_USER_ID,
        "channel_id": 222222222222222222,
        "status": "manual_review",
        "ai_reason": "Amount mismatch",
        "ai_can_verify": False,
        "ai_needs_manual_review": True,
        "payment_date": date(2026, 6, 1),
        "created_at": datetime(2026, 6, 1, 10, 0, 0),
    }
    defaults.update(kwargs)
    return _fake_record(**defaults)


def _make_dashboard_app() -> FastAPI:
    app = make_app()
    app.dependency_overrides[verify_dashboard_discord_admin] = lambda: DISCORD_USER_ID
    return app


class TestVerificationQueue:
  def test_returns_503_when_db_unavailable(self):
      app = _make_dashboard_app()
      with patch.object(api_module, "db_pool", None):
          client = TestClient(app)
          response = client.get(f"/api/dashboard/{GUILD_ID}/verification/queue")
      assert response.status_code == 503

  def test_returns_queue_without_screenshot_fields(self):
      pool, conn = _mock_pool(_verification_queue_row())
      app = _make_dashboard_app()
      with patch.object(api_module, "db_pool", pool):
          client = TestClient(app)
          response = client.get(f"/api/dashboard/{GUILD_ID}/verification/queue")
      assert response.status_code == 200
      data = response.json()
      assert len(data) == 1
      assert data[0]["id"] == 7
      assert data[0]["ai_reason"] == "Amount mismatch"
      assert "screenshot" not in data[0]
      conn.fetch.assert_awaited_once()


class TestVerificationResolve:
  @patch("api.asyncio.run_coroutine_threadsafe")
  def test_resolve_approved(self, mock_threadsafe):
      future = Future()
      future.set_result(None)
      mock_threadsafe.return_value = future
      mock_bot = MagicMock()
      mock_bot.loop = MagicMock()
      app = _make_dashboard_app()
      with patch("gpt.helpers.bot_instance", mock_bot):
          client = TestClient(app)
          response = client.post(
              f"/api/dashboard/{GUILD_ID}/verification/7/resolve",
              json={"outcome": "approved"},
          )
      assert response.status_code == 200
      assert response.json() == {"success": True, "outcome": "approved", "ticket_id": 7}
      mock_threadsafe.assert_called_once()

  @patch("api.asyncio.run_coroutine_threadsafe")
  def test_resolve_rejected_with_reason(self, mock_threadsafe):
      future = Future()
      future.set_result(None)
      mock_threadsafe.return_value = future
      mock_bot = MagicMock()
      mock_bot.loop = MagicMock()
      app = _make_dashboard_app()
      with patch("gpt.helpers.bot_instance", mock_bot):
          client = TestClient(app)
          response = client.post(
              f"/api/dashboard/{GUILD_ID}/verification/7/resolve",
              json={"outcome": "rejected", "reason": "Invalid receipt"},
          )
      assert response.status_code == 200
      assert response.json()["outcome"] == "rejected"

  def test_returns_503_when_bot_unavailable(self):
      app = _make_dashboard_app()
      with patch("gpt.helpers.bot_instance", None):
          client = TestClient(app)
          response = client.post(
              f"/api/dashboard/{GUILD_ID}/verification/7/resolve",
              json={"outcome": "approved"},
          )
      assert response.status_code == 503


# ---------------------------------------------------------------------------
# AutoMod cache invalidation
# ---------------------------------------------------------------------------


class TestAutomodInvalidateCache:
    def test_invalidate_cache_success(self):
        app = _make_dashboard_app()
        with patch.object(api_module, "_invalidate_automod_rules_cache") as mock_invalidate:
            client = TestClient(app)
            response = client.post(f"/api/dashboard/{GUILD_ID}/automod/invalidate-cache")
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_invalidate.assert_called_once_with(GUILD_ID)


# ---------------------------------------------------------------------------
# Dashboard discord meta
# ---------------------------------------------------------------------------


class TestDiscordMeta:
    @patch("api.asyncio.run_coroutine_threadsafe")
    def test_returns_channels_and_roles(self, mock_threadsafe):
        from api import DiscordMetaChannel, DiscordMetaResponse, DiscordMetaRole

        meta = DiscordMetaResponse(
            channels=[
                DiscordMetaChannel(id="111", name="general", type="text", parent_id=None),
                DiscordMetaChannel(id="222", name="tickets", type="category", parent_id=None),
            ],
            roles=[DiscordMetaRole(id="333", name="Staff", color=16711680, position=5)],
        )
        future = Future()
        future.set_result(meta)
        mock_threadsafe.return_value = future
        mock_bot = MagicMock()
        mock_bot.loop = MagicMock()
        app = _make_dashboard_app()
        with patch("gpt.helpers.bot_instance", mock_bot):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/discord-meta")
        assert response.status_code == 200
        data = response.json()
        assert len(data["channels"]) == 2
        assert data["channels"][0]["type"] == "text"
        assert len(data["roles"]) == 1
        assert data["roles"][0]["name"] == "Staff"
        mock_threadsafe.assert_called_once()

    def test_returns_503_when_bot_unavailable(self):
        app = _make_dashboard_app()
        with patch("gpt.helpers.bot_instance", None):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/discord-meta")
        assert response.status_code == 503

    @patch("api.asyncio.run_coroutine_threadsafe")
    def test_returns_400_when_guild_not_found(self, mock_threadsafe):
        future = Future()
        future.set_exception(RuntimeError("Guild not found"))
        mock_threadsafe.return_value = future
        mock_bot = MagicMock()
        mock_bot.loop = MagicMock()
        app = _make_dashboard_app()
        with patch("gpt.helpers.bot_instance", mock_bot):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(f"/api/dashboard/{GUILD_ID}/discord-meta")
        assert response.status_code == 400
        assert response.json()["detail"] == "Guild not found"
