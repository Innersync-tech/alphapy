"""Unit tests for Innersync identity resolution and link upsert (diff coverage)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import innersync_identity as ii


def _pool_with_conn(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.is_closing.return_value = False

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *args):
            return None

    pool.acquire.return_value = _Acq()
    return pool


@pytest.mark.asyncio
async def test_get_discord_id_invalid_uuid_returns_none() -> None:
    assert await ii.get_discord_id_for_innersync(MagicMock(), "not-a-uuid") is None


@pytest.mark.asyncio
async def test_get_discord_id_from_db_row() -> None:
    iu = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"discord_user_id": 424242})
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    out = await ii.get_discord_id_for_innersync(pool, iu)
    assert out == 424242
    conn.fetchrow.assert_awaited()


@pytest.mark.asyncio
async def test_get_innersync_id_from_db_row() -> None:
    conn = AsyncMock()
    iu = str(uuid.uuid4())
    conn.fetchrow = AsyncMock(return_value={"iid": iu})
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    out = await ii.get_innersync_id_for_discord(pool, 999001)
    assert out.lower() == iu.lower()


@pytest.mark.asyncio
async def test_get_discord_fallback_supabase() -> None:
    iu = str(uuid.uuid4())
    with patch(
        "utils.supabase_client.get_discord_id_for_user",
        new=AsyncMock(return_value="888001"),
    ):
        out = await ii.get_discord_id_for_innersync(
            None, iu, allow_profile_fallback=True
        )
    assert out == 888001


@pytest.mark.asyncio
async def test_upsert_discord_link_noop_same_pair() -> None:
    iu = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"iu": iu})
    conn.execute = AsyncMock()
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id=iu, discord_user_id=1001, link_source="webhook"
    )
    assert status == "noop"
    assert err is None
    conn.execute.assert_not_called()
    assert conn.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_upsert_discord_link_noop_via_innersync_row() -> None:
    iu = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"discord_user_id": 5005},
        ]
    )
    conn.execute = AsyncMock()
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id=iu, discord_user_id=5005, link_source="command"
    )
    assert status == "noop"
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_discord_link_conflict_discord_taken() -> None:
    iu = str(uuid.uuid4())
    other = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"iu": other},
            None,
        ]
    )
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id=iu, discord_user_id=2002, link_source="magic_link"
    )
    assert status == "conflict"
    assert err is not None


@pytest.mark.asyncio
async def test_upsert_discord_link_conflict_innersync_taken() -> None:
    iu = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"discord_user_id": 9999},
        ]
    )
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id=iu, discord_user_id=6006, link_source="webhook"
    )
    assert status == "conflict"
    assert "already linked" in (err or "").lower()


@pytest.mark.asyncio
async def test_upsert_invalid_uuid_returns_conflict() -> None:
    pool = _pool_with_conn(AsyncMock())
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id="not-a-uuid", discord_user_id=1, link_source="x"
    )
    assert status == "conflict"
    assert err is not None


@pytest.mark.asyncio
async def test_upsert_discord_link_inserts() -> None:
    iu = str(uuid.uuid4())
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[None, None])
    conn.execute = AsyncMock()
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    status, err = await ii.upsert_discord_link(
        pool, innersync_user_id=iu, discord_user_id=3003, link_source="otp"
    )
    assert status == "ok"
    assert err is None
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_delete_discord_link_parses_execute_result() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    pool = _pool_with_conn(conn)
    ii.invalidate_identity_cache()
    assert await ii.delete_discord_link_for_discord_user(pool, 4004) is True


@pytest.mark.asyncio
async def test_resolve_innersync_jwt_sub_delegates() -> None:
    pool = MagicMock()
    with patch.object(ii, "get_discord_id_for_innersync", new=AsyncMock(return_value=55)) as m:
        out = await ii.resolve_innersync_jwt_sub_to_discord_int(pool, str(uuid.uuid4()))
    assert out == 55
    m.assert_awaited()
