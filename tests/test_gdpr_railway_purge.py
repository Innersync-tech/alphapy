from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.gdpr_railway_purge import purge_railway_user_data


def _pool_with_conn() -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)
    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_purge_railway_blanks_reminder_message() -> None:
    pool, conn = _pool_with_conn()
    await purge_railway_user_data(pool, 42, log_prefix="delete_my_data")
    sqls = [call.args[0] for call in conn.execute.await_args_list]
    reminder_sql = next(s for s in sqls if "UPDATE reminders" in s)
    assert "message = NULL" in reminder_sql
    assert "created_by = NULL" in reminder_sql
    custom_sql = next(s for s in sqls if "UPDATE custom_commands" in s)
    assert "created_by = NULL" in custom_sql
    assert "response" not in custom_sql
    assert any("alphapy_discord_links" in s for s in sqls)
    assert any("app_reflections" in s for s in sqls)
    assert all("plaintext_content" not in s for s in sqls)


@pytest.mark.asyncio
async def test_delete_my_data_uses_shared_railway_purge(monkeypatch) -> None:
    from cogs.delete_my_data import _purge_user_data

    railway = AsyncMock()
    monkeypatch.setattr("cogs.delete_my_data.purge_railway_user_data", railway)
    monkeypatch.setattr(
        "cogs.delete_my_data.get_innersync_id_for_discord",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agents.memory.purge_agent_user_data",
        AsyncMock(),
    )
    pool = MagicMock()
    await _purge_user_data(pool, 99)
    railway.assert_awaited_once()
    assert railway.await_args.args[0] is pool
    assert railway.await_args.args[1] == 99


@pytest.mark.asyncio
async def test_supabase_webhook_uses_shared_railway_purge() -> None:
    from webhooks.supabase import _purge_railway_data

    pool = MagicMock()
    with patch(
        "webhooks.supabase.purge_railway_user_data",
        new=AsyncMock(),
    ) as railway:
        await _purge_railway_data(pool, 77, "user-uuid")
    railway.assert_awaited_once_with(pool, 77, log_prefix="GDPR purge")
