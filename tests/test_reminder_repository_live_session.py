"""Tests for live-session repository helpers."""

from datetime import time
from typing import Any

import pytest

from utils import reminder_repository as repo


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *params: Any) -> None:
        self.executed.append((query.strip(), params))


@pytest.mark.asyncio
async def test_update_live_session_sets_fixed_name_and_message():
    conn = FakeConn()
    await repo.update_live_session(
        conn,
        guild_id=1,
        reminder_id=42,
        reminder_time=time(18, 30),
        call_time=time(19, 30),
        days=["0", "2"],
        channel_id=99,
        image_url="https://example.com/banner.png",
    )
    assert len(conn.executed) == 1
    _query, params = conn.executed[0]
    assert "UPDATE reminders" in _query
    assert params[-4] == "Live session"
    assert params[-3] == "Live session starting now!"
    assert params[-2] == 42
    assert params[-1] == 1


@pytest.mark.asyncio
async def test_delete_for_user_scoped_by_creator():
    conn = FakeConn()
    await repo.delete_for_user(conn, guild_id=5, reminder_id=7, user_id=12345)
    assert len(conn.executed) == 1
    _query, params = conn.executed[0]
    assert "DELETE FROM reminders" in _query
    assert params == (7, 5, 12345)
