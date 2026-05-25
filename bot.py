import time
from threading import Thread
from typing import Any, cast

import discord
import uvicorn
from discord.ext import commands

import config
from gpt.helpers import set_bot_instance
from utils.logger import logger
from utils.operational_logs import EventType, log_operational_event
from utils.settings_service import SettingDefinition, SettingsService


def start_api():
    uvicorn.run("api:app", host="0.0.0.0", port=8000)


# Intentions instellen
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True  # Required for reaction roles
intents.guilds = True
intents.members = True  # Required to recognize members
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Set start_time for uptime tracking
cast(Any, bot).start_time = time.time()

# Database pool access is handled by utils.db_helpers.get_bot_db_pool()
# This provides consistent database access across the codebase

settings_service = SettingsService(getattr(config, "DATABASE_URL", None))
settings_service.register(
    SettingDefinition(
        scope="system",
        key="log_channel_id",
        description="Channel for status and error messages.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="system",
        key="rules_channel_id",
        description="Channel for rules and onboarding (welcome message + Start button).",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="onboarding",
        key="enabled",
        description="Whether onboarding is enabled for this guild.",
        value_type="boolean",
        default=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="onboarding",
        key="mode",
        description="Onboarding mode: 'disabled', 'rules_only', 'rules_with_questions', 'questions_only'",
        value_type="string",
        default="rules_with_questions",
    )
)
settings_service.register(
    SettingDefinition(
        scope="onboarding",
        key="completion_role_id",
        description="Role to assign after onboarding completion.",
        value_type="role",
        default=None,  # Optional - no role assigned if not set
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="onboarding",
        key="join_role_id",
        description="Temporary role to assign immediately when a user joins (removed after onboarding/verification).",
        value_type="role",
        default=None,  # Optional - no role assigned if not set
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="announcements_channel_id",
        description="Channel monitored for auto-reminder embeds.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="reminder_offset_minutes",
        description="Number of minutes before the event that the reminder is scheduled.",
        value_type="int",
        default=60,
        min_value=0,
        max_value=4320,
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="gpt_fallback_enabled",
        description="Enable LLM fallback parsing when structured parsing fails.",
        value_type="bool",
        default=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="failed_parse_log_channel_id",
        description="Channel to log failed parse attempts (optional, defaults to log channel).",
        value_type="channel",
        default=0,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="automod",
        key="enabled",
        description="Enable auto-moderation for this guild.",
        value_type="bool",
        default=False,
    )
)
settings_service.register(
    SettingDefinition(
        scope="automod",
        key="log_channel_id",
        description="Channel for auto-mod violation logs.",
        value_type="channel",
        default=0,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="automod",
        key="warn_message",
        description="Default warning message for auto-mod violations.",
        value_type="string",
        default="⚠️ Your message violates server rules.",
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="non_embed_enabled",
        description="Enable parsing of non-embed messages (plain text messages) for reminders.",
        value_type="bool",
        default=False,
    )
)
settings_service.register(
    SettingDefinition(
        scope="embedwatcher",
        key="process_bot_messages",
        description="Enable processing of embeds/messages sent by the bot itself (e.g., from /embed command).",
        value_type="bool",
        default=False,
    )
)
settings_service.register(
    SettingDefinition(
        scope="ticketbot",
        key="category_id",
        description="Category where new ticket channels are created.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="ticketbot",
        key="staff_role_id",
        description="Role that gets access to ticket channels.",
        value_type="role",
        default=None,  # Must be configured per guild
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="ticketbot",
        key="escalation_role_id",
        description="Role for ticket escalation.",
        value_type="role",
        default=None,  # Must be configured per guild
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="ticketbot",
        key="idle_days_threshold",
        description="Days of inactivity before sending idle reminder (default: 5).",
        value_type="int",
        default=5,
        min_value=1,
        max_value=30,
    )
)
settings_service.register(
    SettingDefinition(
        scope="ticketbot",
        key="auto_close_days_threshold",
        description="Days of inactivity before auto-closing ticket (default: 14).",
        value_type="int",
        default=14,
        min_value=1,
        max_value=90,
    )
)
# Default model depends on LLM_PROVIDER (grok-3 for Grok, gpt-3.5-turbo for OpenAI)
_default_llm_model = "grok-3" if getattr(config, "LLM_PROVIDER", "grok").strip().lower() == "grok" else "gpt-3.5-turbo"
settings_service.register(
    SettingDefinition(
        scope="gpt",
        key="model",
        description="Default AI model for Grok commands (e.g. grok-3).",
        value_type="str",
        default=_default_llm_model,
    )
)
settings_service.register(
    SettingDefinition(
        scope="gpt",
        key="temperature",
        description="Temperature (creativity) for Grok responses.",
        value_type="float",
        default=0.7,
        min_value=0.0,
        max_value=2.0,
    )
)
settings_service.register(
    SettingDefinition(
        scope="invites",
        key="enabled",
        description="Enable invite tracker functionality.",
        value_type="bool",
        default=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="invites",
        key="announcement_channel_id",
        description="Channel for automatic invite announcements.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="invites",
        key="with_inviter_template",
        description="Message template when an inviter is found.",
        value_type="str",
        default="{member} joined! {inviter} now has {count} invites.",
    )
)
settings_service.register(
    SettingDefinition(
        scope="invites",
        key="no_inviter_template",
        description="Message template when no inviter is found.",
        value_type="str",
        default="{member} joined, but no inviter data found.",
    )
)
settings_service.register(
    SettingDefinition(
        scope="gdpr",
        key="enabled",
        description="Enable GDPR handler for command and button.",
        value_type="bool",
        default=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="gdpr",
        key="channel_id",
        description="Channel where the GDPR document is posted.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="reminders",
        key="enabled",
        description="Enable reminders functionality.",
        value_type="bool",
        default=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="reminders",
        key="default_channel_id",
        description="Default channel for new reminders (optional).",
        value_type="channel",
        default=0,  # Must be configured per guild
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="reminders",
        key="allow_everyone_mentions",
        description="Allow @everyone mentions in reminders.",
        value_type="bool",
        default=False,  # Must be configured per guild
    )
)
settings_service.register(
    SettingDefinition(
        scope="system",
        key="log_level",
        description="Log verbosity level: verbose (all), normal (no debug), critical (errors + config only).",
        value_type="string",
        default="verbose",
        choices=["verbose", "normal", "critical"],
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="verified_role_id",
        description="Role to assign after successful AI verification.",
        value_type="role",
        default=None,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="category_id",
        description="Category where new verification channels are created.",
        value_type="channel",
        default=0,
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="vision_model",
        description="Vision-capable model for verification (e.g. grok-4-1-fast-reasoning).",
        value_type="str",
        default="grok-4-1-fast-reasoning",
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="ai_prompt_context",
        description="Extra context for the AI verifier (e.g. what a valid payment looks like for this community).",
        value_type="str",
        default="",
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="reference_image_channel_id",
        description="Channel where the reference payment screenshot is stored (managed by the bot).",
        value_type="channel",
        default=0,
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="reference_image_message_id",
        description="Message ID of the bot-stored reference payment screenshot.",
        value_type="str",
        default="",
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="max_payment_age_days",
        description="Maximum age in days a payment screenshot may be (default: 35). Payments older than this are rejected.",
        value_type="int",
        default=35,
    )
)
settings_service.register(
    SettingDefinition(
        scope="verification",
        key="reviewer_role_id",
        description="Role that gets tagged in the verification channel when manual review is required.",
        value_type="role",
        default=None,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="growth",
        key="log_channel_id",
        description="Channel where shared Growth Check-in posts are published.",
        value_type="channel",
        default=0,  # Must be configured per guild
    )
)
# --- Engagement module settings ---
for _eng_feature in ("challenges", "weekly", "badges", "streaks", "og"):
    settings_service.register(
        SettingDefinition(
            scope="engagement",
            key=f"{_eng_feature}_enabled",
            description=f"Enable the {_eng_feature} feature of the Engagement module.",
            value_type="bool",
            default=False,
        )
    )
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="challenge_winner_role_id",
        description="Role assigned to challenge winners.",
        value_type="role",
        default=None,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="weekly_award_channel_id",
        description="Channel where weekly award announcements are posted.",
        value_type="channel",
        default=0,
        allow_null=True,
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="weekly_food_channel_ids",
        description="Comma-separated channel IDs that count as food channels for weekly awards.",
        value_type="str",
        default="",
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="weekly_award_configs",
        description="JSON list of weekly award category definitions (key, label, subtitle, filter).",
        value_type="str",
        default="",
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="streaks_nicknames",
        description="Whether to append streak suffix to member nicknames (e.g. 'Name | 🔥 week 2').",
        value_type="bool",
        default=False,
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="og_cap",
        description="Maximum number of OG claims allowed.",
        value_type="int",
        default=50,
        min_value=1,
        max_value=10000,
    )
)
settings_service.register(
    SettingDefinition(
        scope="engagement",
        key="og_claim_text",
        description="Message text posted by /og setup when the OG claim message is created.",
        value_type="str",
        default="",
    )
)
# Badge role settings — one per well-known badge key
for _badge_key in ("winner", "og", "motivator", "foodfluencer", "sharpshooter", "star"):
    settings_service.register(
        SettingDefinition(
            scope="engagement",
            key=f"badge_role_{_badge_key}",
            description=f"Discord role linked to the '{_badge_key}' badge.",
            value_type="role",
            default=None,
            allow_null=True,
        )
    )

