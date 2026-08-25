"""Coverage for ballast-cut schema fail-loud checks and local dataset loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


def _mock_pool_with_conn(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.acquire.return_value = _AcquireCtx(conn)
    return pool


@pytest.mark.asyncio
async def test_reminder_connect_database_checks_reminders_table():
    from cogs.reminders import ReminderCog

    bot = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    pool = _mock_pool_with_conn(conn)
    bot.settings._pool = pool

    cog = ReminderCog(bot)
    with patch("cogs.reminders.get_bot_db_pool", return_value=pool), patch(
        "cogs.reminders.acquire_safe", side_effect=lambda _pool: _AcquireCtx(conn)
    ):
        await cog._connect_database()

    assert cog.db is pool
    conn.fetchval.assert_awaited()
    assert "reminders" in conn.fetchval.await_args.args[0]


@pytest.mark.asyncio
async def test_reminder_connect_database_raises_when_table_missing():
    from cogs.reminders import ReminderCog

    bot = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("relation reminders does not exist"))
    pool = _mock_pool_with_conn(conn)

    cog = ReminderCog(bot)
    with patch("cogs.reminders.get_bot_db_pool", return_value=pool), patch(
        "cogs.reminders.acquire_safe", side_effect=lambda _pool: _AcquireCtx(conn)
    ), pytest.raises(RuntimeError, match="reminders"):
        await cog._connect_database()


@pytest.mark.asyncio
async def test_ticketbot_setup_db_checks_core_tables():
    from cogs.ticketbot import TicketBot

    bot = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    pool = _mock_pool_with_conn(conn)

    cog = TicketBot(bot)
    with patch("cogs.ticketbot.get_bot_db_pool", return_value=pool), patch(
        "cogs.ticketbot.acquire_safe", side_effect=lambda _pool: _AcquireCtx(conn)
    ):
        await cog.setup_db()

    assert cog.db is pool
    assert conn.fetchval.await_count == 2


@pytest.mark.asyncio
async def test_ticketbot_setup_db_raises_when_tables_missing():
    from cogs.ticketbot import TicketBot

    bot = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("missing table"))
    pool = _mock_pool_with_conn(conn)

    cog = TicketBot(bot)
    with patch("cogs.ticketbot.get_bot_db_pool", return_value=pool), patch(
        "cogs.ticketbot.acquire_safe", side_effect=lambda _pool: _AcquireCtx(conn)
    ), pytest.raises(RuntimeError, match="missing table"):
        await cog.setup_db()
    assert cog.db is None


@pytest.mark.asyncio
async def test_load_topic_context_reads_local_md(tmp_path: Path, monkeypatch):
    from gpt import dataset_loader

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "rsi.md").write_text("RSI context", encoding="utf-8")
    monkeypatch.setattr(dataset_loader, "BASE_PATH", str(prompts))

    assert await dataset_loader.load_topic_context("RSI") == "RSI context"
    assert await dataset_loader.load_topic_context("") == ""
    assert await dataset_loader.load_topic_context("missing topic") == ""
