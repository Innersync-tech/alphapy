"""Self-service GDPR data deletion command for Discord users.

Allows any user to permanently delete all their personal data from the
Alphapy Railway PostgreSQL database. Requires explicit confirmation before
any data is removed. Does not affect Discord accounts or Supabase accounts.
"""


import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

from utils.database_helpers import DatabaseManager
from utils.db_helpers import is_pool_healthy
from utils.gdpr_railway_purge import purge_railway_user_data
from utils.guild_logs import format_user_log_line, send_home_guild_log
from utils.innersync_identity import get_innersync_id_for_discord
from utils.logger import logger


class ConfirmDeleteView(discord.ui.View):
    """Two-button confirmation view for /delete_my_data."""

    def __init__(self, user_id: int, cog: "DeleteMyDataCog"):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This confirmation is not for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, delete everything", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        self.stop()

        if not is_pool_healthy(self.cog.db):
            await interaction.edit_original_response(
                content="❌ Database not available. Please try again later or contact support@innersync.tech.",
                view=None,
            )
            return

        try:
            await _purge_user_data(self.cog.db, interaction.user.id)
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning("delete_my_data: DB connection error for user %s: %s", interaction.user.id, conn_err)
            await interaction.edit_original_response(
                content="❌ Database connection error. Please try again later or contact support@innersync.tech.",
                view=None,
            )
            return
        except Exception as exc:
            logger.error("delete_my_data: Unexpected error for user %s: %s", interaction.user.id, exc, exc_info=True)
            await interaction.edit_original_response(
                content="❌ An error occurred. Please contact support@innersync.tech.",
                view=None,
            )
            return

        logger.info("delete_my_data: Data purge complete for user_id=%s", interaction.user.id)
        await interaction.edit_original_response(
            content=(
                "✅ **Your data has been deleted.**\n\n"
                "All your personal data stored in Alphapy's database has been removed. "
                "Your Discord account and any Supabase/web account are not affected. "
                "If you have questions, contact support@innersync.tech."
            ),
            view=None,
        )
        await send_home_guild_log(
            interaction.client,
            "User data deleted",
            (
                f"{format_user_log_line(interaction.user)}\n"
                "Confirmed `/delete_my_data`. Personal data was purged from Alphapy."
            ),
            level="warning",
            source="gdpr",
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. No data was deleted.", embed=None, view=None
        )

    async def on_timeout(self) -> None:
        self.stop()


async def _purge_user_data(pool: asyncpg.Pool, user_id: int) -> None:
    """Delete all personal data for a user from Railway PostgreSQL.

    Delegates Railway SQL to ``purge_railway_user_data`` (shared with
    ``webhooks/supabase.py``). premium_subs is excluded — Belgian tax law
    requires 7-year retention of financial records. See privacy-policy.md §6.
    """
    await purge_railway_user_data(pool, user_id, log_prefix="delete_my_data")

    innersync_id = await get_innersync_id_for_discord(pool, user_id)
    try:
        from agents.memory import purge_agent_user_data

        await purge_agent_user_data(innersync_id, discord_user_id=user_id)
    except Exception as exc:
        logger.error(
            "delete_my_data: agent memory purge failed for user_id=%s: %s",
            user_id,
            exc,
        )


class DeleteMyDataCog(commands.Cog):
    """Provides the /delete_my_data slash command for GDPR self-service erasure."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("delete_my_data", bot=bot)
        self.bot.loop.create_task(self._setup())

    async def _setup(self) -> None:
        try:
            self.db = await self._db_manager.ensure_pool()
        except Exception as exc:
            logger.error("DeleteMyDataCog: DB pool creation error: %s", exc)
            self.db = None

    async def cog_unload(self) -> None:
        self.db = None

    @app_commands.command(
        name="delete_my_data",
        description="Permanently delete all your personal data stored by Alphapy. This cannot be undone.",
    )
    async def delete_my_data(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🗑️ Delete My Data",
            description=(
                "This will permanently delete **all your personal data** stored in Alphapy's database:\n\n"
                "• Onboarding responses\n"
                "• Support tickets and ticket summaries\n"
                "• FAQ search history\n"
                "• Command audit logs\n"
                "• Growth check-in reflections\n"
                "• AI usage records and quota counters\n"
                "• Agent session daily counters\n"
                "• Agent session history and agent memory (Supabase)\n"
                "• Automod logs\n"
                "• Terms acceptance records\n\n"
                "Reminder messages you authored are cleared and your ID is removed from "
                "those reminders and from custom commands you created.\n\n"
                "⚠️ **This cannot be undone.** Your Discord account and Supabase/web account "
                "are **not** affected. Premium subscription records are retained for 7 years "
                "as required by Belgian tax law.\n\n"
                "Are you sure you want to proceed?"
            ),
            color=discord.Color.red(),
        )
        view = ConfirmDeleteView(user_id=interaction.user.id, cog=self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DeleteMyDataCog(bot))
