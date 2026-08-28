"""Regression: 24h audit_logs count must not bind unused $1 when filtering by guild."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api as api_module


@pytest.mark.asyncio
async def test_fetch_command_stats_24h_count_uses_dollar1_for_guild():
    """Guild filter must use $1 (not $2 with a dummy unused $1 param)."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=3)

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = MagicMock()
    mock_pool.acquire = _acquire

    api_module._command_stats_cache.clear()
    with patch.object(api_module, "db_pool", mock_pool):
        result = await api_module._fetch_command_stats(guild_id=42, days=7, limit=10)

    assert result is not None
    assert result.total_commands_24h == 3

    fetchval_sql, *fetchval_args = mock_conn.fetchval.call_args[0]
    assert "AND guild_id = $1" in fetchval_sql
    assert "$2" not in fetchval_sql
    assert fetchval_args == [42]


@pytest.mark.asyncio
async def test_fetch_command_stats_24h_count_no_guild_has_no_params():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=0)

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool = MagicMock()
    mock_pool.acquire = _acquire

    api_module._command_stats_cache.clear()
    with patch.object(api_module, "db_pool", mock_pool):
        result = await api_module._fetch_command_stats(guild_id=None, days=7, limit=10)

    assert result is not None
    fetchval_sql, *fetchval_args = mock_conn.fetchval.call_args[0]
    assert "guild_id" not in fetchval_sql
    assert fetchval_args == []