# Event: Bot is ready
@bot.event
async def on_ready():
    await bot.wait_until_ready()

    # Check if this is a reconnect (not first startup)
    from utils.lifecycle import StartupManager
    if StartupManager.is_first_startup():
        # First startup - full initialization already done in setup_hook()
        StartupManager._mark_startup_complete()
        logger.info(f"{bot.user} is online! ✅")
        log_operational_event(EventType.BOT_READY, f"{bot.user} is online", guild_id=None)
    elif StartupManager.consume_disconnect_seen():
        # Actual reconnect - we saw on_disconnect before this on_ready. Discord.py can fire
        # on_ready multiple times during initial connection; only run reconnect_phase after
        # a real disconnect to avoid unnecessary syncs and BOT_RECONNECT logging.
        startup_manager = StartupManager(bot)
        await startup_manager.reconnect_phase(bot)
        logger.info(f"{bot.user} reconnected! 🔄")
    else:
        logger.debug("on_ready: duplicate call (no disconnect seen), skipping")


set_bot_instance(bot)  # This also starts the Grok retry queue task


@bot.event
async def on_command_error(ctx, error):
    logger.error(f"⚠️ Error in command '{ctx.command}': {error}")
    
    # Track command error
    try:
        from utils.command_tracker import log_command_usage
        command_name = ctx.command.name if ctx.command else "unknown"
        guild_id = ctx.guild.id if ctx.guild else None
        await log_command_usage(
            guild_id=guild_id,
            user_id=ctx.author.id,
            command_name=command_name,
            command_type="text",
            success=False,
            error_message=str(error)[:500]
        )
    except Exception:
        pass  # Don't break error handling if tracking fails
    
    await ctx.send("❌ Oops! An error occurred. Please try again later.")


