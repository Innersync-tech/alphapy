"""Tests for P3 shared-pool migration (DatabaseManager + GDPR helper)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.database_helpers import DatabaseManager
from utils.gdpr_helpers import store_gdpr_acceptance


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


def test_database_manager_bind_bot():
    bot = MagicMock()
    shared_pool = object()
    bot.settings._pool = shared_pool

    manager = DatabaseManager("test_cog")
    manager.bind_bot(bot)

    assert manager._pool is shared_pool


@pytest.mark.asyncio
async def test_database_manager_connection_and_execute_helpers():
    bot = MagicMock()
    conn = AsyncMock()
    conn.fetch.return_value = [{"id": 1}]
    conn.fetchval.return_value = 42
    bot.settings._pool = _mock_pool_with_conn(conn)

    manager = DatabaseManager("test_cog", bot=bot)

    async with manager.connection() as acquired:
        assert acquired is conn

    rows = await manager.execute_query("SELECT 1")
    assert rows == [{"id": 1}]

    value = await manager.execute_single("SELECT 2")
    assert value == 42


@pytest.mark.asyncio
async def test_faq_setup_db_uses_shared_pool():
    from cogs.faq import FAQ

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = FAQ(bot)
    await cog._setup_db()

    assert cog.db is bot.settings._pool
    assert conn.execute.call_count >= 1


@pytest.mark.asyncio
async def test_exports_setup_uses_shared_pool():
    from cogs.exports import Exports

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = Exports(bot)
    await cog._setup()

    assert cog.db is bot.settings._pool


@pytest.mark.asyncio
async def test_exports_setup_handles_missing_pool():
    from cogs.exports import Exports

    bot = MagicMock()
    bot.settings._pool = None

    cog = Exports(bot)
    await cog._setup()

    assert cog.db is None


@pytest.mark.asyncio
async def test_dataquery_setup_database_uses_shared_pool():
    from cogs.dataquery import DataQuery

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = DataQuery(bot)
    await cog.setup_database()

    assert cog.db is bot.settings._pool
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_delete_my_data_setup_uses_shared_pool():
    from cogs.delete_my_data import DeleteMyDataCog

    bot = MagicMock()
    bot.settings._pool = _mock_pool_with_conn(AsyncMock())

    cog = DeleteMyDataCog(bot)
    await cog._setup()

    assert cog.db is bot.settings._pool


@pytest.mark.asyncio
async def test_premium_connect_and_terms_acceptance():
    from cogs.premium import PremiumCog, _save_terms_acceptance

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = PremiumCog(bot)
    await cog._connect_database()
    assert cog.db is bot.settings._pool

    await _save_terms_acceptance(cog, 99, 99)
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_onboarding_connect_pool_uses_shared_pool():
    from cogs.onboarding import Onboarding

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = Onboarding(bot)
    await cog._connect_pool()

    assert cog.db is bot.settings._pool
    assert conn.execute.call_count >= 1


@pytest.mark.asyncio
async def test_inviteboard_setup_database_uses_shared_pool():
    from cogs.inviteboard import InviteTracker

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = InviteTracker(bot)
    await cog.setup_database()

    assert cog.db is bot.settings._pool
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_verification_setup_db_uses_shared_pool():
    from cogs.verification import VerificationCog

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = VerificationCog(bot)
    await cog.setup_db()

    assert cog.db is bot.settings._pool
    conn.execute.assert_awaited()


@pytest.mark.asyncio
async def test_retention_cleanup_setup_uses_shared_pool():
    from cogs.retention_cleanup import RetentionCleanupCog

    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    cog = RetentionCleanupCog(bot)
    await cog._setup()

    assert cog.db is bot.settings._pool


@pytest.mark.asyncio
async def test_store_gdpr_acceptance_no_pool_is_noop():
    bot = MagicMock()
    bot.settings._pool = None

    await store_gdpr_acceptance(1, 2, bot)


@pytest.mark.asyncio
async def test_database_manager_pool_none_without_bot():
    manager = DatabaseManager("orphan")
    assert manager._pool is None


@pytest.mark.asyncio
async def test_store_gdpr_acceptance_writes_via_shared_pool():
    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    await store_gdpr_acceptance(123, 456, bot)

    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    assert args[1] == 123
    assert args[2] == 456

    bot = MagicMock()
    conn = AsyncMock()
    bot.settings._pool = _mock_pool_with_conn(conn)

    await store_gdpr_acceptance(123, 456, bot)

    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    assert args[1] == 123
    assert args[2] == 456
