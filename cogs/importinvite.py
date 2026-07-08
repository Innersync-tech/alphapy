"""One-shot invite leaderboard import (`!import_invites`). Not loaded in lifecycle."""

import re

import asyncpg
import discord
from discord.ext import commands

from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe
from utils.logger import logger


class ImportInvites(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("importinvite", bot=bot)

    async def setup_database(self):
        pool = await self._db_manager.ensure_pool()
        self.db = pool
        async with acquire_safe(pool) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS invite_tracker (
                    user_id BIGINT PRIMARY KEY,
                    invite_count INTEGER DEFAULT 0
                );
            ''')

    @commands.command(name="import_invites")
    @commands.is_owner()
    async def import_invites(self, ctx):
        """Import invites from the invite-tracker channel."""
        # Use guild-specific announcement channel setting
        try:
            announcement_channel_id = int(self.bot.settings.get("invites", "announcement_channel_id", ctx.guild.id))
            if announcement_channel_id == 0:
                await ctx.send("❌ Invite announcement channel not configured for this server. Set it first with `/invites set_channel #invites`.")
                return
        except (KeyError, ValueError):
            await ctx.send("❌ Invite announcement channel not configured for this server. Set it first with `/invites set_channel #invites`.")
            return

        channel = self.bot.get_channel(announcement_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await ctx.send("Invite announcement channel not found! Configure it first with `/invites set_channel` for this server.")
            return

        invite_counts = {}
        pattern_invited = re.compile(r'<@(\d+)> has been invited by <@(\d+)> and now has (\d+) invites?\.')
        pattern_joined = re.compile(r'<@(\d+)> joined! <@(\d+)> now has (\d+) invites?\.')


        logger.debug("Channel check: %s (ID: %s) for guild %s", channel, announcement_channel_id, ctx.guild.name)
        async for message in channel.history(limit=1000):  # Adjust limit if needed
            match = pattern_invited.search(message.content) or pattern_joined.search(message.content)
            logger.debug("Message found: %s", message.content)
            if match:
                logger.debug("Regex match: %s", match.groups())
                _, inviter_mention, count = match.groups()
                inviter = int(re.sub(r'[^\d]', '', inviter_mention))
                logger.debug("Found: user_id=%s now has %s invites", inviter, count)
                count = int(count)
                invite_counts[inviter] = max(invite_counts.get(inviter, 0), count)
            else:
                logger.debug("No regex match: %s", message.content)

        assert self.db is not None
        async with acquire_safe(self.db) as conn:
            for inviter, count in invite_counts.items():
                logger.debug("Saving: user_id=%s invite_count=%s", inviter, count)
                await conn.execute(
                    """
                    INSERT INTO invite_tracker (user_id, invite_count)
                    VALUES ($1, $2)
                    ON CONFLICT(user_id) DO UPDATE SET invite_count = $2;
                    """,
                    inviter, count
                )
                logger.debug("Imported: user_id=%s invite_count=%s", inviter, count)
        
        await ctx.send("✅ Import complete!")

async def setup(bot: commands.Bot):
    cog = ImportInvites(bot)
    await bot.add_cog(cog)
    await cog.setup_database()
