import csv
import os

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.validators import requires_owner


class DataQuery(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("dataquery", bot=bot)

    async def setup_database(self):
        """Create database connection and table if not present."""
        pool = get_bot_db_pool(self.bot)
        if not is_pool_healthy(pool):
            raise RuntimeError("Shared database pool not available")
        self.db = pool
        async with acquire_safe(self.db) as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS onboarding (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    responses JSONB,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    async def cog_load(self):
        """Runs automatically when the cog is loaded."""
        await self.setup_database()

    @app_commands.command(name="export_onboarding", description="Export onboarding data as CSV (owner only).")
    @requires_owner()
    async def export_onboarding(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        csv_filename = "onboarding_data.csv"
        
        if not is_pool_healthy(self.db):
            await interaction.followup.send("Database not available.", ephemeral=True)
            return
        async with acquire_safe(self.db) as connection:
            rows = await connection.fetch("SELECT * FROM onboarding")

        if not rows:
            await interaction.followup.send("No onboarding data found.", ephemeral=True)
            return

        columns = ["id", "user_id", "responses", "timestamp"]
        with open(csv_filename, "w", newline="", encoding="utf-8") as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(columns)
            csv_writer.writerows([list(row) for row in rows])

        file = discord.File(csv_filename)
        await interaction.followup.send("Exported onboarding data:", file=file, ephemeral=True)
        os.remove(csv_filename)

async def setup(bot: commands.Bot):
    await bot.add_cog(DataQuery(bot))
    # Command sync is now handled centrally in bot.py on_ready() hook
    # This prevents blocking startup and respects rate limits
