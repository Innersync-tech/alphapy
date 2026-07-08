"""Automated data retention cleanup for GDPR compliance.

Runs a daily background task that deletes analytics records older than 90 days:
- audit_logs: command usage tracking
- faq_search_logs: FAQ search analytics

90 days is a defensible retention period under Belgian DPA guidance for
operational analytics data that is not required for the primary service.
"""


import asyncpg
from discord.ext import commands

from utils.background_tasks import BackgroundTask
from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe
from utils.logger import logger

_RETENTION_DAYS = 90
_INTERVAL_SECONDS = 86400  # 24 hours


class RetentionCleanupCog(commands.Cog):
    """Background cog that enforces 90-day retention on analytics tables."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("retention_cleanup", bot=bot)
        self._task: BackgroundTask | None = None
        self.bot.loop.create_task(self._setup())

    async def _setup(self) -> None:
        await self.bot.wait_until_ready()
        try:
            self.db = await self._db_manager.ensure_pool()
        except Exception as exc:
            logger.error("RetentionCleanup: DB pool creation error: %s", exc)
            return

        self._task = BackgroundTask(
            name="RetentionCleanup",
            interval=_INTERVAL_SECONDS,
            task_func=self._run_cleanup,
            pool=self.db,
        )
        await self._task.start()

    async def cog_unload(self) -> None:
        if self._task:
            await self._task.stop()
        self.db = None

    async def _run_cleanup(self) -> None:
        """Delete analytics rows older than RETENTION_DAYS from audit and search tables."""
        async with acquire_safe(self.db) as conn:
            result = await conn.execute(
                "DELETE FROM audit_logs WHERE created_at < NOW() - make_interval(days => $1)",
                _RETENTION_DAYS,
            )
            logger.info("RetentionCleanup: %s audit_logs rows deleted (>%dd)", result, _RETENTION_DAYS)

            result = await conn.execute(
                "DELETE FROM faq_search_logs WHERE created_at < NOW() - make_interval(days => $1)",
                _RETENTION_DAYS,
            )
            logger.info("RetentionCleanup: %s faq_search_logs rows deleted (>%dd)", result, _RETENTION_DAYS)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RetentionCleanupCog(bot))