@bot.event
async def on_command_completion(ctx):
    """Track successful text command execution."""
    try:
        from utils.command_tracker import log_command_usage
        command_name = ctx.command.name if ctx.command else "unknown"
        guild_id = ctx.guild.id if ctx.guild else None
        await log_command_usage(
            guild_id=guild_id,
            user_id=ctx.author.id,
            command_name=command_name,
            command_type="text",
            success=True
        )
    except Exception:
        pass  # Don't break command execution if tracking fails


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: discord.app_commands.Command):
    """Track successful slash command execution."""
    try:
        from utils.command_tracker import log_command_usage
        guild_id = interaction.guild.id if interaction.guild else None
        await log_command_usage(
            guild_id=guild_id,
            user_id=interaction.user.id,
            command_name=command.name,
            command_type="slash",
            success=True
        )
    except Exception:
        pass  # Don't break command execution if tracking fails


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Handle app command errors globally."""
    # Extract common variables once
    guild_id = interaction.guild.id if interaction.guild else None
    command_name = interaction.command.name if interaction.command else "unknown"
    
    # Handle cooldown errors with user-friendly message
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        minutes = int(error.retry_after // 60)
        seconds = int(error.retry_after % 60)

        if minutes > 0:
            time_str = f"{minutes} minute{'s' if minutes != 1 else ''} and {seconds} second{'s' if seconds != 1 else ''}"
        else:
            time_str = f"{seconds} second{'s' if seconds != 1 else ''}"

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"⏸️ **Cooldown active**\n\nYou can use this command again in {time_str}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"⏸️ **Cooldown active**\n\nYou can use this command again in {time_str}.",
                    ephemeral=True,
                )
        except Exception as e:
            logger.debug(f"Failed to send cooldown message: {e}")

    # Handle permission/check failures with user-friendly message (not a real error)
    elif isinstance(error, discord.app_commands.CheckFailure):
        msg = str(error) if str(error) else "❌ You do not have permission to use this command."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Failed to send check failure message: {e}")
        logger.warning(f"CheckFailure in '/{command_name}' (guild={guild_id}, user={interaction.user.id}): {error}")

    # Log unexpected errors
    else:
        log_operational_event(
            EventType.COG_ERROR,
            f"Slash command error: /{command_name}: {str(error)[:200]}",
            guild_id=guild_id,
            details={
                "command": command_name,
                "user_id": interaction.user.id,
                "error_type": error.__class__.__name__,
                "error": str(error)[:500],
            },
        )
        logger.error(
            f"⚠️ Error in slash command '/{command_name}' (guild={guild_id}): {error}",
            exc_info=True,
        )
    
    # Track failed command execution
    try:
        from utils.command_tracker import log_command_usage
        await log_command_usage(
            guild_id=guild_id,
            user_id=interaction.user.id,
            command_name=command_name,
            command_type="slash",
            success=False,
            error_message=str(error)[:500]
        )
    except Exception:
        pass  # Don't break error handling if tracking fails


@bot.event
async def on_disconnect():
    """Handle bot disconnection - log only, don't shutdown.

    Note: This event fires on every disconnect, including temporary reconnects.
    Discord.py will automatically reconnect, and on_ready() will be called again.
    Only perform shutdown on actual bot.stop() or process termination.
    """
    from utils.lifecycle import StartupManager
    StartupManager.set_disconnect_seen()
    logger.info("⚠️ Bot disconnected from Discord (will attempt to reconnect automatically)")
    log_operational_event(EventType.BOT_DISCONNECT, "Bot disconnected (will reconnect automatically)", guild_id=None)


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Sync guild-only commands when bot joins a new guild."""
    from utils.command_sync import detect_guild_only_commands, safe_sync
    
    logger.info(f"🆕 Bot joined new guild: {guild.name} (ID: {guild.id})")
    
    # Check if we have guild-only commands
    has_guild_only = detect_guild_only_commands(bot)
    if has_guild_only:
        logger.info(f"🔄 Syncing guild-only commands for {guild.name}...")
        result = await safe_sync(bot, guild=guild, force=False)
        if result.success:
            logger.info(f"✅ Guild commands synced for {guild.name}: {result.command_count} commands")
            log_operational_event(
                EventType.GUILD_SYNC,
                f"Commands synced: {result.command_count} commands",
                guild_id=guild.id,
                details={"command_count": result.command_count, "sync_type": "guild_join"}
            )
        else:
            if result.cooldown_remaining:
                logger.info(f"⏸️ Guild sync skipped for {guild.name} (cooldown: {result.cooldown_remaining:.0f}s)")
                log_operational_event(
                    EventType.GUILD_SYNC,
                    "Sync skipped: cooldown active",
                    guild_id=guild.id,
                    details={"cooldown_remaining": result.cooldown_remaining, "sync_type": "guild_join"}
                )
            else:
                logger.warning(f"⚠️ Guild sync failed for {guild.name}: {result.error}")
                log_operational_event(
                    EventType.GUILD_SYNC,
                    f"Sync failed: {result.error}",
                    guild_id=guild.id,
                    details={"error": result.error, "sync_type": "guild_join"}
                )
    else:
        logger.debug(f"ℹ️ No guild-only commands detected, skipping sync for {guild.name}")

    # Send welcome FYI to fallback channel (no log channel configured yet)
    channel = None
    me = getattr(guild, "me", None)
    if me is not None:
        channel = guild.system_channel
        if not channel or not channel.permissions_for(me).send_messages:
            for ch in guild.text_channels:
                if ch.permissions_for(me).send_messages:
                    channel = ch
                    break
            else:
                channel = None
    if channel:
        from utils.fyi_tips import send_fyi_if_first
        await send_fyi_if_first(bot, guild.id, "first_guild_join", channel_id_override=channel.id)



async def setup_hook():
    from utils.lifecycle import StartupManager
    startup_manager = StartupManager(bot)
    # Store settings_service reference for StartupManager (created at module level)
    startup_manager.settings_service = settings_service
    await startup_manager.startup()



bot.setup_hook = setup_hook
# API server runs alongside for health checks and monitoring data to Mind
Thread(target=start_api, daemon=True).start()


# Override bot.close() to run graceful shutdown
_original_close = bot.close
async def close_with_shutdown():
    """Close bot with graceful shutdown sequence."""
    logger.info("🛑 Bot.close() called, running graceful shutdown...")
    from utils.lifecycle import ShutdownManager
    shutdown_manager = ShutdownManager(bot)
    await shutdown_manager.shutdown()
    await _original_close()

bot.close = close_with_shutdown

# Start bot: uses BOT_TOKEN_ACTIVE (BOT_TOKEN_TEST when USE_TEST_BOT=1, else BOT_TOKEN)
token: str | None = getattr(config, "BOT_TOKEN_ACTIVE", None)
if not token:
    raise RuntimeError("BOT_TOKEN (or BOT_TOKEN_TEST when USE_TEST_BOT=1) is not set in the config.")
bot.run(token)
