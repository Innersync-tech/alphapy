"""
Database helper utilities for common database operations.

DatabaseManager no longer creates per-cog pools. It resolves the bot's shared
SettingsService pool via get_bot_db_pool() (see utils/db_helpers.py).
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from discord.ext import commands

from .db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy


class DatabaseManager:
    """Thin accessor for the shared bot database pool (legacy name retained)."""

    def __init__(
        self,
        pool_name: str,
        config_dict: dict[str, Any] | None = None,
        *,
        bot: commands.Bot | None = None,
    ):
        self.pool_name = pool_name
        self._bot = bot

    def bind_bot(self, bot: commands.Bot) -> None:
        """Attach bot after cog init when the manager was created before super().__init__."""
        self._bot = bot

    def _shared_pool(self) -> asyncpg.Pool | None:
        if self._bot is None:
            return None
        return get_bot_db_pool(self._bot)

    @property
    def _pool(self) -> asyncpg.Pool | None:
        """Compatibility for legacy `if manager._pool` checks (read-only)."""
        return self._shared_pool()

    async def ensure_pool(self) -> asyncpg.Pool:
        pool = self._shared_pool()
        if not is_pool_healthy(pool):
            raise RuntimeError(
                f"Shared database pool not available for '{self.pool_name}'"
            )
        return pool

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[asyncpg.Connection, None]:
        pool = await self.ensure_pool()
        async with acquire_safe(pool) as conn:
            yield conn

    async def execute_query(self, query: str, *args: Any):
        async with self.connection() as conn:
            return await conn.fetch(query, *args)

    async def execute_single(self, query: str, *args: Any):
        async with self.connection() as conn:
            return await conn.fetchval(query, *args)
