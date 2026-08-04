"""Sprint 3b guild dashboard CRUD endpoint tests."""

from datetime import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api as api_module
from tests.test_api_endpoints import (
    DISCORD_USER_ID,
    GUILD_ID,
    _fake_record,
    _make_dashboard_app,
    _mock_pool,
)


def _reminder_row(**kwargs):
    defaults = {
        "id": 42,
        "guild_id": GUILD_ID,
        "name": "Daily stand-up",
        "channel_id": 123,
        "time": time(9, 0),
        "call_time": time(10, 0),
        "days": ["0", "1", "2"],
        "message": "Stand-up time",
        "created_by": DISCORD_USER_ID,
        "location": None,
        "event_time": None,
        "scheduled_time": None,
        "image_url": None,
        "completed": False,
        "last_sent_at": None,
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return _fake_record(**defaults)


def _command_row(**kwargs):
    defaults = {
        "id": 7,
        "guild_id": GUILD_ID,
        "name": "hello",
        "trigger_type": "exact",
        "trigger_value": "!hello",
        "response": "Hi {user}",
        "enabled": True,
        "case_sensitive": False,
        "delete_trigger": False,
        "reply_to_user": True,
        "uses": 3,
        "created_by": DISCORD_USER_ID,
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return _fake_record(**defaults)


class TestGuildRemindersDashboard:
    def test_list_reminders(self):
        pool, conn = _mock_pool(_reminder_row())
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/reminders")
        assert response.status_code == 200
        data = response.json()
        assert len(data["reminders"]) == 1
        assert data["reminders"][0]["id"] == 42
        assert data["reminders"][0]["name"] == "Daily stand-up"

    def test_create_reminder_requires_time(self):
        pool, _conn = _mock_pool()
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/reminders",
                json={"message": "hi", "channel_id": "123"},
            )
        assert response.status_code == 400

    def test_create_reminder(self):
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=_reminder_row(id=99))
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/reminders",
                json={
                    "message": "hi",
                    "channel_id": "123",
                    "time": "10:00",
                    "days": ["0", "2"],
                    "name": "Ping",
                },
            )
        assert response.status_code == 200
        assert response.json()["reminderId"] == 99

    def test_delete_reminder_not_found(self):
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.delete(f"/api/dashboard/{GUILD_ID}/reminders/404")
        assert response.status_code == 404

    def test_live_session_create(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        conn.fetchrow = AsyncMock(return_value=_fake_record(id=55))
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/reminders/live-sessions",
                json={"time": "19:30", "channel_id": "999", "days": [0, 2, 4]},
            )
        assert response.status_code == 200
        assert response.json()["reminderId"] == 55


class TestGuildEngagementDashboard:
    def test_engagement_stats_shape(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(side_effect=[
            [],  # active
            [],  # history
            [_fake_record(count="2")],
            [],
            [_fake_record(count="1")],
            [_fake_record(count="4", max_streak="12")],
            [],
        ])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/engagement")
        assert response.status_code == 200
        data = response.json()
        assert data["og_claims_count"] == 2
        assert data["badges_count"] == 1
        assert data["streaks_count"] == 4
        assert data["max_streak"] == 12
        assert data["active_challenges"] == []


class TestGuildCustomCommandsDashboard:
    def test_list_commands(self):
        pool, conn = _mock_pool(_command_row())
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/custom-commands")
        assert response.status_code == 200
        assert response.json()["commands"][0]["name"] == "hello"

    def test_create_command(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value=1)
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_command_row())
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/custom-commands",
                json={
                    "name": "hello",
                    "trigger_type": "exact",
                    "trigger_value": "!hello",
                    "response": "Hi",
                },
            )
        assert response.status_code == 201
        assert response.json()["command"]["name"] == "hello"

    def test_delete_command_not_found(self):
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.delete(f"/api/dashboard/{GUILD_ID}/custom-commands/missing")
        assert response.status_code == 404
