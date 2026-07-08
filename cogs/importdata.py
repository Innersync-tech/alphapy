"""One-shot onboarding embed import (`!import_onboarding`). Not loaded in lifecycle."""

import json

import asyncpg
import discord
from discord.ext import commands

from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe
from utils.logger import logger


class ImportData(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("importdata", bot=bot)

    async def setup_database(self):
        pool = await self._db_manager.ensure_pool()
        self.db = pool
        async with acquire_safe(pool) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS onboarding (
                    user_id BIGINT PRIMARY KEY,
                    responses JSONB,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')

    @commands.command(name="import_onboarding")
    @commands.is_owner()
    async def import_onboarding(self, ctx):
        """Import onboarding data from embed messages in the log channel."""
        # Use guild-specific log channel setting
        try:
            log_channel_id = int(self.bot.settings.get("system", "log_channel_id", ctx.guild.id))
            if log_channel_id == 0:
                await ctx.send("❌ Log channel not configured for this server. Set it first with `/system set_log_channel #logs`.")
                return
        except (KeyError, ValueError):
            await ctx.send("❌ Log channel not configured for this server. Set it first with `/system set_log_channel #logs`.")
            return

        channel = self.bot.get_channel(log_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await ctx.send("Log channel not found! Configure it first with `/system set_log_channel` for this server.")
            return

        async for message in channel.history(limit=1000):  # Adjust limit if needed
            if not message.embeds:
                continue

            embed = message.embeds[0]
            description = embed.description or ""
            if "(" not in description or ")" not in description:
                continue

            try:
                user_field = description.split("(")[1].split(")")[0]
            except IndexError:
                continue

            user_id = int(user_field) if user_field.isdigit() else None

            if not user_id:
                continue

            responses = {}
            for field in embed.fields:
                question_raw = field.name or ""
                answer_raw = field.value or ""
                question = question_raw.strip()
                answer = answer_raw.strip()
                responses[question] = answer

            assert self.db is not None
            async with acquire_safe(self.db) as conn:
                await conn.execute(
                    """
                    INSERT INTO onboarding (user_id, responses)
                    VALUES ($1, $2)
                    ON CONFLICT(user_id) DO UPDATE SET responses = $2;
                    """,
                    user_id, json.dumps(responses)
                )
            logger.debug("Imported user_id=%s", user_id)
        
        await ctx.send("✅ Import complete!")

async def setup(bot: commands.Bot):
    cog = ImportData(bot)
    await bot.add_cog(cog)
    await cog.setup_database()
