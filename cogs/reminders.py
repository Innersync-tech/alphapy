import re
import time as time_module
from datetime import datetime, timedelta
from typing import Any, Protocol, cast

import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.app_commands import checks as app_checks
from discord.ext import commands, tasks

import config
import utils.reminder_repository as reminder_repo

# from config import GUILD_ID  # Removed - no longer needed for multi-guild support
from cogs.embed_watcher import parse_embed_for_reminder
from utils.cog_base import AlphaCog
from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import log_database_event, log_guild_action, log_with_guild, logger
from utils.parsers import format_days_for_display, parse_days_string, parse_time_string
from utils.sanitizer import safe_embed_text
from utils.timezone import BRUSSELS_TZ
from utils.user_messages import ERR_DB, ERR_GUILD_ONLY
from utils.validators import validate_admin

# All logging timestamps in this module use Brussels time for clarity.

LIVE_SESSION_NAME = "Live session"
LIVE_SESSION_MESSAGE = "Live session starting now!"


def is_live_session_row(row: asyncpg.Record | dict[str, Any] | None) -> bool:
    """True when the reminder row is a live-session preset."""
    if not row:
        return False
    name = row["name"] if isinstance(row, asyncpg.Record) else row.get("name")
    message = row["message"] if isinstance(row, asyncpg.Record) else row.get("message")
    return name == LIVE_SESSION_NAME and message == LIVE_SESSION_MESSAGE


class ReminderRepoConnection(Protocol):
    async def fetch(self, query: str, *args: Any, timeout: float | None = ...) -> list[asyncpg.Record]: ...
    async def execute(self, query: str, *args: Any, timeout: float | None = ...) -> str: ...


