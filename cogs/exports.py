
import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

from utils.csv_helpers import create_csv_buffer, create_discord_file_from_buffer
from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, is_pool_healthy
from utils.logger import logger
from utils.validators import validate_admin


class Exports(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("exports", bot=bot)
        self.bot.loop.create_task(self._setup())

    async def _setup(self) -> None:
        try:
            self.db = await self._db_manager.ensure_pool()
        except Exception as e:
            logger.error(f"Exports: DB pool bind error: {e}")
            self.db = None

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        self.db = None

    @app_commands.command(name="export_tickets", description="Export tickets as CSV (admin)")
    @app_commands.describe(scope="Optional: 7d, 30d, all (default: all)")
    async def export_tickets(self, interaction: discord.Interaction, scope: str | None = "all"):
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not is_pool_healthy(self.db):
            await interaction.followup.send("❌ Database not connected.", ephemeral=True)
            return
        try:
            where = ""
            if scope == "7d":
                where = "WHERE created_at >= NOW() - INTERVAL '7 days'"
            elif scope == "30d":
                where = "WHERE created_at >= NOW() - INTERVAL '30 days'"
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch(
                    f"SELECT id, user_id, username, status, created_at, updated_at, claimed_by, channel_id FROM support_tickets {where} ORDER BY id DESC"
                )
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in export_tickets: {conn_err}")
            await interaction.followup.send("❌ Database connection error. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Database error in export_tickets: {e}")
            await interaction.followup.send("❌ Database error. Please try again later.", ephemeral=True)
            return
        csv_rows = [dict(r) for r in rows]
        fieldnames = ["id", "user_id", "username", "status", "created_at", "updated_at", "claimed_by", "channel_id"]
        buf = create_csv_buffer(csv_rows, fieldnames=fieldnames)
        data = create_discord_file_from_buffer(buf, f"tickets_{scope or 'all'}.csv")
        await interaction.followup.send(content=f"✅ Exported {len(rows)} tickets (scope={scope}).", file=data, ephemeral=True)

    @app_commands.command(name="export_faq", description="Export FAQ entries as CSV (admin)")
    async def export_faq(self, interaction: discord.Interaction):
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not is_pool_healthy(self.db):
            await interaction.followup.send("❌ Database not connected.", ephemeral=True)
            return
        try:
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch("SELECT id, title, summary, keywords, created_at FROM faq_entries ORDER BY id DESC")
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in export_faq: {conn_err}")
            await interaction.followup.send("❌ Database connection error. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Database error in export_faq: {e}")
            await interaction.followup.send("❌ Database error. Please try again later.", ephemeral=True)
            return
        csv_rows = []
        for r in rows:
            csv_rows.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "summary": r.get("summary"),
                "keywords": ";".join(r.get("keywords") or []),
                "created_at": r.get("created_at"),
            })
        fieldnames = ["id", "title", "summary", "keywords", "created_at"]
        buf = create_csv_buffer(csv_rows, fieldnames=fieldnames)
        data = create_discord_file_from_buffer(buf, "faq.csv")
        await interaction.followup.send(content=f"✅ Exported {len(rows)} FAQ entries.", file=data, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Exports(bot))


