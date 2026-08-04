"""Sprint 3b guild dashboard CRUD endpoint tests."""

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import api as api_module
from tests.test_api_endpoints import (
    DISCORD_USER_ID,
    GUILD_ID,
    _fake_record,
    _make_dashboard_app,
    _mock_pool,
)

BRUSSELS_TZ = ZoneInfo("Europe/Brussels")


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
        conn.fetchval = AsyncMock(return_value="60")
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
        # Event 10:00 with 60m offset → reminder fire at 09:00
        args = conn.fetchrow.await_args.args
        assert args[4] == time(9, 0)
        assert args[5] == time(10, 0)

    def test_create_recurring_requires_days(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/reminders",
                json={"message": "hi", "channel_id": "123", "time": "10:00"},
            )
        assert response.status_code == 400

    def test_create_one_off_sets_time_and_call_time(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        conn.fetchrow = AsyncMock(return_value=_reminder_row(id=11))
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/reminders",
                json={
                    "message": "once",
                    "channel_id": "123",
                    "scheduled_time": "2026-08-05T14:30:00+02:00",
                },
            )
        assert response.status_code == 200
        args = conn.fetchrow.await_args.args
        assert args[4] == time(13, 30)
        assert args[5] == time(14, 30)
        assert args[9] is not None

    def test_clear_scheduled_time_also_clears_days(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        existing = _fake_record(
            event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        )
        updated = _reminder_row(event_time=None, days=[])
        conn.fetchrow = AsyncMock(side_effect=[existing, updated])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"scheduled_time": ""},
            )
        assert response.status_code == 200
        update_sql = conn.fetchrow.await_args_list[1].args[0]
        assert "days" in update_sql
        assert [] in conn.fetchrow.await_args_list[1].args

    def test_empty_schedule_on_recurring_preserves_days(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        existing = _fake_record(event_time=None)
        updated = _reminder_row(event_time=None, days=["0", "1"])
        conn.fetchrow = AsyncMock(side_effect=[existing, updated])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"scheduled_time": ""},
            )
        assert response.status_code == 200
        update_args = conn.fetchrow.await_args_list[1].args
        assert "days" not in update_args[0]

    def test_one_off_scheduled_time_clears_days(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        existing = _fake_record(event_time=None)
        updated = _reminder_row(days=[])
        conn.fetchrow = AsyncMock(side_effect=[existing, updated])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"scheduled_time": "2026-08-05T14:30:00+02:00"},
            )
        assert response.status_code == 200
        update_sql = conn.fetchrow.await_args_list[1].args[0]
        assert "days" in update_sql
        assert [] in conn.fetchrow.await_args_list[1].args

    def test_one_off_call_time_syncs_event_time(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        existing = _fake_record(
            event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        )
        updated = _reminder_row()
        conn.fetchrow = AsyncMock(side_effect=[existing, updated])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"call_time": "16:45"},
            )
        assert response.status_code == 200
        update_sql = conn.fetchrow.await_args_list[1].args[0]
        assert "event_time" in update_sql
        synced = next(
            arg for arg in conn.fetchrow.await_args_list[1].args[1:]
            if isinstance(arg, datetime)
        )
        local = synced.astimezone(BRUSSELS_TZ) if synced.tzinfo else synced
        assert (local.hour, local.minute) == (16, 45)

    def test_reject_completed_on_recurring(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        conn.fetchrow = AsyncMock(return_value=_fake_record(event_time=None))
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"completed": True},
            )
        assert response.status_code == 400
        assert "one-off" in response.json()["detail"]

    def test_completed_with_clear_schedule_on_one_off(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(return_value="60")
        existing = _fake_record(
            event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        )
        updated = _reminder_row(event_time=None, completed=True, days=[])
        conn.fetchrow = AsyncMock(side_effect=[existing, updated])
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/reminders/42",
                json={"completed": True, "scheduled_time": ""},
            )
        assert response.status_code == 200
        assert response.json()["success"] is True

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
        ])
        conn.fetchrow = AsyncMock(return_value=None)  # no weekly awards
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
        assert data["latest_weekly"] is None

    def test_engagement_latest_weekly_all_results(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(side_effect=[
            [],
            [],
            [_fake_record(count="0")],
            [],
            [_fake_record(count="0")],
            [_fake_record(count="0", max_streak=None)],
            [
                _fake_record(award_key=f"award_{i}", user_id=1000 + i, metric=i)
                for i in range(25)
            ],
        ])
        conn.fetchrow = AsyncMock(
            return_value=_fake_record(
                id=9,
                week_start=date(2026, 7, 28),
                week_end=date(2026, 8, 3),
            )
        )
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.get(f"/api/dashboard/{GUILD_ID}/engagement")
        assert response.status_code == 200
        latest = response.json()["latest_weekly"]
        assert latest["id"] == 9
        assert len(latest["results"]) == 25


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
        conn.fetchval = AsyncMock(side_effect=[1, None])  # count, existing name
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_command_row())
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.post(
                f"/api/dashboard/{GUILD_ID}/custom-commands",
                json={
                    "name": "Hello",
                    "trigger_type": "exact",
                    "trigger_value": "!hello",
                    "response": "Hi",
                },
            )
        assert response.status_code == 201
        assert response.json()["command"]["name"] == "hello"
        # Stored lowercase to match Discord /cc add
        assert conn.execute.await_args.args[1] == GUILD_ID
        assert conn.execute.await_args.args[2] == "hello"
        # Default reply_to_user matches DB/cog default
        assert conn.execute.await_args.args[8] is True

    def test_create_duplicate_command_returns_400(self):
        pool, conn = _mock_pool()
        conn.fetchval = AsyncMock(side_effect=[1, 7])  # count, existing id
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
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_update_regex_type_validates_existing_value(self):
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(
            side_effect=[
                _fake_record(trigger_type="exact", trigger_value="(unclosed"),
            ]
        )
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.put(
                f"/api/dashboard/{GUILD_ID}/custom-commands/hello",
                json={"trigger_type": "regex"},
            )
        assert response.status_code == 400
        assert "Invalid regex" in response.json()["detail"]

    def test_delete_command_not_found(self):
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        app = _make_dashboard_app()
        with patch.object(api_module, "db_pool", pool):
            client = TestClient(app)
            response = client.delete(f"/api/dashboard/{GUILD_ID}/custom-commands/missing")
        assert response.status_code == 404