class ReminderCog(AlphaCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db: asyncpg.Pool | None = None
        # Rate limit: max 3 image reminders per hour per (user_id, guild_id)
        self._image_reminder_timestamps: dict[tuple[int, int], list[float]] = {}
        self.bot.loop.create_task(self.setup())

    def cog_load(self) -> None:
        """Subscribe to key reminder settings so changes take effect immediately."""
        async def _on_default_channel_changed(value: Any) -> None:
            logger.info(f"Reminders: default_channel_id changed to {value}")

        async def _on_enabled_changed(value: Any) -> None:
            logger.info(f"Reminders: module enabled changed to {value}")

        self.settings.add_listener("reminders", "default_channel_id", _on_default_channel_changed)
        self.settings.add_listener("reminders", "enabled", _on_enabled_changed)

    async def setup(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self._connect_database()
        except Exception as e:
            log_database_event("DB_CONNECT_FAILED", details=f"Initial connection: {e}")
            logger.error(f"❌ Error connecting to database: {e}")
            return

        if not self.check_reminders.is_running():
            self.check_reminders.start()

    async def send_log_embed(self, title: str, description: str, level: str = "info", guild_id: int = 0) -> None:
        """Send log embed to the correct guild's log channel"""
        if guild_id == 0:
            # Fallback for legacy calls without guild_id
            logger.warning("⚠️ send_log_embed called without guild_id - skipping Discord log")
            return

        # Check log level filtering
        from utils.logger import should_log_to_discord
        if not should_log_to_discord(level, guild_id):
            return

        try:
            embed = EmbedBuilder.log(title, description, level, guild_id)
            embed.set_footer(text=f"reminders | Guild: {guild_id}")

            channel_id = self._get_log_channel_id(guild_id)
            if channel_id == 0:
                # No log channel configured for this guild
                log_with_guild("No log channel configured for reminders logging", guild_id, "debug")
                return

            channel = self.bot.get_channel(channel_id)
            if channel and hasattr(channel, "send"):
                text_channel = cast(discord.TextChannel, channel)
                await text_channel.send(embed=embed)
                log_guild_action(guild_id, "LOG_SENT", details=f"reminders: {title}")
            else:
                log_with_guild(f"Log channel {channel_id} not found or not accessible", guild_id, "warning")

        except Exception as e:
            log_with_guild(f"Could not send reminders log embed: {e}", guild_id, "error")

    async def _connect_database(self) -> None:
        pool = get_bot_db_pool(self.bot)
        if pool is None:
            raise RuntimeError("Bot database pool not available")
        self.db = pool
        log_database_event("DB_CONNECTED", details="Reminders using shared bot database pool")

        try:
            async with acquire_safe(self.db) as conn:
                # Create table
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reminders (
                        id SERIAL PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        name TEXT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        time TIME,
                        call_time TIME,
                        days TEXT[],
                        message TEXT,
                        created_by BIGINT,
                        origin_channel_id BIGINT,
                        origin_message_id BIGINT,
                        event_time TIMESTAMPTZ,
                        location TEXT,
                        last_sent_at TIMESTAMPTZ
                    );
                    """
                )
                # Add columns if they don't exist
                await conn.execute(
                    "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS call_time TIME;"
                )
                await conn.execute(
                    "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ;"
                )
                await conn.execute(
                    "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS image_url TEXT;"
                )
                # Create indexes
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(time);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_event_time ON reminders(event_time);")
        except Exception as setup_e:
            log_database_event("DB_SETUP_ERROR", details=f"Failed to setup reminders table: {setup_e}")
            raise

        logger.info("✅ Connected to database pool!")
        log_database_event("DB_CONNECTION_ESTABLISHED", details="Reminders database pool ready")

    async def _ensure_connection(self) -> bool:
        if is_pool_healthy(self.db):
            return True
        pool = get_bot_db_pool(self.bot)
        if pool and is_pool_healthy(pool):
            self.db = pool
            return True
        logger.error("❌ Reminder DB: shared pool not available or unhealthy")
        return False

    async def _handle_connection_lost(self, error: Exception) -> None:
        logger.warning(f"Reminder DB connection lost (shared pool will self-heal): {error}")

    @app_commands.command(name="add_reminder", description="Schedule a one-off or recurring reminder.")
    @app_checks.cooldown(5, 60.0, key=lambda i: (i.guild.id, i.user.id) if i.guild else i.user.id)  # 5 per minute per guild+user
    @app_commands.describe(
        name="Name of the reminder",
        channel="Channel where the reminder should be sent (optional if default is set)",
        time="Time in HH:MM format",
        days="Days of the week (e.g. mon,tue,wed)",
        message="The reminder message text",
        link="(Optional) Link to message with embed",
        image_url="(Premium) Image or banner URL for the reminder",
        image="(Premium) Image attachment for the reminder",
    )
    async def add_reminder(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel | None = None,
        time: str | None = None,
        days: str | None = None,
        message: str | None = None,
        link: str | None = None,
        image_url: str | None = None,
        image: discord.Attachment | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return

        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are off in this server. Ask an admin to enable them.", ephemeral=True)
            return

        # Tier-based reminder limit (free users: max 10 active reminders)
        from utils.premium_guard import get_user_tier
        from utils.premium_tiers import REMINDER_LIMIT
        tier = await get_user_tier(interaction.user.id, interaction.guild.id)
        limit = REMINDER_LIMIT.get(tier)
        if limit is not None:
            async with acquire_safe(self.db) as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM reminders WHERE created_by = $1 AND guild_id = $2",
                    interaction.user.id,
                    interaction.guild.id,
                )
            if count >= limit:
                await interaction.followup.send(
                    f"You have reached the maximum of {limit} reminders for your tier. "
                    "Upgrade via `/premium` to create unlimited reminders.",
                    ephemeral=True,
                )
                return

        # Premium gate: reminders with image require premium
        resolved_image_url: str | None = (image.url if image else None) or (image_url.strip() if image_url and image_url.strip() else None)
        if resolved_image_url:
            from utils.premium_guard import is_premium, premium_required_message
            if not await is_premium(interaction.user.id, interaction.guild.id):
                await interaction.followup.send(
                    premium_required_message("Reminders with images"),
                    ephemeral=True,
                )
                return
            # Rate limit: max 3 image reminders per hour per user per guild
            key = (interaction.user.id, interaction.guild.id)
            now_ts = time_module.time()
            timestamps = self._image_reminder_timestamps.get(key, [])
            recent = [t for t in timestamps if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
            if len(recent) >= 3:
                await interaction.followup.send(
                    "You can add at most 3 reminders with images per hour. Try again later.",
                    ephemeral=True,
                )
                return

        if not await self._ensure_connection():
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        origin_channel_id = origin_message_id = event_time = None
        debug_info: list[str] = []
        days_input: Any | None = None

        # 👇 Als een embed-link is opgegeven: fetch en parse de embed
        if link:
            match = re.match(r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)", link)
            if not match:
                await interaction.followup.send("❌ Invalid message link provided.", ephemeral=True)
                return

            _, channel_id, message_id = map(int, match.groups())
            try:
                msg_channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                # Cast to TextChannel for type safety (fetch_message exists there)
                text_ch = cast(discord.TextChannel, msg_channel)
                msg = await text_ch.fetch_message(message_id)

                if not msg.embeds:
                    await interaction.followup.send("❌ No embed found in that message.", ephemeral=True)
                    return

                parsed = await parse_embed_for_reminder(msg.embeds[0])

                if not parsed:
                    await interaction.followup.send("❌ Error parsing embed.", ephemeral=True)
                    return

                if parsed.get("title"):
                    name = parsed["title"]
                    debug_info.append(f"📝 Title: `{name}`")

                if parsed.get("description") is not None:
                    desc_val = str(parsed.get("description") or "")
                    message = desc_val
                    debug_info.append(f"💬 Message: `{desc_val[:25]}...`" if len(desc_val) > 25 else f"💬 Message: `{desc_val}`")

                if parsed.get("reminder_time"):
                    time = parsed["reminder_time"].strftime("%H:%M")
                    event_time = parsed["datetime"].astimezone(BRUSSELS_TZ)
                    debug_info.append(f"⏰ Time: `{time}`")

                days_input = days
                if parsed.get("days") is not None:
                    days_input = parsed["days"]
                    days_for_debug = list(days_input) if isinstance(days_input, list) else [str(days_input)]
                    debug_info.append(f"📅 Day: `{', '.join(days_for_debug)}`")
                elif parsed.get("datetime"):
                    # Treat parsed embeds with a concrete date as one-off events
                    days_input = []
                    debug_info.append("📅 One-off event (no days set)")

                if parsed.get("location"):
                    debug_info.append(f"📍 Location: `{parsed['location']}`")

                origin_channel_id = str(channel_id)
                origin_message_id = str(message_id)

            except Exception as e:
                logger.error(f"Error parsing embed: {e}")
                await interaction.followup.send("❌ Failed to parse embed. Please try again.", ephemeral=True)
                return

        # ⏰ Tijd moet zeker bestaan
        if not time:
            await interaction.followup.send("❌ No time specified and no valid embed found.", ephemeral=True)
            return

        if channel is None:
            default_channel_id = self._get_default_channel_id(interaction.guild.id)
            if default_channel_id:
                resolved = self.bot.get_channel(default_channel_id)
                if resolved is None:
                    try:
                        resolved = await self.bot.fetch_channel(default_channel_id)
                    except Exception:
                        resolved = None
                if isinstance(resolved, discord.TextChannel):
                    channel = resolved
            if channel is None:
                await interaction.followup.send(
                    "❌ No channel specified and no default channel set for reminders.",
                    ephemeral=True,
                )
                return

        # ⏳ Parse time string naar datetime.time
        time_obj = parse_time_string(time)
        if time_obj is None:
            await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 19:30).", ephemeral=True)
            return

        # Normaliseer days naar weekday nummers (0=maandag, 6=zondag)
        raw_days = days_input if days_input is not None else days
        days_list = parse_days_string(raw_days if isinstance(raw_days, str) else None)

        origin_channel_id = int(origin_channel_id) if origin_channel_id else None
        origin_message_id = int(origin_message_id) if origin_message_id else None
        created_by = int(interaction.user.id)
        channel_id = int(channel.id)

        # Bepaal call_time en time:
        # - time = reminder tijd (T-60, wanneer reminder wordt verzonden)
        # - call_time = event tijd (T0, de daadwerkelijke tijd van het event)
        if event_time:
            # One-off: time is al reminder tijd (T-60), call_time = event tijd (T0)
            call_time_obj = event_time.time()
            # time_obj is al de reminder tijd (berekend in embed_watcher of hieronder)
        else:
            # Recurring: gebruiker geeft event tijd in, reminder moet 1 uur eerder komen
            # Bereken reminder tijd (1 uur voor event)
            reminder_offset = self._get_reminder_offset(interaction.guild.id)
            event_dt = datetime.combine(datetime.now(BRUSSELS_TZ).date(), time_obj)
            reminder_dt = event_dt - timedelta(minutes=reminder_offset)
            reminder_time_obj = reminder_dt.time()
            call_time_obj = time_obj  # Event tijd (wat gebruiker instelt)
            time_obj = reminder_time_obj  # Reminder tijd (1 uur eerder)

        if not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        guild_id = interaction.guild.id
        try:
            async with acquire_safe(self.db) as conn:
                rid = await reminder_repo.create(
                    conn,
                    guild_id=guild_id,
                    name=name,
                    channel_id=channel_id,
                    reminder_time=time_obj,
                    call_time=call_time_obj,
                    days=days_list,
                    message=message,
                    created_by=created_by,
                    origin_channel_id=origin_channel_id,
                    origin_message_id=origin_message_id,
                    event_time=event_time,
                    image_url=resolved_image_url,
                )
                logger.info(f"🟢 Reminder created (ID={rid}): {name} @ {time_obj} days={days_list} channel={channel_id}")
                # Rate limit tracking: record image reminder for this user/guild
                if resolved_image_url:
                    rkey = (interaction.user.id, interaction.guild.id)
                    now_ts = time_module.time()
                    ts_list = self._image_reminder_timestamps.get(rkey, [])
                    ts_list = [t for t in ts_list if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
                    ts_list.append(now_ts)
                    self._image_reminder_timestamps[rkey] = ts_list[-config.IMAGE_REMINDER_RATE_LIMIT_COUNT:]
        except RuntimeError:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return
        except Exception:
            logger.exception("Error creating reminder")
            await interaction.followup.send("❌ Failed to create reminder. Please try again.", ephemeral=True)
            return
        await self.send_log_embed(
            title="🟢 Reminder created",
            description=(
                f"ID: `{rid}`\n"
                f"Name: **{name}**\n"
                f"Channel: <#{channel_id}>\n"
                f"Time: `{time_obj.strftime('%H:%M')}`\n"
                f"Call time: `{call_time_obj.strftime('%H:%M')}`\n"
                f"Days: `{', '.join(days_list) if days_list else '—'}`"
            ),
            level="success",
            guild_id=interaction.guild.id,
        )

        from utils.fyi_tips import send_fyi_if_first
        await send_fyi_if_first(self.bot, guild_id, "first_reminder")

        if debug_info:
            logger.debug("add_reminder embed parse info: %s", debug_info)
        await interaction.followup.send(
            f"✅ Reminder **'{name}'** added to {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="add_live_session",
        description="(Premium) Add a recurring live session reminder with fixed message.",
    )
    @app_commands.describe(
        days="Days of the week (e.g. mon,wed,fri)",
        time="Time in HH:MM format (session start)",
        channel="Channel where the reminder should be sent (optional if default is set)",
        image_url="(Premium) Image or banner URL for the reminder",
        image="(Premium) Image attachment for the reminder",
    )
    async def add_live_session(
        self,
        interaction: discord.Interaction,
        days: str,
        time: str,
        channel: discord.TextChannel | None = None,
        image_url: str | None = None,
        image: discord.Attachment | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return

        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are off in this server. Ask an admin to enable them.", ephemeral=True)
            return

        resolved_image_url: str | None = (image.url if image else None) or (image_url.strip() if image_url and image_url.strip() else None)
        if resolved_image_url:
            from utils.premium_guard import is_premium, premium_required_message
            if not await is_premium(interaction.user.id, interaction.guild.id):
                await interaction.followup.send(
                    premium_required_message("Live session presets with images"),
                    ephemeral=True,
                )
                return
            key = (interaction.user.id, interaction.guild.id)
            now_ts = time_module.time()
            timestamps = self._image_reminder_timestamps.get(key, [])
            recent = [t for t in timestamps if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
            if len(recent) >= 3:
                await interaction.followup.send(
                    "You can add at most 3 reminders with images per hour. Try again later.",
                    ephemeral=True,
                )
                return

        if not await self._ensure_connection():
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        if channel is None:
            default_channel_id = self._get_default_channel_id(interaction.guild.id)
            if default_channel_id:
                resolved = self.bot.get_channel(default_channel_id)
                if resolved is None:
                    try:
                        resolved = await self.bot.fetch_channel(default_channel_id)
                    except Exception:
                        resolved = None
                if isinstance(resolved, discord.TextChannel):
                    channel = resolved
            if channel is None:
                await interaction.followup.send(
                    "❌ No channel specified and no default channel set for reminders.",
                    ephemeral=True,
                )
                return

        time_obj = parse_time_string(time)
        if time_obj is None:
            await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 19:30).", ephemeral=True)
            return

        days_list = parse_days_string(days)
        if not days_list:
            await interaction.followup.send("❌ Invalid days. Use e.g. mon,wed,fri.", ephemeral=True)
            return

        reminder_offset = self._get_reminder_offset(interaction.guild.id)
        event_dt = datetime.combine(datetime.now(BRUSSELS_TZ).date(), time_obj)
        reminder_dt = event_dt - timedelta(minutes=reminder_offset)
        reminder_time_obj = reminder_dt.time()
        call_time_obj = time_obj

        guild_id = interaction.guild.id
        channel_id = int(channel.id)
        created_by = int(interaction.user.id)
        name = LIVE_SESSION_NAME

        if not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        try:
            async with acquire_safe(self.db) as conn:
                await reminder_repo.create(
                    conn,
                    guild_id=guild_id,
                    name=name,
                    channel_id=channel_id,
                    reminder_time=reminder_time_obj,
                    call_time=call_time_obj,
                    days=days_list,
                    message=LIVE_SESSION_MESSAGE,
                    created_by=created_by,
                    image_url=resolved_image_url,
                )
            if resolved_image_url:
                rkey = (interaction.user.id, interaction.guild.id)
                now_ts = time_module.time()
                ts_list = self._image_reminder_timestamps.get(rkey, [])
                ts_list = [t for t in ts_list if t > now_ts - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW]
                ts_list.append(now_ts)
                self._image_reminder_timestamps[rkey] = ts_list[-config.IMAGE_REMINDER_RATE_LIMIT_COUNT:]
        except Exception:
            logger.exception("Error creating live session reminder")
            await interaction.followup.send("❌ Failed to create live session. Please try again.", ephemeral=True)
            return

        await interaction.followup.send(
            f"Live session set — {channel.mention} at **{time}** ({days}).",
            ephemeral=True,
        )

    @app_commands.command(
        name="edit_live_session",
        description="Edit an existing live-session preset reminder.",
    )
    @app_commands.describe(
        live_session_id="ID of the live session reminder",
        days="Days of the week (e.g. mon,wed,fri)",
        time="Session start time in HH:MM format",
        channel="Target channel",
        image_url="(Premium) New image URL",
        image="(Premium) New image attachment",
        clear_image="Set true to remove the current image",
    )
    async def edit_live_session(
        self,
        interaction: discord.Interaction,
        live_session_id: int,
        days: str | None = None,
        time: str | None = None,
        channel: discord.TextChannel | None = None,
        image_url: str | None = None,
        image: discord.Attachment | None = None,
        clear_image: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are off in this server. Ask an admin to enable them.", ephemeral=True)
            return
        if not await self._ensure_connection() or not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)

        try:
            async with acquire_safe(self.db) as conn:
                if is_admin:
                    row = await reminder_repo.get_by_id(conn, guild_id, live_session_id)
                else:
                    row = await reminder_repo.get_by_id_for_user(
                        conn, guild_id, live_session_id, user_id
                    )
        except Exception:
            logger.exception("Error fetching live session for edit")
            await interaction.followup.send("❌ Failed to load live session. Please try again.", ephemeral=True)
            return

        if not row or not is_live_session_row(row):
            await interaction.followup.send(
                f"No live session found with ID `{live_session_id}` or you don't have permission to edit it.",
                ephemeral=True,
            )
            return

        _UNCHANGED = object()
        image_update: str | None | object = _UNCHANGED
        if clear_image:
            image_update = None
        elif image or (image_url and image_url.strip()):
            image_update = (image.url if image else None) or image_url.strip()
            if image_update:
                from utils.premium_guard import is_premium, premium_required_message
                if not await is_premium(interaction.user.id, guild_id):
                    await interaction.followup.send(
                        premium_required_message("Live session presets with images"),
                        ephemeral=True,
                    )
                    return

        current_call_time = row.get("call_time") or row.get("time")
        if hasattr(current_call_time, "strftime"):
            time_str = current_call_time.strftime("%H:%M")
        else:
            time_str = str(current_call_time)[:5] if current_call_time else "00:00"

        if time:
            time_obj = parse_time_string(time)
            if time_obj is None:
                await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 19:30).", ephemeral=True)
                return
            time_str = time

        time_obj = parse_time_string(time_str)
        if time_obj is None:
            await interaction.followup.send("❌ Invalid time on existing reminder.", ephemeral=True)
            return

        days_list = list(row["days"]) if row.get("days") else []
        if days:
            days_list = parse_days_string(days)
            if not days_list:
                await interaction.followup.send("❌ Invalid days. Use e.g. mon,wed,fri.", ephemeral=True)
                return

        channel_id = int(channel.id) if channel else int(row["channel_id"])
        reminder_offset = self._get_reminder_offset(guild_id)
        event_dt = datetime.combine(datetime.now(BRUSSELS_TZ).date(), time_obj)
        reminder_dt = event_dt - timedelta(minutes=reminder_offset)
        reminder_time_obj = reminder_dt.time()
        call_time_obj = time_obj

        final_image: str | None = row.get("image_url")
        if image_update is not _UNCHANGED:
            final_image = image_update  # type: ignore[assignment]

        try:
            async with acquire_safe(self.db) as conn:
                await reminder_repo.update_live_session(
                    conn,
                    guild_id,
                    live_session_id,
                    reminder_time=reminder_time_obj,
                    call_time=call_time_obj,
                    days=days_list,
                    channel_id=channel_id,
                    image_url=final_image,
                )
        except Exception:
            logger.exception("Error updating live session")
            await interaction.followup.send("❌ Failed to update live session. Please try again.", ephemeral=True)
            return

        ch = channel or self.bot.get_channel(channel_id)
        ch_mention = ch.mention if isinstance(ch, discord.TextChannel) else f"<#{channel_id}>"
        await interaction.followup.send(
            f"✅ Live session **#{live_session_id}** updated — {ch_mention} at **{time_obj.strftime('%H:%M')}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="delete_live_session",
        description="Delete an existing live-session preset reminder.",
    )
    @app_commands.describe(live_session_id="ID of the live session reminder")
    async def delete_live_session(
        self,
        interaction: discord.Interaction,
        live_session_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are off in this server. Ask an admin to enable them.", ephemeral=True)
            return
        if not await self._ensure_connection() or not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)

        try:
            async with acquire_safe(self.db) as conn:
                if is_admin:
                    row = await reminder_repo.get_by_id(conn, guild_id, live_session_id)
                else:
                    row = await reminder_repo.get_by_id_for_user(
                        conn, guild_id, live_session_id, user_id
                    )
                if not row or not is_live_session_row(row):
                    await interaction.followup.send(
                        f"No live session found with ID `{live_session_id}` or you don't have permission to delete it.",
                        ephemeral=True,
                    )
                    return
                if is_admin:
                    await reminder_repo.delete(conn, guild_id, live_session_id)
                else:
                    await reminder_repo.delete_for_user(conn, guild_id, live_session_id, user_id)
        except Exception:
            logger.exception("Error deleting live session")
            await interaction.followup.send("❌ Failed to delete live session. Please try again.", ephemeral=True)
            return

        await interaction.followup.send(
            f"🗑️ Live session **#{live_session_id}** deleted.",
            ephemeral=True,
        )

    @app_commands.command(name="reminder_list", description="View your active reminders")
    async def reminder_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(ERR_GUILD_ONLY, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are currently disabled.", ephemeral=True)
            return
        if not await self._ensure_connection():
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return
        if not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return
        user_id = interaction.user.id
        channel_id = getattr(interaction.channel, "id", 0)

        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)

        guild_id = interaction.guild.id
        try:
            if not self.db:
                await interaction.followup.send(ERR_DB, ephemeral=True)
                return
            async with acquire_safe(self.db) as conn:
                if is_admin:
                    rows = await reminder_repo.list_for_guild(conn, guild_id)
                else:
                    rows = await reminder_repo.list_for_user(conn, guild_id, user_id, channel_id)
                logger.info(f"🔍 Fetched {len(rows)} reminders ({'admin' if is_admin else 'user'})")

                if not rows:
                    embed = EmbedBuilder.warning(
                        "Active Reminders",
                        "No reminders found."
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                # Helper function to format days using centralized parser
                def format_days(days_db: Any) -> str:
                    """Convert day numbers/abbreviations to readable day names."""
                    if not days_db:
                        return "One-off"
                    if isinstance(days_db, str):
                        days_list = [days_db]
                    else:
                        days_list = list(days_db)
                    return format_days_for_display(days_list) or "One-off"

                # Create embed using EmbedBuilder
                embed = EmbedBuilder.info(
                    "Active Reminders",
                    f"Found **{len(rows)}** reminder{'s' if len(rows) != 1 else ''}"
                )

                # Group reminders by type for better organization
                recurring_reminders = []
                one_off_reminders = []
                
                for row in rows:
                    is_one_off = not row["days"] or len(row["days"]) == 0
                    if is_one_off:
                        one_off_reminders.append(row)
                    else:
                        recurring_reminders.append(row)
                
                # Add recurring reminders first
                if recurring_reminders:
                    recurring_text = []
                    for row in recurring_reminders[:10]:  # Limit to 10 per section
                        # Use call_time (event time) if available, otherwise time (reminder time)
                        display_time = row["call_time"] if row["call_time"] else row["time"]
                        time_str = display_time.strftime("%H:%M") if display_time else "—"
                        
                        days_str = format_days(row["days"])
                        location_str = f" • 📍 {safe_embed_text(row['location'])}" if row.get("location") else ""
                        
                        # Get channel mention
                        channel = self.bot.get_channel(row["channel_id"]) if row.get("channel_id") else None
                        if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
                            channel_str = f" in {channel.mention}"
                        else:
                            channel_str = f" (Channel ID: {row['channel_id']})"
                        
                        recurring_text.append(
                            f"**{safe_embed_text(row['name'])}**\n"
                            f"⏰ {time_str} • 📅 {days_str}{location_str}\n"
                            f"📺 {channel_str} • ID: `{row['id']}`"
                        )
                    
                    if len(recurring_reminders) > 10:
                        recurring_text.append(f"\n*...and {len(recurring_reminders) - 10} more recurring reminders*")
                    
                    embed.add_field(
                        name=f"🔄 Recurring ({len(recurring_reminders)})",
                        value="\n\n".join(recurring_text) if recurring_text else "None",
                        inline=False
                    )
                
                # Add one-off reminders
                if one_off_reminders:
                    one_off_text = []
                    for row in one_off_reminders[:10]:  # Limit to 10 per section
                        # Use call_time (event time) if available, otherwise time (reminder time)
                        display_time = row["call_time"] if row["call_time"] else row["time"]
                        time_str = display_time.strftime("%H:%M") if display_time else "—"
                        
                        # Format event_time if available
                        event_info = ""
                        if row.get("event_time"):
                            event_dt = row["event_time"]
                            if isinstance(event_dt, datetime):
                                event_dt = event_dt.astimezone(BRUSSELS_TZ)
                                event_info = f" • 📅 {event_dt.strftime('%A, %B %d, %Y')}"
                        
                        location_str = f" • 📍 {safe_embed_text(row['location'])}" if row.get("location") else ""
                        
                        # Get channel mention
                        channel = self.bot.get_channel(row["channel_id"]) if row.get("channel_id") else None
                        if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
                            channel_str = f" in {channel.mention}"
                        else:
                            channel_str = f" (Channel ID: {row['channel_id']})"
                        
                        one_off_text.append(
                            f"**{safe_embed_text(row['name'])}**\n"
                            f"⏰ {time_str}{event_info}{location_str}\n"
                            f"📺 {channel_str} • ID: `{row['id']}`"
                        )
                    
                    if len(one_off_reminders) > 10:
                        one_off_text.append(f"\n*...and {len(one_off_reminders) - 10} more one-off reminders*")
                    
                    embed.add_field(
                        name=f"📌 One-off ({len(one_off_reminders)})",
                        value="\n\n".join(one_off_text) if one_off_text else "None",
                        inline=False
                    )

                embed.set_footer(text="Use /reminder_edit <id> to edit or /reminder_delete <id> to delete")
                
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            logger.exception("Error fetching reminders")
            await interaction.followup.send("⚠️ Failed to fetch reminders. Please try again.", ephemeral=True)

    @app_commands.command(name="reminder_delete", description="Delete a reminder by ID")
    @app_commands.describe(reminder_id="The ID of the reminder you want to delete")
    async def reminder_delete(self, interaction: discord.Interaction, reminder_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.followup.send("Reminders are currently disabled.", ephemeral=True)
            return
        if not await self._ensure_connection():
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        if not self.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        guild_id = interaction.guild.id
        try:
            async with acquire_safe(self.db) as conn:
                row = await reminder_repo.get_by_id(conn, guild_id, reminder_id)

                if not row:
                    await interaction.followup.send(f"No reminder found with ID `{reminder_id}`.", ephemeral=True)
                    return

                await reminder_repo.delete(conn, guild_id, reminder_id)
        except RuntimeError:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return
        except Exception:
            logger.exception("Error deleting reminder")
            await interaction.followup.send("❌ Failed to delete reminder. Please try again.", ephemeral=True)
            return
        await interaction.followup.send(
            f"🗑️ Reminder **{row['name']}** (ID: `{reminder_id}`) was successfully deleted."
        )
    
    @app_commands.command(name="reminder_edit", description="Edit an existing reminder")
    @app_commands.describe(reminder_id="The ID of the reminder to edit")
    async def reminder_edit(self, interaction: discord.Interaction, reminder_id: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message(ERR_GUILD_ONLY, ephemeral=True)
            return
        if not self._is_enabled(interaction.guild.id):
            await interaction.response.send_message("⚠️ Reminders are currently disabled.", ephemeral=True)
            return
        if not await self._ensure_connection():
            await interaction.response.send_message(ERR_DB, ephemeral=True)
            return

        if not self.db:
            await interaction.response.send_message(ERR_DB, ephemeral=True)
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)

        # Fetch reminder and check ownership
        try:
            async with acquire_safe(self.db) as conn:
                if is_admin:
                    row = await reminder_repo.get_by_id(conn, guild_id, reminder_id)
                else:
                    row = await reminder_repo.get_by_id_for_user(conn, guild_id, reminder_id, user_id)
        except RuntimeError:
            await interaction.response.send_message(ERR_DB, ephemeral=True)
            return
        except Exception:
            logger.exception("Error fetching reminder for edit")
            await interaction.response.send_message("❌ Failed to fetch reminder. Please try again.", ephemeral=True)
            return

        if not row:
            await interaction.response.send_message(
                f"❌ Reminder with ID `{reminder_id}` not found or you don't have permission to edit it.",
                ephemeral=True
            )
            return

        # Prepare current values for modal
        # Use call_time (event tijd, T0) for display, not time (reminder tijd, T-60)
        current_name = row["name"] or ""
        
        # Safely extract time as HH:MM string
        current_time = ""
        try:
            call_time_val = row.get("call_time")
            if call_time_val:
                # Handle both time objects and strings
                if hasattr(call_time_val, 'strftime'):
                    current_time = call_time_val.strftime("%H:%M")
                elif isinstance(call_time_val, str):
                    # If it's already a string, try to parse and reformat
                    try:
                        parsed = datetime.strptime(call_time_val, "%H:%M:%S").time()
                        current_time = parsed.strftime("%H:%M")
                    except ValueError:
                        try:
                            parsed = datetime.strptime(call_time_val, "%H:%M").time()
                            current_time = parsed.strftime("%H:%M")
                        except ValueError:
                            current_time = call_time_val[:5] if len(call_time_val) >= 5 else call_time_val
                else:
                    current_time = str(call_time_val)[:5]
            elif row.get("event_time"):
                event_time_val = row["event_time"]
                if hasattr(event_time_val, 'time'):
                    current_time = event_time_val.time().strftime("%H:%M")
                elif hasattr(event_time_val, 'strftime'):
                    current_time = event_time_val.strftime("%H:%M")
            elif row.get("time"):
                # Fallback: if no call_time, use time (but this shouldn't happen for new reminders)
                time_val = row["time"]
                if hasattr(time_val, 'strftime'):
                    current_time = time_val.strftime("%H:%M")
                elif isinstance(time_val, str):
                    current_time = time_val[:5] if len(time_val) >= 5 else time_val
        except Exception as e:
            logger.warning(f"⚠️ Error formatting time for edit modal: {e}")
            current_time = ""
        
        # Ensure current_time is max 5 characters (HH:MM format) and is a valid string
        if not isinstance(current_time, str):
            current_time = str(current_time)[:5] if current_time else ""
        if len(current_time) > 5:
            current_time = current_time[:5]
        
        # Safely format all values for modal
        # Ensure current_name is a valid string (max 100 chars for Discord)
        if not isinstance(current_name, str):
            current_name = str(current_name) if current_name else ""
        # Sanitize: Discord modals don't accept newlines in TextInput default values
        current_name = current_name.replace('\n', ' ').replace('\r', ' ').strip()
        current_name = ' '.join(current_name.split())  # Collapse multiple spaces
        current_name = current_name[:100] if len(current_name) > 100 else current_name
        
        # Ensure current_days is a valid string
        current_days = ", ".join(row["days"]) if row["days"] else ""
        if not isinstance(current_days, str):
            current_days = ", ".join(map(str, row["days"])) if row["days"] else ""
        current_days = current_days[:50] if len(current_days) > 50 else current_days
        
        # Ensure current_message is a valid string
        # Extract footer from message if present (footer was appended with \n\n separator)
        current_message = row["message"] or ""
        if not isinstance(current_message, str):
            current_message = str(current_message) if current_message else ""
        
        # Try to extract footer from message (footer is appended with \n\n)
        # Look for the pattern: message ends with \n\n followed by footer-like text
        current_footer = ""
        if current_message and "\n\n" in current_message:
            # Split by double newline - last part might be footer
            parts = current_message.rsplit("\n\n", 1)
            if len(parts) == 2:
                # Check if the last part looks like a footer (short text, possibly at end)
                potential_footer = parts[1].strip()
                # If it's relatively short (likely footer) and the message part exists
                if len(potential_footer) < 200 and parts[0].strip():
                    current_message = parts[0].strip()
                    current_footer = potential_footer
        
        current_message = current_message[:1000] if len(current_message) > 1000 else current_message
        current_footer = current_footer[:500] if len(current_footer) > 500 else current_footer
        
        # Ensure current_channel_id is an integer
        current_channel_id = row["channel_id"]
        if not isinstance(current_channel_id, int):
            try:
                current_channel_id = int(current_channel_id)
            except (ValueError, TypeError):
                logger.error(f"❌ Invalid channel_id type: {type(current_channel_id)}")
                await interaction.response.send_message("❌ Invalid reminder data (channel_id).", ephemeral=True)
                return
        
        # Show edit modal
        modal = EditReminderModal(
            reminder_id=reminder_id,
            current_name=current_name,
            current_time=current_time,
            current_days=current_days,
            current_message=current_message,
            current_channel_id=current_channel_id,
            cog=self,
            guild_id=guild_id,
            is_admin=is_admin
        )
        await interaction.response.send_modal(modal)

    @reminder_delete.autocomplete("reminder_id")
    async def reminder_id_autocomplete(self, interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        if not self._is_enabled(interaction.guild.id):
            return []
        if not await self._ensure_connection():
            return []
        if not self.db:
            return []
        guild_id = interaction.guild.id
        async with acquire_safe(self.db) as conn:
            rows = await reminder_repo.autocomplete_all(conn, guild_id)
        return [
            app_commands.Choice(name=f"ID {row['id']} – {row['name'][:30]}", value=row["id"])
            for row in rows if current.lower() in str(row["id"]) or current.lower() in row["name"].lower()
        ]

    @reminder_edit.autocomplete("reminder_id")
    async def reminder_edit_autocomplete(self, interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
        if not interaction.guild:
            return []
        if not self._is_enabled(interaction.guild.id):
            return []
        if not await self._ensure_connection():
            return []
        if not self.db:
            return []
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)
        
        async with acquire_safe(self.db) as conn:
            if is_admin:
                rows = await reminder_repo.autocomplete_all(conn, guild_id)
            else:
                rows = await reminder_repo.autocomplete_for_user(conn, guild_id, user_id)
        return [
            app_commands.Choice(name=f"ID {row['id']} – {row['name'][:30]}", value=row["id"])
            for row in rows if current.lower() in str(row["id"]) or current.lower() in row["name"].lower()
        ]

    def _get_log_channel_id(self, guild_id: int) -> int:
        return self.settings_helper.get_int("system", "log_channel_id", guild_id, fallback=0)

    def _is_enabled(self, guild_id: int) -> bool:
        return self.settings_helper.get_bool("reminders", "enabled", guild_id, fallback=True)

    def _get_default_channel_id(self, guild_id: int) -> int | None:
        value = self.settings_helper.get_int("reminders", "default_channel_id", guild_id, fallback=0)
        return value if value else None

    def _allow_everyone_mentions(self, guild_id: int) -> bool:
        return self.settings_helper.get_bool("reminders", "allow_everyone_mentions", guild_id, fallback=False)

    def _get_reminder_offset(self, guild_id: int) -> int:
        """Get reminder offset in minutes (default: 60 = 1 hour before event)."""
        return self.settings_helper.get_int("embedwatcher", "reminder_offset_minutes", guild_id, fallback=60)

    @tasks.loop(seconds=60)
    async def check_reminders(self) -> None:
        if not await self._ensure_connection():
            logger.debug("⛔ Database connection not ready, skipping reminder check.")
            return

        if not is_pool_healthy(self.db):
            logger.debug("⛔ Database pool not available or closing, skipping reminder check.")
            return

        # Sweep stale entries from the rate-limit dict to prevent unbounded growth
        sweep_cutoff = time_module.time() - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW
        stale_keys = [k for k, v in self._image_reminder_timestamps.items() if not any(t > sweep_cutoff for t in v)]
        for k in stale_keys:
            del self._image_reminder_timestamps[k]

        now = datetime.now(BRUSSELS_TZ).replace(second=0, microsecond=0)
        current_time_str = now.strftime("%H:%M:%S")
        current_time_hhmm = now.strftime("%H:%M")  # For TIME column comparison
        current_day = str(now.weekday())
        current_date = now.date()
        
        # Calculate next day for midnight edge case (reminder at 23:xx for next day event at 00:xx)
        # If reminder is at 23:xx, it's for an event the next day
        next_day = str((now.weekday() + 1) % 7)  # Wrap around: 7 becomes 0 (Monday)
        next_date = (now + timedelta(days=1)).date()

        try:
            async with acquire_safe(self.db) as conn:
                # Build day matching: support both numeric ("0", "1", "2") and text ("ma", "di", "woe", etc.)
                day_abbrevs = {
                    "0": ["0", "ma", "maandag", "monday"],
                    "1": ["1", "di", "dinsdag", "tuesday"],
                    "2": ["2", "wo", "woe", "woensdag", "wednesday"],
                    "3": ["3", "do", "donderdag", "thursday"],
                    "4": ["4", "vr", "vrijdag", "friday"],
                    "5": ["5", "za", "zaterdag", "saturday"],
                    "6": ["6", "zo", "zondag", "sunday"],
                }
                day_abbrevs.get(current_day, [current_day])
                day_abbrevs.get(next_day, [next_day])
                
                # Check if any day variant matches the days array
                # time and call_time are TIME columns, so we compare them directly
                # Convert current_time_hhmm to TIME for comparison
                current_time_obj = datetime.strptime(current_time_hhmm, "%H:%M").time()
                
                # Check if current time is 23:xx (for midnight edge case)
                is_late_night = current_time_obj.hour == 23
                
                rows = await reminder_repo.list_active(
                    conn,
                    current_time_obj=current_time_obj,
                    current_date=current_date,
                    current_day=current_day,
                    is_late_night=is_late_night,
                    next_date=next_date,
                    next_day=next_day,
                )
        except TimeoutError as e:
            logger.warning(f"⚠️ Reminder loop: connection reset timed out (transient): {e}")
            return
        except (pg_exceptions.InterfaceError, pg_exceptions.ConnectionDoesNotExistError, ConnectionResetError) as conn_err:
            await self._handle_connection_lost(conn_err)
            try:
                await self.send_log_embed(
                    title="🚨 Reminder loop error",
                    description=f"Database connection lost: {conn_err}",
                    level="error",
                    guild_id=0  # Will be set by send_log_embed if needed
                )
            except Exception:
                pass
            return
        except RuntimeError as e:
            # Pool not available
            logger.debug(f"⛔ Reminder loop: {e}")
            return
        except Exception as e:
            logger.exception("🚨 Reminder loop error while fetching data")
            try:
                await self.send_log_embed(
                    title="🚨 Reminder loop error",
                    description=str(e),
                    level="error",
                    guild_id=0
                )
            except Exception:
                pass
            return

        try:
            if len(rows) > 0:
                logger.info(f"🔎 Checking reminders at {current_time_str} - found {len(rows)} reminder(s) to process: {[r['id'] for r in rows]}")
            for row in rows:
                # Idempotency guard: skip if already sent in this minute
                last_sent = row.get("last_sent_at")
                if last_sent is not None:
                    try:
                        last_sent_bxl = last_sent.astimezone(BRUSSELS_TZ)
                    except Exception:
                        last_sent_bxl = last_sent
                    if last_sent_bxl.replace(second=0, microsecond=0) == now:
                        logger.info(f"⏭️ Skip reminder {row['id']} (already sent this minute)")
                        continue

                channel = self.bot.get_channel(int(row["channel_id"]))
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    logger.warning(f"⚠️ Channel {row['channel_id']} not found for reminder {row['id']}.")
                    await self.send_log_embed(
                        title="⚠️ Reminder channel not found",
                        description=(
                            f"ID: `{row['id']}`\n"
                            f"Name: **{row['name']}**\n"
                            f"Channel ID: `{row['channel_id']}`\n"
                            f"Channel not found or deleted"
                        ),
                        level="warning",
                        guild_id=row["guild_id"],
                    )
                    continue

                # Determine if this is the T0 (on-time) send or T-60 (offset) send
                is_t0_send = False
                if row.get("event_time"):
                    event_dt_check = row["event_time"].astimezone(BRUSSELS_TZ)
                    is_t0_send = (event_dt_check.strftime("%H:%M:%S") == current_time_str)

                # Keep embed title short (Discord limit 256); truncate long names from older reminders
                name_display = safe_embed_text((row["name"] or "")[:240], 240)
                embed = EmbedBuilder.success(
                    title=f"⏰ Reminder: {name_display}",
                    description=safe_embed_text(row["message"] or "-")
                )
                # Show date+time for one-off events; for recurring show only the configured time
                event_dt = row.get("event_time")
                if event_dt:
                    try:
                        event_dt = event_dt.astimezone(BRUSSELS_TZ)
                    except Exception:
                        pass
                    call_time_obj = row.get("call_time") or event_dt.time()
                    embed.add_field(name="📅 Date", value=event_dt.strftime("%A %d %B %Y"), inline=False)
                    embed.add_field(name="⏰ Time", value=call_time_obj.strftime("%H:%M"), inline=False)
                else:
                    # Recurring: show only reminder time (no date)
                    call_time_obj = row.get("call_time") or row.get("time")
                    if call_time_obj:
                        try:
                            time_str = call_time_obj.strftime("%H:%M")
                        except Exception:
                            time_str = str(call_time_obj)
                        embed.add_field(name="⏰ Time", value=time_str, inline=False)
                # Location
                if row.get("location") and row["location"] != "-":
                    embed.add_field(name="📍 Location", value=safe_embed_text(row["location"], 1024), inline=False)
                # Premium: image/banner if present
                if row.get("image_url"):
                    embed.set_image(url=row["image_url"])
                # Link to original message
                if row.get("origin_channel_id") and row.get("origin_message_id"):
                    link = f"https://discord.com/channels/{row['guild_id']}/{row['origin_channel_id']}/{row['origin_message_id']}"
                    embed.add_field(name="🔗 Original", value=f"[Click here]({link})", inline=False)
                
                embed.set_footer(text="🤖 Auto-reminder")

                text_channel = cast(discord.TextChannel, channel)
                mention_enabled = self._allow_everyone_mentions(row["guild_id"])
                content = "@everyone" if mention_enabled else None

                # If this is the T0 send, delete the earlier T-60 reminder message
                if is_t0_send and row.get("sent_message_id"):
                    try:
                        prev_msg = await text_channel.fetch_message(int(row["sent_message_id"]))
                        await prev_msg.delete()
                        logger.info(f"🗑️ Deleted T-60 reminder message {row['sent_message_id']} (reminder {row['id']})")
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass  # Already deleted or no permissions — not critical

                try:
                    sent_msg = await text_channel.send(
                        content=content,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(everyone=mention_enabled)
                    )
                    logger.info(f"📤 Reminder sent (ID={row['id']}) to channel {row['channel_id']}: {row['name']}")
                    # Format date/time for log
                    date_str = event_dt.strftime('%Y-%m-%d') if event_dt else 'N/A'
                    time_str = call_time_obj.strftime('%H:%M') if call_time_obj else 'N/A'

                    await self.send_log_embed(
                        title="📤 Reminder sent",
                        description=(
                            f"ID: `{row['id']}`\n"
                            f"Name: **{safe_embed_text(row['name'])}**\n"
                            f"Channel: <#{row['channel_id']}>\n"
                            f"Date: {date_str}\n"
                            f"Time (display): `{time_str}`"
                        ),
                        level="info",
                        guild_id=row["guild_id"],
                    )
                    # Update idempotency marker; for T-60 sends also store the message ID
                    try:
                        async with acquire_safe(self.db) as update_conn:
                            await reminder_repo.update_sent_at(
                                update_conn,
                                reminder_id=row["id"],
                                guild_id=row["guild_id"],
                                last_sent_at=now,
                                sent_message_id=sent_msg.id if (not is_t0_send and row.get("event_time")) else None,
                            )
                    except Exception:
                        logger.exception("⚠️ Could not update last_sent_at")
                except discord.Forbidden:
                    logger.warning(f"⚠️ Reminder {row['id']} could not be sent: no permissions in channel {row['channel_id']}")
                    await self.send_log_embed(
                        title="⚠️ Reminder send failed",
                        description=(
                            f"ID: `{row['id']}`\n"
                            f"Name: **{row['name']}**\n"
                            f"Channel: <#{row['channel_id']}>\n"
                            f"Error: No permissions to send in this channel"
                        ),
                        level="error",
                        guild_id=row["guild_id"],
                    )
                except discord.NotFound:
                    logger.warning(f"⚠️ Reminder {row['id']} could not be sent: channel {row['channel_id']} not found")
                    await self.send_log_embed(
                        title="⚠️ Reminder send failed",
                        description=(
                            f"ID: `{row['id']}`\n"
                            f"Name: **{row['name']}**\n"
                            f"Channel: <#{row['channel_id']}>\n"
                            f"Error: Channel not found (possibly deleted)"
                        ),
                        level="error",
                        guild_id=row["guild_id"],
                    )
                except Exception as send_error:
                    logger.exception(f"❌ Reminder {row['id']} could not be sent: {send_error}")
                    await self.send_log_embed(
                        title="❌ Reminder send failed",
                        description=(
                            f"ID: `{row['id']}`\n"
                            f"Name: **{row['name']}**\n"
                            f"Channel: <#{row['channel_id']}>\n"
                            f"Error: {str(send_error)[:200]}"
                        ),
                        level="error",
                        guild_id=row["guild_id"],
                    )
                # Delete one-off reminders from DB after T0 send (not after T-60)
                if row.get("event_time") and is_t0_send:
                    try:
                        async with acquire_safe(self.db) as delete_conn:
                            await reminder_repo.delete(delete_conn, row["guild_id"], row["id"])
                        logger.info(f"🗑️ Reminder {row['id']} (one-off) deleted after T0 send.")
                        await self.send_log_embed(
                            title="🗑️ Reminder deleted (one-off)",
                            description=(
                                f"ID: `{row['id']}`\n"
                                f"Name: **{row['name']}**\n"
                                f"Channel: <#{row['channel_id']}>\n"
                                f"Deleted after T0 at {now.strftime('%Y-%m-%d %H:%M')}"
                            ),
                            level="warning",
                            guild_id=row["guild_id"],
                        )
                    except Exception as delete_err:
                        logger.warning(f"⚠️ Could not delete one-off reminder {row['id']}: {delete_err}")
                        # Continue - reminder will be cleaned up on next loop iteration

        except Exception as e:
            if isinstance(e, (pg_exceptions.InterfaceError, pg_exceptions.ConnectionDoesNotExistError, ConnectionResetError)):
                await self._handle_connection_lost(e)
            logger.exception("🚨 Reminder loop error while sending")
            try:
                await self.send_log_embed(
                    title="🚨 Reminder loop error",
                    description=str(e),
                    level="error",
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level helpers kept for backwards compatibility with api.py imports.
# These are thin wrappers around reminder_repository — new code should import
# from utils.reminder_repository directly.
# ---------------------------------------------------------------------------

async def get_reminders_for_user(
    conn: ReminderRepoConnection,
    user_id: str,
    guild_id: int | None = None,
):
    return await reminder_repo.get_for_api(conn, user_id, guild_id)


async def create_reminder(conn: ReminderRepoConnection, data: dict[str, Any]) -> None:
    await reminder_repo.create_for_api(conn, data)


async def update_reminder(conn: ReminderRepoConnection, data: dict[str, Any]) -> None:
    await reminder_repo.update_for_api(conn, data)


async def delete_reminder(conn: ReminderRepoConnection, reminder_id: int, created_by: int | str) -> None:
    await reminder_repo.delete_by_owner(conn, reminder_id, created_by)


class EditReminderModal(discord.ui.Modal, title="Edit Reminder"):
    def __init__(
        self,
        reminder_id: int,
        current_name: str,
        current_time: str,
        current_days: str,
        current_message: str,
        current_channel_id: int,
        cog: ReminderCog,
        guild_id: int,
        is_admin: bool
    ):
        super().__init__()
        self.reminder_id = reminder_id
        self.cog = cog
        self.guild_id = guild_id
        self.is_admin = is_admin

        # Ensure current_name is not None and is a valid string
        if current_name is None:
            current_name = ""
        if not isinstance(current_name, str):
            current_name = str(current_name)
        # Sanitize: Discord modals don't accept newlines in TextInput default values
        current_name = current_name.replace('\n', ' ').replace('\r', ' ').strip()
        current_name = ' '.join(current_name.split())  # Collapse multiple spaces
        # Discord modal validation: ensure default is a valid string (max 100 chars)
        current_name = current_name[:100] if len(current_name) > 100 else current_name
        
        self.name_input = discord.ui.TextInput(
            label="Name",
            placeholder="Reminder name",
            default=current_name,
            max_length=100,
            required=True
        )
        # Ensure current_time is valid for Discord modal (max 5 chars, HH:MM format)
        # Discord requires default to be a string, not None
        if not current_time or len(current_time) > 5 or not isinstance(current_time, str):
            current_time = ""  # Empty string is acceptable if placeholder is provided
        self.time_input = discord.ui.TextInput(
            label="Time (HH:MM)",
            placeholder="e.g., 19:30",
            default=current_time,
            max_length=5,
            required=True
        )
        # Ensure all defaults are valid strings
        if current_days is None:
            current_days = ""
        if not isinstance(current_days, str):
            current_days = str(current_days)
        current_days = current_days[:50] if len(current_days) > 50 else current_days
        
        if current_message is None:
            current_message = ""
        if not isinstance(current_message, str):
            current_message = str(current_message)
        current_message = current_message[:1000] if len(current_message) > 1000 else current_message
        
        self.days_input = discord.ui.TextInput(
            label="Days (comma-separated)",
            placeholder="e.g. mon,wed,fri or leave empty",
            default=current_days,
            max_length=50,
            required=False
        )
        self.message_input = discord.ui.TextInput(
            label="Message",
            placeholder="Reminder message",
            default=current_message,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False
        )
        self.channel_input = discord.ui.TextInput(
            label="Channel ID (optional)",
            placeholder=f"Current: {current_channel_id}",
            default="",
            max_length=20,
            required=False
        )

        self.add_item(self.name_input)
        self.add_item(self.time_input)
        self.add_item(self.days_input)
        self.add_item(self.message_input)
        self.add_item(self.channel_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not await self.cog._ensure_connection():
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        if not self.cog.db:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return

        # Validate and parse time
        time_str = self.time_input.value.strip()
        time_obj = parse_time_string(time_str)
        if time_obj is None:
            await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 19:30).", ephemeral=True)
            return

        # Parse days and normalize to weekday numbers using centralized parser
        days_str = self.days_input.value.strip()
        days_list = parse_days_string(days_str)

        # Fetch current reminder to preserve event_time and call_time logic
        try:
            async with acquire_safe(self.cog.db) as conn:
                current_row = await reminder_repo.get_by_id(conn, self.guild_id, self.reminder_id)
                if not current_row:
                    await interaction.followup.send("❌ Reminder not found.", ephemeral=True)
                    return

                # Parse channel (optional)
                channel_id = None
                channel_input_str = self.channel_input.value.strip()
                if channel_input_str:
                    try:
                        channel_id = int(channel_input_str)
                        # Validate channel exists
                        channel = self.cog.bot.get_channel(channel_id)
                        if not channel:
                            try:
                                channel = await self.cog.bot.fetch_channel(channel_id)
                            except Exception:
                                await interaction.followup.send(f"❌ Channel with ID `{channel_id}` not found.", ephemeral=True)
                                return
                        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                            await interaction.followup.send("❌ Channel must be a text channel or thread.", ephemeral=True)
                            return
                    except ValueError:
                        await interaction.followup.send("❌ Invalid channel ID format.", ephemeral=True)
                        return
                else:
                    # Use existing channel_id if not provided
                    channel_id = current_row.get("channel_id")

                # Determine call_time and time:
                # - time = reminder tijd (T-60, wanneer reminder wordt verzonden)
                # - call_time = event tijd (T0, de daadwerkelijke tijd van het event)
                # - time_obj is wat de gebruiker heeft ingevuld (event tijd, T0)
                event_time = current_row.get("event_time")
                if event_time:
                    # One-off: time_obj is event tijd (T0), time kolom moet reminder tijd (T-60) worden
                    call_time_obj = time_obj  # Event tijd (wat gebruiker instelt)
                    reminder_offset = self.cog._get_reminder_offset(self.guild_id)
                    event_dt = datetime.combine(datetime.now(BRUSSELS_TZ).date(), time_obj)
                    reminder_dt = event_dt - timedelta(minutes=reminder_offset)
                    reminder_time_obj = reminder_dt.time()
                    time_obj = reminder_time_obj  # Reminder tijd (T-60)
                else:
                    # Recurring: gebruiker geeft event tijd in, reminder moet 1 uur eerder komen
                    reminder_offset = self.cog._get_reminder_offset(self.guild_id)
                    event_dt = datetime.combine(datetime.now(BRUSSELS_TZ).date(), time_obj)
                    reminder_dt = event_dt - timedelta(minutes=reminder_offset)
                    reminder_time_obj = reminder_dt.time()
                    call_time_obj = time_obj  # Event tijd (wat gebruiker instelt)
                    time_obj = reminder_time_obj  # Reminder tijd (1 uur eerder)

                # Get message value (footer remains in message if it was there originally)
                final_message = self.message_input.value.strip() if self.message_input.value else None
                
                # Update reminder (time and call_time are TIME columns, so we pass time objects)
                await reminder_repo.update_fields(
                    conn,
                    reminder_id=self.reminder_id,
                    guild_id=self.guild_id,
                    name=self.name_input.value.strip(),
                    reminder_time=time_obj,
                    call_time=call_time_obj,
                    days=days_list,
                    message=final_message,
                    channel_id=channel_id if channel_id else None,
                )
        except RuntimeError:
            await interaction.followup.send(ERR_DB, ephemeral=True)
            return
        except Exception:
            logger.exception("Error updating reminder")
            await interaction.followup.send("❌ Failed to update reminder. Please try again.", ephemeral=True)
            return

        # Log the edit
        await self.cog.send_log_embed(
            title="🟡 Reminder edited",
            description=(
                f"ID: `{self.reminder_id}`\n"
                f"Name: **{self.name_input.value.strip()}**\n"
                f"Time: `{time_str}`\n"
                f"Days: `{', '.join(days_list) if days_list else '—'}`\n"
                f"Edited by: {interaction.user.mention}"
            ),
            level="info",
            guild_id=self.guild_id,
        )

        await interaction.followup.send(
            f"✅ Reminder **{self.name_input.value.strip()}** (ID: `{self.reminder_id}`) has been updated.",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ReminderCog(bot))
