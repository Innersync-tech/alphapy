import re
from datetime import datetime, timedelta
from typing import Any, cast

import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

import utils.reminder_repository as reminder_repo
from utils.cog_base import AlphaCog
from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.embed_parser import (
    extract_datetime_from_text,
    extract_fields_from_lines,
    format_message_paragraphs,
    infer_date_from_time_line,
    parse_days,
    parse_relative_date,
    short_title_for_reminder_name,
)
from utils.embed_parser import (
    parse_datetime as parse_datetime_fields,
)
from utils.logger import logger
from utils.parsers import format_days_for_display, parse_days_string
from utils.sanitizer import safe_embed_text
from utils.timezone import BRUSSELS_TZ
from utils.validators import validate_admin

# All logging timestamps in this module use Brussels time for clarity.


class EmbedReminderWatcher(AlphaCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db: asyncpg.Pool | None = None
    
    def format_days_for_display(self, days_list: list[str]) -> str:
        """Convert day numbers to readable day names using centralized parser."""
        return format_days_for_display(days_list) or "—"

    async def setup_db(self) -> None:
        pool = get_bot_db_pool(self.bot)
        if pool is None:
            logger.error("EmbedWatcher: shared DB pool not available")
            self.db = None
        else:
            self.db = pool

    def cog_load(self) -> None:
        """Subscribe to key embedwatcher settings so changes take effect immediately."""
        async def _on_announcements_channel_changed(value: Any) -> None:
            logger.info(f"EmbedWatcher: announcements_channel_id changed to {value}")

        async def _on_offset_changed(value: Any) -> None:
            logger.info(f"EmbedWatcher: reminder_offset_minutes changed to {value}")

        self.settings.add_listener("embedwatcher", "announcements_channel_id", _on_announcements_channel_changed)
        self.settings.add_listener("embedwatcher", "reminder_offset_minutes", _on_offset_changed)

    async def cog_unload(self) -> None:
        """Called when the cog is unloaded - clear local pool reference only (pool is shared)."""
        self.db = None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return  # Skip messages not in a guild
        
        announcements_channel_id = self._get_announcements_channel_id(message.guild.id)
        if message.channel.id != announcements_channel_id:
            return
        
        is_bot_message = message.author.id == getattr(self.bot.user, 'id', None)
        
        # Skip messages from the bot itself unless processing bot messages is enabled
        if is_bot_message:
            if not self._is_process_bot_messages_enabled(message.guild.id):
                return
        
        # Loop protection: Check if we already processed this message (for both bot and user messages)
        # This prevents duplicate reminders from the same message
        if is_pool_healthy(self.db):
            existing = await self._check_existing_reminder_for_message(message.guild.id, message.channel.id, message.id)
            if existing:
                logger.info(f"⏭️ Message {message.id} already has a reminder (ID: {existing}) - skipping to prevent duplicate processing")
                await self._log_message_processed(message, "skipped", f"Already processed (reminder ID: {existing})", message.guild.id)
                return
            
            # Additional check: if this is a bot message, check if a similar reminder already exists
            # This prevents loops when bot_messages is enabled and the bot sees its own reminder embeds
            if is_bot_message and message.embeds:
                embed = message.embeds[0]
                # Check if this embed is already a reminder (has auto-reminder footer or similar title pattern)
                if embed.footer and "auto-reminder" in (embed.footer.text or "").lower():
                    logger.info(f"⏭️ Bot message {message.id} is already a reminder embed - skipping to prevent loop")
                    await self._log_message_processed(message, "skipped", "Bot reminder embed detected", message.guild.id)
                    return
                # Check if a reminder with similar title already exists (within last 5 minutes)
                if embed.title:
                    similar = await self._check_similar_reminder_exists(
                        message.guild.id, 
                        embed.title, 
                        message.channel.id
                    )
                    if similar:
                        logger.info(f"⏭️ Similar reminder already exists for bot message {message.id} - skipping to prevent duplicate")
                        await self._log_message_processed(message, "skipped", f"Similar reminder exists (ID: {similar})", message.guild.id)
                        return
        
        # Check for embeds first
        message_type = "embed"
        embed_source = None
        
        if message.embeds:
            embed = message.embeds[0]
            embed_source = embed
            
            # ⛔️ Skip reminders that the bot itself has already posted (to avoid loops)
            # Check for auto-reminder footer (from reminder system)
            if embed.footer and "auto-reminder" in (embed.footer.text or "").lower():
                await self._log_message_processed(message, "skipped", "Auto-reminder tag detected", message.guild.id)
                return
            
            # Additional loop protection: Check embed footer for "processed" markers
            # (Future: we could add a marker when we create reminders from embeds)
            if embed.footer and "embedwatcher-processed" in (embed.footer.text or "").lower():
                return
            
            if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
                channel_name = message.channel.mention
            else:
                channel_name = f"#{getattr(message.channel, 'name', 'unknown')}" if hasattr(message.channel, 'name') else str(message.channel.id)
            logger.info(f"📥 Processing embed message from {message.author} in {channel_name} (ID: {message.id})")
            parsed = await self.parse_embed_for_reminder(embed, message.guild.id)
            if not parsed:
                logger.warning(f"⚠️ Failed to parse embed message {message.id} - parse_embed_for_reminder returned None")
                await self._log_message_processed(message, "failed", "Parse returned None - check logs for details", message.guild.id)
                await self._log_failed_parse(embed, message.guild.id, message, "embed")
                return
        # Check for non-embed messages if enabled
        elif self._is_non_embed_enabled(message.guild.id) and message.content:
            message_type = "text"
            # Convert message to a mock embed for parsing
            title_text = message.content[:256] if len(message.content) > 100 else message.content[:100] if message.content else "Message"
            mock_embed = EmbedBuilder.info(
                title=title_text,
                description=message.content
            )
            embed_source = mock_embed
            if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
                channel_name = message.channel.mention
            else:
                channel_name = f"#{getattr(message.channel, 'name', 'unknown')}" if hasattr(message.channel, 'name') else str(message.channel.id)
            logger.info(f"📥 Processing text message from {message.author} in {channel_name} (ID: {message.id})")
            parsed = await self.parse_embed_for_reminder(mock_embed, message.guild.id)
        else:
            return

        if parsed and parsed["reminder_time"]:
            # Successfully parsed
            logger.info(f"✅ Successfully parsed {message_type} message (ID: {message.id}) - reminder scheduled for {parsed['datetime'].strftime('%d/%m/%Y %H:%M')}")
            
            log_channel_id = self._get_log_channel_id(message.guild.id)
            log_channel = self.bot.get_channel(log_channel_id)
            if isinstance(log_channel, (discord.TextChannel, discord.Thread)):
                if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
                    channel_ref = message.channel.mention
                else:
                    channel_ref = f"#{getattr(message.channel, 'name', 'unknown')}" if hasattr(message.channel, 'name') else str(message.channel.id)
                
                # Create embed for cleaner log display using EmbedBuilder
                log_embed = EmbedBuilder.success(
                    title="🔔 Auto-reminder Detected",
                    description=f"Reminder automatically created from {message_type} message"
                )
                log_embed.add_field(
                    name="📌 Title",
                    value=safe_embed_text((parsed.get("title") or "—")[:1024], 1024),
                    inline=False
                )
                log_embed.add_field(
                    name="📅 Event Date & Time",
                    value=f"{parsed['datetime'].strftime('%A %d %B %Y')} at {parsed['datetime'].strftime('%H:%M')}",
                    inline=True
                )
                log_embed.add_field(
                    name="⏰ Reminder Time",
                    value=f"Will trigger at {parsed['reminder_time'].strftime('%H:%M')}",
                    inline=True
                )
                if parsed.get("location") and parsed.get("location") != "-":
                    log_embed.add_field(
                        name="📍 Location",
                        value=safe_embed_text(parsed.get("location", "")[:1024], 1024),
                        inline=True
                    )
                log_embed.add_field(
                    name="📝 Message Type",
                    value=message_type.capitalize(),
                    inline=True
                )
                if parsed.get("is_one_off"):
                    log_embed.add_field(
                        name="📆 Type",
                        value="One-off",
                        inline=True
                    )
                elif parsed.get('days'):
                    days_display = self.format_days_for_display(parsed['days'])
                    log_embed.add_field(
                        name="📆 Recurring Days",
                        value=days_display,
                        inline=True
                    )
                log_embed.set_footer(text=f"Author: {message.author.display_name} | Channel: {channel_ref}")
                log_embed.url = message.jump_url
                
                await log_channel.send(embed=log_embed)
            else:
                logger.warning("⚠️ Log channel not found or not accessible.")

            if is_pool_healthy(self.db):
                # Embed image URL for premium: only pass if embed has image (thumbnail or main image)
                embed_image_url = None
                if embed_source and hasattr(embed_source, "image") and embed_source.image and embed_source.image.url:
                    embed_image_url = embed_source.image.url
                elif embed_source and hasattr(embed_source, "thumbnail") and embed_source.thumbnail and embed_source.thumbnail.url:
                    embed_image_url = embed_source.thumbnail.url
                # Additional loop protection: Mark this message as processed by storing it
                # This prevents the same message from being processed again if it gets re-triggered
                await self.store_parsed_reminder(
                    parsed,
                    int(message.channel.id),
                    int(message.author.id),
                    message.guild.id,
                    origin_channel_id=int(message.channel.id),
                    origin_message_id=int(message.id),
                    image_url=embed_image_url,
                )
                
                # For bot messages: Add a marker to prevent future loops
                if is_bot_message:
                    logger.info(f"🔒 Marked bot message {message.id} as processed to prevent loops")
            else:
                logger.warning(f"⚠️ Database connection not available - reminder not stored for message {message.id}")
        else:
            # Failed to parse
            reason = "No date/time information found"
            if embed_source:
                await self._log_failed_parse(embed_source, message.guild.id, message, message_type)
            logger.info(f"⚠️ Failed to parse {message_type} message (ID: {message.id}) - {reason}")
            await self._log_message_processed(message, "failed", reason, message.guild.id)

    async def parse_embed_for_reminder(self, embed: discord.Embed, guild_id: int) -> dict | None:
        all_text = embed.description or ""
        title_text = embed.title or ""
        
        # Include footer text in all_text for parsing (if present)
        if embed.footer and embed.footer.text:
            all_text += f"\n{embed.footer.text}"
        
        for field in embed.fields:
            all_text += f"\n{field.name}\n{field.value}"

        lines = all_text.split('\n')
        date_line, time_line, location_line, days_line = self.extract_fields_from_lines(lines)

        # Fallback: if the "Time" line includes a concrete date, extract it so
        # one-off messages such as "Wednesday, October 01/10/25 – 19:30" are
        # recognised as dated events instead of recurring reminders.
        inferred_date = None
        if not date_line and time_line:
            inferred_date = self.infer_date_from_time_line(time_line)
            if inferred_date:
                date_line = inferred_date

        # Fallbacks for time and days if not present in structured lines
        if not time_line:
            time_fallback = re.search(r"\b(\d{1,2}[:.]\d{2})\s*(?:CET|CEST)?", embed.description or "")
            if not time_fallback:
                time_fallback = re.search(r"\b(\d{1,2}[:.]\d{2})\s*(?:CET|CEST)?", all_text)
            if time_fallback:
                time_line = time_fallback.group(0)

        # Try to extract relative dates like "This Wednesday", "Next Friday" from title or description
        if not date_line:
            relative_date = self.parse_relative_date(title_text + " " + (embed.description or ""))
            if relative_date:
                date_line = relative_date
                logger.info(f"✅ Parsed relative date: {date_line}")

        if not days_line and embed.description:
            day_fallback = re.search(r"\b(?:every|elke)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", embed.description, re.IGNORECASE)
            if day_fallback:
                days_line = day_fallback.group(1)

        try:
            dt, tz = self.parse_datetime(date_line, time_line)

            # Track whether we found a concrete calendar date
            had_explicit_date = bool(date_line)
            dt_from_description = False

            # Fallback: try to parse datetime from the description when
            # no explicit date/time fields were found.
            if not dt:
                parsed_from_description = extract_datetime_from_text(embed.description or "")
                if parsed_from_description:
                    dt = parsed_from_description
                    tz = BRUSSELS_TZ
                    dt_from_description = True

            # Fallback: attempt to extract location from the description if no
            # location field exists.
            if not location_line and embed.description:
                loc_match = re.search(r"(?:location|locatie)[:\s]*([^\n]+)", embed.description, re.IGNORECASE)
                if loc_match:
                    location_line = loc_match.group(1).strip()

            if not dt:
                # Try Grok fallback if enabled
                if self._is_gpt_fallback_enabled(guild_id):
                    gpt_parsed = await self._parse_with_gpt_fallback(embed, guild_id)
                    if gpt_parsed and gpt_parsed.get("datetime"):
                        # Use Grok parsed result
                        dt_gpt = gpt_parsed.get("datetime")
                        if dt_gpt:
                            reminder_time = dt_gpt - timedelta(minutes=self._get_reminder_offset(guild_id))
                        else:
                            reminder_time = None
                        if not dt_gpt or not reminder_time:
                            logger.warning("⚠️ Grok fallback returned invalid datetime")
                            await self._log_failed_parse(embed, guild_id)
                            return None
                        days_list = gpt_parsed.get("days", [])
                        location_from_gpt = gpt_parsed.get("location", "-")
                        logger.info(f"✅ Grok fallback parsing succeeded for embed: {embed.title}")
                        return {
                            "datetime": dt_gpt,
                            "reminder_time": reminder_time,
                            "location": location_from_gpt,
                            "title": embed.title or "-",
                            "description": embed.description or "-",
                            "days": days_list,
                            "is_one_off": not bool(days_list),
                        }
                
                # If Grok fallback also failed or disabled, log the failure
                # Note: We'll log this at the on_message level with more context
                logger.warning("⚠️ No valid date found and no time/date in description. Reminder will not be created.")
                return None

            if dt is None:
                logger.warning("⚠️ Cannot calculate reminder time: dt is None")
                return None
            offset_minutes = self._get_reminder_offset(guild_id)
            reminder_time = dt - timedelta(minutes=offset_minutes)

            # Decide recurrence: only use parse_days when explicitly provided.
            # If we have a concrete date (explicit date or parsed from description),
            # treat it as a one-off (days = []). Otherwise, require explicit days.
            #
            # Safety override: if "this [weekday]" or "next [weekday]" appears in the
            # title/description and matches the day in days_line, that is a strong signal
            # for a one-off event regardless of the days_line (e.g. a structured embed
            # that has both a "Days: Wednesday" field AND "This Wednesday's session" in
            # the title).
            if days_line and not (had_explicit_date or dt_from_description):
                full_text_for_check = title_text + " " + (embed.description or "")
                override_date = self.parse_relative_date(full_text_for_check)
                if override_date:
                    # "This Wednesday" / "Next Friday" found — treat as one-off
                    date_line = override_date
                    had_explicit_date = True
                    days_line = None
                    # Re-parse datetime with the concrete date
                    dt, tz = self.parse_datetime(date_line, time_line)
                    if dt is None:
                        logger.warning("⚠️ Override one-off date parse failed; falling back to recurring")
                        date_line = None
                        had_explicit_date = False
                        days_line = override_date  # restore (won't happen in practice)
                    else:
                        logger.info(f"✅ Override: 'this/next [day]' found — treating as one-off ({date_line})")

            is_one_off = had_explicit_date or dt_from_description

            if days_line:
                days_list = parse_days_string(days_line)
            else:
                if had_explicit_date or dt_from_description:
                    # For one-off events, store weekday for informational purposes
                    # This helps in the edit modal to show which day the event is on
                    weekday = str(dt.weekday())
                    days_list = [weekday]  # Store weekday for one-off events too
                else:
                    # At this point we have a time but no explicit/relative date and no days.
                    # Before giving up, try the Grok fallback once more with full context.
                    if self._is_gpt_fallback_enabled(guild_id):
                        gpt_parsed = await self._parse_with_gpt_fallback(embed, guild_id)
                        if gpt_parsed and gpt_parsed.get("datetime"):
                            dt_gpt = gpt_parsed.get("datetime")
                            if dt_gpt:
                                reminder_time = dt_gpt - timedelta(minutes=self._get_reminder_offset(guild_id))
                            else:
                                reminder_time = None
                            if not dt_gpt or not reminder_time:
                                logger.warning("⚠️ Grok fallback returned invalid datetime in days/recurrence phase")
                                await self._log_failed_parse(embed, guild_id)
                                return None
                            days_list = gpt_parsed.get("days", [])
                            location_from_gpt = gpt_parsed.get("location", "-")
                            logger.info(f"✅ Grok fallback parsing (no date/days) succeeded for embed: {embed.title}")
                            return {
                                "datetime": dt_gpt,
                                "reminder_time": reminder_time,
                                "location": location_from_gpt,
                                "title": embed.title or "-",
                                "description": embed.description or "-",
                                "days": days_list,
                                "is_one_off": not bool(days_list),
                            }

                    logger.warning("⚠️ No date and no days specified → reminder not created.")
                    return None

            # Log the parsed datetime and days list for debugging purposes.
            logger.debug(
                f"🕑 Parsed datetime: {dt.astimezone(BRUSSELS_TZ)} "
                f"(weekday {dt.weekday()}) → days {days_list}"
            )

            # Include footer in description if present (for storage in message field)
            footer_text = embed.footer.text if embed.footer and embed.footer.text else None
            
            # At this point we have a valid datetime; days_list contains weekday for one-off events
            return {
                "datetime": dt,
                "reminder_time": reminder_time,
                "location": location_line or "-",
                "title": embed.title or "-",
                "description": embed.description or "-",
                "footer": footer_text,  # Include footer for later use
                "days": days_list,
                "is_one_off": is_one_off,
            }
        except Exception as e:
            logger.exception(f"❌ Parse error: {e}")
            return None

    def extract_fields_from_lines(self, lines: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
        return extract_fields_from_lines(lines)

    def parse_datetime(self, date_line: str | None, time_line: str | None) -> tuple[datetime | None, object | None]:
        return parse_datetime_fields(date_line, time_line)

    def infer_date_from_time_line(self, time_line: str) -> str | None:
        return infer_date_from_time_line(time_line)

    def parse_relative_date(self, text: str) -> str | None:
        return parse_relative_date(text)

    def parse_days(self, days_line: str | None, dt: datetime) -> str:
        return parse_days(days_line, dt)

    def _short_title_for_reminder_name(self, parsed: dict, max_chars: int = 50) -> str:
        return short_title_for_reminder_name(parsed, max_chars)

    def _format_message_paragraphs(self, text: str) -> str:
        return format_message_paragraphs(text)

    async def store_parsed_reminder(
        self,
        parsed: dict,
        channel: int,
        created_by: int,
        guild_id: int,
        origin_channel_id: int | None = None,
        origin_message_id: int | None = None,
        image_url: str | None = None,
    ) -> None:
        parsed["datetime"]
        channel = int(channel)
        created_by = int(created_by)
        origin_channel_id = int(origin_channel_id) if origin_channel_id is not None else None
        origin_message_id = int(origin_message_id) if origin_message_id is not None else None
        reminder_dt = parsed["reminder_time"].astimezone(BRUSSELS_TZ)
        event_dt = parsed["datetime"].astimezone(BRUSSELS_TZ)
        trigger_time = reminder_dt.time()
        call_time_obj = event_dt.time()
        days_arr = parsed["days"]
        if isinstance(days_arr, str):
            days_arr = [d.strip() for d in days_arr.split(",") if d.strip()]
            logger.debug(f"[DEBUG] Final days_arr for DB insert: {days_arr} ({type(days_arr)})")
        # Use a short, clear title for the reminder name (avoids long/duplicate embed titles)
        short_title = self._short_title_for_reminder_name(parsed, max_chars=50)
        title_sanitized = short_title.replace("\n", " ").replace("\r", " ").strip()
        title_sanitized = " ".join(title_sanitized.split())

        # Create reminder name from event title (Discord limit 100 chars)
        max_title_length = 100
        truncated_title = title_sanitized[:max_title_length] if len(title_sanitized) > max_title_length else title_sanitized

        # Add ellipsis if truncated
        if len(title_sanitized) > max_title_length:
            truncated_title = truncated_title.rstrip() + "..."

        name = truncated_title
        
        # Construct message field: avoid duplication
        # For plain text messages and embeds where title is already in description, use only description
        title_val = parsed.get('title', '').strip()
        desc_val = parsed.get('description', '').strip()
        footer_val = parsed.get('footer', '').strip() if parsed.get('footer') else None
        
        # Smart duplicate detection: check if title content is already in description
        # This handles cases where title and description overlap significantly
        if title_val and desc_val and title_val != "-" and desc_val != "-":
            # Extract key words from title (remove emojis, special chars, common words)
            title_words = set(re.findall(r'\b\w{3,}\b', title_val.lower()))  # Words with 3+ chars
            desc_words = set(re.findall(r'\b\w{3,}\b', desc_val.lower()))
            
            # Calculate overlap: if >50% of title words are in description, likely duplicate
            overlap = len(title_words & desc_words)
            title_word_count = len(title_words) if title_words else 1
            overlap_ratio = overlap / title_word_count if title_word_count > 0 else 0
            
            # Also check if description starts with title or contains significant title content
            title_normalized = ' '.join(title_val.split()[:10])  # First 10 words of title
            desc_start = desc_val[:len(title_normalized) + 50]  # First part of description
            
            if overlap_ratio > 0.5 or desc_val.startswith(title_val) or title_normalized.lower() in desc_start.lower():
                # Title is already in description - use description only
                message = desc_val
            else:
                # Real embed where title and description are different - combine them
                message = f"{title_val}\n\n{desc_val}"
        elif desc_val and desc_val != "-":
            # Only description available
            message = desc_val
        elif title_val and title_val != "-":
            # Only title available
            message = title_val
        else:
            message = ""
        
        # Append footer to message if present
        if footer_val and footer_val not in message:
            message = f"{message}\n\n{footer_val}".strip() if message else footer_val

        # Add light paragraph formatting when message is one long block (no structure)
        message = self._format_message_paragraphs(message)

        location = parsed.get("location", "-")

        # Premium: only store image_url if user has premium in this guild
        if image_url:
            from utils.premium_guard import is_premium
            if not await is_premium(created_by, guild_id):
                image_url = None

        try:
            if not is_pool_healthy(self.db):
                raise RuntimeError("Database connection not available for store_parsed_reminder")
            async with acquire_safe(self.db) as conn:
                await reminder_repo.create(
                    conn,
                    guild_id=guild_id,
                    name=name,
                    channel_id=channel,
                    reminder_time=trigger_time,
                    call_time=call_time_obj,
                    days=days_arr,
                    message=message,
                    created_by=created_by,
                    origin_channel_id=origin_channel_id,
                    origin_message_id=origin_message_id,
                    event_time=event_dt,
                    image_url=image_url,
                    location=location if location and location != "-" else None,
                )
            
            # Log successful save
            log_channel_id = self._get_log_channel_id(guild_id)
            log_channel = self.bot.get_channel(log_channel_id)
            if isinstance(log_channel, (discord.TextChannel, discord.Thread)):
                # Create embed for cleaner log display using EmbedBuilder
                db_log_embed = EmbedBuilder.info(
                    title="Reminder Saved to Database",
                    description=f"Reminder **{name}** has been successfully stored",
                    fields=[
                        {"name": "🕒 Reminder Time", "value": f"{trigger_time.strftime('%H:%M')}", "inline": True},
                        {
                            "name": "📆 Type" if parsed.get("is_one_off") else ("📆 Recurring Days" if days_arr else "📆 Type"),
                            "value": (
                                f"One-off ({event_dt.strftime('%A %d %B %Y')})"
                                if parsed.get("is_one_off")
                                else (self.format_days_for_display(days_arr) if days_arr else "One-off event")
                            ),
                            "inline": True
                        }
                    ]
                )
                if location and location != "-":
                    db_log_embed.add_field(
                        name="📍 Location",
                        value=safe_embed_text(location[:1024], 1024),
                        inline=True
                    )
                db_log_embed.set_footer(text=f"embedwatcher | Guild: {guild_id}")
                
                await log_channel.send(embed=db_log_embed)
            else:
                logger.warning("⚠️ Could not find log channel for confirmation.")

            from utils.fyi_tips import send_fyi_if_first
            await send_fyi_if_first(self.bot, guild_id, "first_reminder_watcher")
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            logger.warning(f"Database connection error in store_parsed_reminder: {conn_err}")
            if getattr(self, "_db_manager", None) and self._db_manager._pool:
                try:
                    await self._db_manager._pool.close()
                except Exception:
                    pass
                self._db_manager._pool = None
            self.db = None
            raise
        except RuntimeError as e:
            logger.warning(f"Database pool not available: {e}")
            raise
        except Exception as e:
            logger.exception(f"[ERROR] Reminder insert failed: {e}")

    @app_commands.command(name="debug_parse_embed", description="(Admin) Parse the last embed in the channel.")
    async def debug_parse_embed(self, interaction: discord.Interaction) -> None:
        is_admin, err = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(err or "❌ Administrator access required.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send("⚠️ This command only works in servers.")
            return
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("⚠️ This command only works in text channels.")
            return
        messages = [m async for m in interaction.channel.history(limit=10)]

        for msg in messages:
            if msg.embeds:
                embed = msg.embeds[0]
                parsed = await self.parse_embed_for_reminder(embed, interaction.guild.id)
                if parsed:
                    response = (
                        f"🧠 **Parsed data:**\n"
                        f"📅 Date: `{parsed['datetime']}`\n"
                        f"⏰ Reminder Time: `{parsed['reminder_time']}`\n"
                        f"📍 Location: `{parsed.get('location', '-')}`"
                    )
                    await interaction.followup.send(response)
                else:
                    await interaction.followup.send("❌ Could not parse embed.")
                return

        await interaction.followup.send("⚠️ No embed found in the last 10 messages.")


    def _get_announcements_channel_id(self, guild_id: int) -> int:
        return self.settings_helper.get_int("embedwatcher", "announcements_channel_id", guild_id, fallback=0)

    def _get_log_channel_id(self, guild_id: int) -> int:
        return self.settings_helper.get_int("system", "log_channel_id", guild_id, fallback=0)

    def _get_reminder_offset(self, guild_id: int) -> int:
        return self.settings_helper.get_int("embedwatcher", "reminder_offset_minutes", guild_id, fallback=60)

    def _is_gpt_fallback_enabled(self, guild_id: int) -> bool:
        return self.settings_helper.get_bool("embedwatcher", "gpt_fallback_enabled", guild_id, fallback=True)

    def _get_failed_parse_log_channel_id(self, guild_id: int) -> int:
        value = self.settings_helper.get_int("embedwatcher", "failed_parse_log_channel_id", guild_id, fallback=0)
        if value and value != 0:
            return value
        # Fallback to system log channel
        return self.settings_helper.get_int("system", "log_channel_id", guild_id, fallback=0)

    def _is_non_embed_enabled(self, guild_id: int) -> bool:
        """Check if non-embed message parsing is enabled."""
        return self.settings_helper.get_bool("embedwatcher", "non_embed_enabled", guild_id, fallback=False)

    def _is_process_bot_messages_enabled(self, guild_id: int) -> bool:
        """Check if processing bot's own messages is enabled."""
        return self.settings_helper.get_bool("embedwatcher", "process_bot_messages", guild_id, fallback=False)

    async def _check_existing_reminder_for_message(self, guild_id: int, channel_id: int, message_id: int) -> int | None:
        """Check if a reminder already exists for this message to prevent duplicate processing."""
        if not is_pool_healthy(self.db):
            return None
        try:
            async with acquire_safe(self.db) as conn:
                row = await conn.fetchrow(
                    "SELECT id FROM reminders WHERE guild_id = $1 AND origin_channel_id = $2 AND origin_message_id = $3 LIMIT 1",
                    guild_id, channel_id, message_id
                )
                return row["id"] if row else None
        except RuntimeError:
            return None
        except Exception as e:
            logger.warning(f"⚠️ Error checking existing reminder: {e}")
            return None
    
    async def _check_similar_reminder_exists(self, guild_id: int, title: str, channel_id: int, minutes_threshold: int = 5) -> int | None:
        """Check if a reminder with similar title already exists in the same channel within the last N minutes.
        
        This helps prevent duplicate reminders when bot_messages is enabled and the bot sees its own reminder embeds.
        """
        if not is_pool_healthy(self.db):
            return None
        try:
            # Extract key words from title (remove emojis, "Reminder:", etc.)
            title_clean = re.sub(r'[^\w\s]', ' ', title.lower())
            title_clean = re.sub(r'\b(reminder|⏰|auto)\b', '', title_clean, flags=re.IGNORECASE)
            title_words = [w for w in title_clean.split() if len(w) >= 3]  # Words with 3+ chars
            
            if not title_words:
                return None
            
            # Check for reminders in the same channel created in the last N minutes
            async with acquire_safe(self.db) as conn:
                # Get recent reminders from the same channel
                threshold_time = datetime.now(BRUSSELS_TZ) - timedelta(minutes=minutes_threshold)
                rows = await conn.fetch(
                    """
                    SELECT id, name, created_at 
                    FROM reminders 
                    WHERE guild_id = $1 
                      AND channel_id = $2 
                      AND created_at >= $3
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    guild_id, channel_id, threshold_time
                )
                
                for row in rows:
                    reminder_name = row["name"] or ""
                    # Extract key words from reminder name
                    name_clean = re.sub(r'[^\w\s]', ' ', reminder_name.lower())
                    name_clean = re.sub(r'\b(reminder|⏰|auto|🤖)\b', '', name_clean, flags=re.IGNORECASE)
                    name_words = [w for w in name_clean.split() if len(w) >= 3]
                    
                    # Check overlap: if >60% of words match, consider it similar
                    if name_words and title_words:
                        overlap = len(set(title_words) & set(name_words))
                        min_words = min(len(title_words), len(name_words))
                        if min_words > 0 and overlap / min_words >= 0.6:
                            logger.debug(f"🔍 Found similar reminder: '{reminder_name}' matches '{title}' ({overlap}/{min_words} words)")
                            return row["id"]
                
                return None
        except RuntimeError:
            return None
        except Exception as e:
            logger.warning(f"⚠️ Error checking similar reminder: {e}")
            return None

    async def _parse_with_gpt_fallback(self, embed: discord.Embed, guild_id: int) -> dict | None:
        """Use Grok to parse embed when structured parsing fails."""
        try:
            from gpt.helpers import ask_gpt
            from utils.sanitizer import safe_prompt
            
            # Build text from embed and sanitize it
            embed_text = f"Title: {embed.title or ''}\n"
            embed_text += f"Description: {embed.description or ''}\n"
            for field in embed.fields:
                embed_text += f"{field.name}: {field.value}\n"
            
            # Sanitize embed text before sending to Grok
            gpt_context = safe_prompt(embed_text)
            
            # Get current date for context
            current_date = datetime.now(BRUSSELS_TZ)
            current_date_str = current_date.strftime("%d/%m/%Y")
            current_weekday = current_date.strftime("%A")
            
            prompt = f"""You are a precise datetime extractor for Discord event announcements. Today is {current_date_str} ({current_weekday}).

                        Rule #1: "This [weekday]" or "This coming [weekday]" (e.g. "This Wednesday", "This Wednesday's session") → ONE-OFF event on the next upcoming occurrence of that weekday. Fill "date", leave "days" empty.
                        Rule #2: "Next [weekday]" (e.g. "Next Friday") → ONE-OFF event on the occurrence AFTER the next one. Fill "date", leave "days" empty.
                        Rule #3: When only a plain weekday + time is given (e.g. "Wednesday 19:30", "Wednesday at 7:30pm") → ALWAYS take the NEXT upcoming occurrence of that weekday from today. Fill "date", leave "days" empty.
                           - If today is Monday → Wednesday = the Wednesday coming up this week ({(current_date + timedelta(days=(2 - current_date.weekday() + 7) % 7)).strftime('%d/%m/%Y')})
                           - Do NOT jump to next week unless "next week", "following week", "next Wednesday" is explicitly said.
                        Rule #4: If a concrete date is present (18/03, March 18, 18 March) → use that date + time. Fill "date", leave "days" empty.
                        Rule #5: ONLY for truly recurring events with explicit recurrence wording (e.g. "every Wednesday", "weekly on Wednesday", "each Friday") → keep "date" empty, fill "days".
                        
                        Output ONLY valid JSON, nothing else:
                        {{
                          "date": "DD/MM/YYYY" or "" if recurring,
                          "time": "HH:MM" in 24h format,
                          "days": "0,2,4" or "" (0=Monday, 2=Wednesday, etc.),
                          "location": "string" or ""
                        }}
                        
                        If truly impossible to extract: {{"error": "cannot_parse"}}
                        
                        Embed text:
                        {gpt_context}
                        """

            # Call Grok with structured prompt (temperature from guild settings)
            response = await ask_gpt(
                [{"role": "user", "content": prompt}],
                user_id=None,
                model=None,
                guild_id=guild_id
            )
            
            # Parse JSON response
            import json
            # Try to extract JSON from response (might have markdown code blocks)
            response_clean = response.strip()
            if response_clean.startswith("```"):
                # Remove markdown code blocks
                response_clean = response_clean.split("```")[1]
                if response_clean.startswith("json"):
                    response_clean = response_clean[4:]
                response_clean = response_clean.strip()
            elif response_clean.startswith("{"):
                # Find the JSON object
                start = response_clean.find("{")
                end = response_clean.rfind("}") + 1
                response_clean = response_clean[start:end]
            
            parsed_json = json.loads(response_clean)
            
            if parsed_json.get("error") == "cannot_parse":
                return None
            
            # Build datetime from parsed data
            date_str = parsed_json.get("date", "").strip()
            time_str = parsed_json.get("time", "").strip()
            days_str = parsed_json.get("days", "").strip()
            location = parsed_json.get("location", "").strip()
            
            if not time_str:
                return None
            
            # Parse time
            try:
                hour, minute = map(int, time_str.split(":"))
            except (ValueError, AttributeError):
                return None
            
            # Parse date if provided
            if date_str:
                try:
                    # Try DD/MM/YYYY format
                    day, month, year = map(int, date_str.split("/"))
                    dt = datetime(year, month, day, hour, minute, tzinfo=BRUSSELS_TZ)
                    days_list = []  # One-off event
                except (ValueError, AttributeError):
                    return None
            else:
                # Recurring - use current date with parsed time
                now = datetime.now(BRUSSELS_TZ)
                dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Parse days
                if days_str:
                    days_list = [d.strip() for d in days_str.split(",") if d.strip()]
                else:
                    # Default to weekday of current time
                    days_list = [str(dt.weekday())]
            
            return {
                "datetime": dt,
                "days": days_list,
                "location": location or "-",
            }
        except Exception as e:
            logger.exception(f"❌ Grok fallback parsing failed: {e}")
            return None

    async def _log_failed_parse(self, embed: discord.Embed, guild_id: int, message: discord.Message | None = None, message_type: str = "embed") -> None:
        """Log failed parse attempt to admin channel."""
        channel_id = self._get_failed_parse_log_channel_id(guild_id)
        if channel_id == 0:
            return
        
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        
        # Build embed text for logging
        embed_text = f"**Type:** {message_type.capitalize()} message\n"
        if message:
            embed_text += f"**Author:** {message.author.mention}\n"
            channel_ref = getattr(message.channel, 'mention', f"#{getattr(message.channel, 'name', 'unknown')}") if hasattr(message.channel, 'mention') else str(message.channel.id)
            embed_text += f"**Channel:** {channel_ref}\n"
            embed_text += f"**Message ID:** `{message.id}`\n"
            embed_text += f"**Jump URL:** [Go to message]({message.jump_url})\n\n"
        
        embed_text += f"**Title:** {embed.title or '—'}\n"
        embed_text += f"**Description:** {embed.description or '—'}\n"
        if embed.fields:
            embed_text += "\n**Fields:**\n"
            for field in embed.fields:
                embed_text += f"- **{field.name}:** {field.value}\n"
        
        log_embed = EmbedBuilder.warning(
            title="⚠️ Failed Parse",
            description=f"Could not parse reminder from {message_type}. Please review manually.\n\n{embed_text}"
        )
        log_embed.set_footer(text=f"embedwatcher | Guild: {guild_id}")
        
        try:
            await channel.send(embed=log_embed)
        except Exception as e:
            logger.warning(f"⚠️ Failed to log failed parse: {e}")

    async def _log_message_processed(self, message: discord.Message, status: str, reason: str, guild_id: int) -> None:
        """Log that a message was processed (successfully or not) to the log channel."""
        log_channel_id = self._get_log_channel_id(guild_id)
        if log_channel_id == 0:
            return
        
        log_channel = self.bot.get_channel(log_channel_id)
        if not isinstance(log_channel, (discord.TextChannel, discord.Thread)):
            return
        
        # Only log if log level allows it (info level for successful, warning for failed)
        from utils.logger import should_log_to_discord
        level = "warning" if status == "failed" else "info"
        if not should_log_to_discord(level, guild_id):
            return
        
        discord.Color.orange() if status == "failed" else discord.Color.green() if status == "success" else discord.Color.blue()
        emoji = "✅" if status == "success" else "⚠️" if status == "failed" else "⏭️"
        
        if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            channel_ref = message.channel.mention
        else:
            channel_ref = f"#{getattr(message.channel, 'name', 'unknown')}" if hasattr(message.channel, 'name') else str(message.channel.id)
        # Use EmbedBuilder with appropriate level
        level = "warning" if status == "failed" else "success" if status == "success" else "info"
        log_embed = EmbedBuilder.log(
            title=f"{emoji} Message Processed ({status.capitalize()})",
            description=(
                f"**Type:** {'Embed' if message.embeds else 'Text'}\n"
                f"**Author:** {message.author.mention}\n"
                f"**Channel:** {channel_ref}\n"
                f"**Status:** {status}\n"
                f"**Reason:** {reason}\n"
                f"🔗 [Jump to message]({message.jump_url})"
            ),
            level=level,
            guild_id=guild_id
        )
        log_embed.set_footer(text=f"embedwatcher | Guild: {guild_id}")
        
        try:
            await log_channel.send(embed=log_embed)
        except Exception as e:
            logger.warning(f"⚠️ Failed to log message processed: {e}")


class MockSettingsService:
    def get(self, scope, key, guild_id):
        # Return default values for testing
        if scope == "embedwatcher" and (key == "reminder_offset" or key == "reminder_offset_minutes"):
            return 60  # default 60 minutes
        return None

class MockBot:
    def __init__(self):
        self.settings = MockSettingsService()

    def get_channel(self, *_):
        return None

async def parse_embed_for_reminder(embed: discord.Embed, guild_id: int = 0):
    """Convenience wrapper to parse a reminder embed outside of the cog."""
    parser = EmbedReminderWatcher(cast(commands.Bot, MockBot()))
    return await parser.parse_embed_for_reminder(embed, guild_id)

async def setup(bot):
    cog = EmbedReminderWatcher(bot)
    await cog.setup_db()  # set up own DB connection
    await bot.add_cog(cog)
