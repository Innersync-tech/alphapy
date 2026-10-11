from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from utils import app_reflection_purge as purge


def _pool_with_delete_rows(user_ids: list[int]) -> MagicMock:
    rows = [{"user_id": uid} for uid in user_ids]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool._conn = conn
    return pool


@pytest.mark.asyncio
async def test_delete_app_reflection_rows_uses_reflection_id_only() -> None:
    pool = _pool_with_delete_rows([111, 222])
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="ref-1")
    assert count == 2
    assert user_ids == [111, 222]
    sql = pool._conn.fetch.await_args.args[0]
    assert "DELETE FROM app_reflections" in sql
    assert "plaintext_content" not in sql
    assert pool._conn.fetch.await_args.args[1] == "ref-1"


@pytest.mark.asyncio
async def test_delete_app_reflection_rows_empty_id() -> None:
    pool = MagicMock()
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="  ")
    assert count == 0
    assert user_ids == []
    pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_purge_app_reflection_copy_deletes_and_purges_tier2(monkeypatch) -> None:
    pool = _pool_with_delete_rows([42])
    monkeypatch.setattr(
        purge,
        "get_innersync_id_for_discord",
        AsyncMock(return_value="user-uuid"),
    )
    tier2 = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", tier2)
    forwarded: list[dict] = []
    monkeypatch.setattr(
        "utils.dashboard_webhooks.forward_revoke_reflection",
        lambda payload: forwarded.append(payload),
    )

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=42,
    )
    assert result["deleted"] == 1
    assert result["tier2_purged"] == 1
    tier2.assert_awaited_once_with("user-uuid", "reflection", "ref-1")
    assert forwarded == [{"user_id": 42, "reflection_id": "ref-1"}]


@pytest.mark.asyncio
async def test_delete_skips_unparseable_user_ids() -> None:
    pool = _pool_with_delete_rows([])
    pool._conn.fetch = AsyncMock(return_value=[{"user_id": "not-an-int"}, {"user_id": 9}])
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="ref-1")
    assert count == 2
    assert user_ids == [9]


@pytest.mark.asyncio
async def test_purge_uses_explicit_innersync_id_and_swallows_tier2_errors(monkeypatch) -> None:
    pool = _pool_with_delete_rows([])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "agents.memory.purge_tier2_for_reflection",
        AsyncMock(side_effect=RuntimeError("memory down")),
    )
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=55,
        innersync_user_id="user-uuid",
    )
    assert result["deleted"] == 0
    assert result["tier2_purged"] == 0
    assert 55 in result["user_ids"]


@pytest.mark.asyncio
async def test_purge_app_reflection_copy_is_idempotent_when_missing(monkeypatch) -> None:
    pool = _pool_with_delete_rows([])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value=None))
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", AsyncMock())
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(pool, reflection_id="gone")
    assert result["deleted"] == 0
    assert result["tier2_purged"] == 0
