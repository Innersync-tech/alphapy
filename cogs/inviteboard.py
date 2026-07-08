import asyncio

import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

import config
from utils.cog_base import AlphaCog
from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import logger


class InviteTracker(AlphaCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.invites_cache = {}
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("inviteboard", bot=bot)
        
    @commands.Cog.listener()
    async def on_ready(self):
        """Load existing invites on bot start."""
        await self.bot.wait_until_ready()  # Wait until the bot is ready
        for guild in self.bot.guilds:
            try:
                self.invites_cache[guild.id] = await guild.invites()
            except Exception as exc:
                logger.warning(f"InviteTracker: could not load invites for guild {guild.id}: {exc}")
        logger.info("Invite cache loaded.")

    async def setup_database(self):
        """Initialize PostgreSQL database and create tables if needed."""
        try:
            pool = await self._db_manager.ensure_pool()
            self.db = pool
            async with acquire_safe(pool) as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS invite_tracker (
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        invite_count INTEGER DEFAULT 0,
                        PRIMARY KEY(guild_id, user_id)
                    );
                ''')
        except Exception as e:
            logger.error(f"InviteTracker: DB pool creation error: {e}")
            self.db = None

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        self.db = None

    async def update_invite_count(self, guild_id: int, inviter_id: int, count: int | None = None):
        """Increment or set the invite count in the database."""
        if not is_pool_healthy(self.db):
            logger.warning("InviteTracker: Database pool not available")
            return
        try:
            async with acquire_safe(self.db) as conn:
                if count is None:
                    # Increment invites by 1 when no specific value is given
                    await conn.execute(
                        """
                        INSERT INTO invite_tracker (guild_id, user_id, invite_count)
                        VALUES ($1, $2, 1)
                        ON CONFLICT(guild_id, user_id) DO UPDATE SET invite_count = invite_tracker.invite_count + 1;
                        """,
                        guild_id, inviter_id
                    )
                else:
                    # Set invite count manually
                    await conn.execute(
                        """
                        INSERT INTO invite_tracker (guild_id, user_id, invite_count)
                        VALUES ($1, $2, $3)
                        ON CONFLICT(guild_id, user_id) DO UPDATE SET invite_count = $3;
                        """,
                        guild_id, inviter_id, count
                    )
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in update_invite_count: {conn_err}")
        except Exception as e:
            logger.error(f"Database error in update_invite_count: {e}")
    
    async def get_invite_leaderboard(self, guild_id: int, limit=10):
        """Fetch top users by invite count."""
        if not is_pool_healthy(self.db):
            logger.warning("⚠️ InviteTracker: Database pool not available")
            return []
        try:
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch(
                    "SELECT user_id, invite_count FROM invite_tracker WHERE guild_id = $1 ORDER BY invite_count DESC LIMIT $2",
                    guild_id, limit
                )
                return rows
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in get_invite_leaderboard: {conn_err}")
            return []
        except Exception as e:
            logger.error(f"Database error in get_invite_leaderboard: {e}")
            return []

    def _is_enabled(self, guild_id: int) -> bool:
        if self.settings:
            try:
                return bool(self.settings.get("invites", "enabled", guild_id))
            except KeyError:
                pass
        return True

    def _get_announcement_channel_id(self, guild_id: int) -> int | None:
        if self.settings:
            try:
                value = self.settings.get("invites", "announcement_channel_id", guild_id)
                if value:
                    return int(value)
            except KeyError:
                pass
            except (TypeError, ValueError):
                logger.warning("InviteTracker: announcement_channel_id invalid in settings.")
        return getattr(config, "INVITE_ANNOUNCEMENT_CHANNEL_ID", 0)

    def _get_template(self, *, with_inviter: bool, guild_id: int) -> str:
        key = "with_inviter_template" if with_inviter else "no_inviter_template"
        if self.settings:
            try:
                value = self.settings.get("invites", key, guild_id)
                if isinstance(value, str) and value.strip():
                    return value
            except KeyError:
                pass
        defaults = {
            True: "{member} joined! {inviter} now has {count} invites.",
            False: "{member} joined, but no inviter data found.",
        }
        return defaults[with_inviter]

    def _render_template(
        self,
        template: str,
        *,
        member: discord.Member,
        inviter: discord.abc.User | None,
        count: int | None,
    ) -> str:
        context: dict[str, str] = {
            "member": member.mention,
            "member_name": member.display_name,
            "inviter": inviter.mention if inviter and hasattr(inviter, "mention") else (inviter.name if inviter else ""),
            "inviter_name": (
                inviter.display_name
                if inviter and hasattr(inviter, "display_name")
                else (inviter.name if inviter else "")
            ),
            "count": str(count) if count is not None else "0",
        }
        try:
            return template.format(**context)
        except KeyError as exc:
            logger.warning(f"InviteTracker: missing placeholder in template: {exc}")
            return template

    @app_commands.command(name="inviteleaderboard", description="Show a leaderboard of the highest invite counts.")
    async def inviteleaderboard(self, interaction: discord.Interaction, limit: int = 10):
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        if not self._is_enabled(interaction.guild.id):
            await interaction.response.send_message("Invite tracker is currently disabled.", ephemeral=True)
            return
        rows = await self.get_invite_leaderboard(interaction.guild.id, limit)
        if not rows:
            await interaction.response.send_message("No invite data available yet.", ephemeral=True)
            return

        embed = EmbedBuilder.warning(title="Invite Leaderboard")
        for idx, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            embed.add_field(name=f"{idx}. {name}", value=f"Invites: {row['invite_count']}", inline=False)
        await interaction.response.send_message(embed=embed)
        from utils.fyi_tips import send_fyi_if_first
        await send_fyi_if_first(self.bot, interaction.guild.id, "first_invite_leaderboard")

    @app_commands.command(name="setinvites", description="Set a user's invite count manually.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setinvites(self, interaction: discord.Interaction, member: discord.Member, count: int):
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.response.send_message("Invite tracker is currently disabled.", ephemeral=True)
            return
        await self.update_invite_count(interaction.guild.id, member.id, count)
        await interaction.response.send_message(f"Invite count for {member.display_name} is set to {count}.")

    @app_commands.command(name="resetinvites", description="Reset a user's invite count to 0.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resetinvites(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.response.send_message("Invite tracker is currently disabled.", ephemeral=True)
            return
        await self.update_invite_count(interaction.guild.id, member.id, 0)
        await interaction.response.send_message(f"Invite count for {member.display_name} has been reset to 0.")
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self._is_enabled(member.guild.id):
            return
        await self.bot.wait_until_ready()  # Ensure the bot is ready

        # Brief wait for Discord to update invites
        await asyncio.sleep(1)

        guild = member.guild
        try:
            new_invites = await guild.invites()
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch guild invites: {e}")
            return
        old_invites = self.invites_cache.get(guild.id, [])

        inviter = None

        # Find which invite was used
        for old_invite in old_invites:
            for new_invite in new_invites:
                if old_invite.code == new_invite.code and old_invite.uses < new_invite.uses:
                    inviter = new_invite.inviter
                    break
        
        # Update cache with new invites
        self.invites_cache[guild.id] = new_invites

        # Send a message in the channel
        channel_id = self._get_announcement_channel_id(guild.id)
        if not channel_id:
            logger.warning("InviteTracker: announcement channel not set.")
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning("InviteTracker: could not find or access announcement channel.")
            return

        if inviter:
            await self.update_invite_count(member.guild.id, inviter.id)
            # Fetch current invite count after update
            invite_count = 0
            if is_pool_healthy(self.db):
                try:
                    async with acquire_safe(self.db) as conn:
                        row = await conn.fetchrow("SELECT invite_count FROM invite_tracker WHERE guild_id = $1 AND user_id = $2", member.guild.id, inviter.id)
                        invite_count = row["invite_count"] if row else 0
                except Exception as e:
                    logger.warning(f"⚠️ InviteTracker: DB error while fetching invite_count: {e}")
                    invite_count = 0
            message = self._render_template(
                self._get_template(with_inviter=True, guild_id=member.guild.id),
                member=member,
                inviter=inviter,
                count=invite_count,
            )
            await channel.send(message)
        else:
            message = self._render_template(
                self._get_template(with_inviter=False, guild_id=member.guild.id),
                member=member,
                inviter=None,
                count=None,
            )
            await channel.send(message)

        logger.info(f"InviteTracker: message sent in {getattr(channel, 'name', channel_id)} for {member}") 


async def setup(bot: commands.Bot):
    invite_tracker = InviteTracker(bot)
    await bot.add_cog(invite_tracker)
    await invite_tracker.setup_database()  # Ensure the database is set up
