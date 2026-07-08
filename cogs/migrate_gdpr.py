"""One-shot SQLite → Postgres GDPR migration (`!migrate_gdpr`). Not loaded in lifecycle."""

import sqlite3
from datetime import datetime

import asyncpg
from discord.ext import commands

import config
from utils.logger import logger


class GDPRMigration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def setup_database(self):
        """Creates the GDPR table if it does not exist."""
        pg_conn = await asyncpg.connect(config.DATABASE_URL)

        await pg_conn.execute("""
            CREATE TABLE IF NOT EXISTS gdpr_acceptance (
                user_id BIGINT PRIMARY KEY,
                accepted INTEGER DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await pg_conn.close()
        logger.info("GDPR table checked/created")


    @commands.command(name="migrate_gdpr")
    @commands.is_owner()
    async def migrate_gdpr(self, ctx):
        """Migrates GDPR data from SQLite to PostgreSQL."""
        await ctx.send("📦 GDPR migration started... This may take a moment!")
        await self.setup_database()

        # ✅ Step 1: Connect to SQLite
        sqlite_conn = sqlite3.connect("onboarding.db")
        sqlite_cursor = sqlite_conn.cursor()

        # ✅ Step 2: Fetch GDPR data
        sqlite_cursor.execute("SELECT user_id, accepted, timestamp FROM gdpr_acceptance")
        data = sqlite_cursor.fetchall()

        if not data:
            await ctx.send("❌ No GDPR data found in SQLite.")
            return

        await ctx.send(f"📦 {len(data)} GDPR records found, starting migration...")

        # ✅ Step 3: Connect to PostgreSQL
        pg_conn = await asyncpg.connect(config.DATABASE_URL)

        for user_id, accepted, timestamp in data:
            timestamp = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            # ✅ Step 4: Insert GDPR data into PostgreSQL
            await pg_conn.execute(
                """
                INSERT INTO gdpr_acceptance (user_id, accepted, timestamp)
                VALUES ($1, $2, $3)
                ON CONFLICT(user_id) DO UPDATE SET accepted = $2, timestamp = $3;
                """,
                int(user_id), accepted, timestamp
            )

        # ✅ Step 5: Close connections
        await pg_conn.close()
        sqlite_conn.close()

        await ctx.send("✅ GDPR migration complete! 🎉")

async def setup(bot):
    await bot.add_cog(GDPRMigration(bot))
