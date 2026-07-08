import re
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.configuration_automod import (
    AUTOMOD_ACTION_CHOICES,
    check_advanced_action_premium,
    normalize_automod_action_type,
    validate_rule_update_fields,
)
from cogs.configuration_ui import SETUP_STEPS, ReorderQuestionsModal, SetupWizardView
from cogs.reaction_roles import StartOnboardingView
from utils.automod_rules import ActionType, RuleProcessor, RuleType
from utils.cog_base import AlphaCog
from utils.db_helpers import acquire_safe
from utils.embed_builder import EmbedBuilder
from utils.fyi_tips import FYI_KEYS as FYI_TIPS_KEYS
from utils.gdpr_helpers import GDPRView, build_gdpr_text
from utils.logger import log_guild_action, log_with_guild, logger
from utils.premium_guard import guild_has_premium
from utils.sanitizer import safe_embed_text
from utils.settings_service import SettingDefinition
from utils.validators import validate_admin


def requires_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)
        if is_admin:
            return True
        raise app_commands.CheckFailure("You need administrator permissions for this command.")
    return app_commands.check(predicate)
class Configuration(AlphaCog):
    config = app_commands.Group(
        name="config",
        description="Manage bot settings",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )
    _admin_perms = discord.Permissions(administrator=True)

    system_group = app_commands.Group(
        name="system",
        description="System settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    embedwatcher_group = app_commands.Group(
        name="embedwatcher",
        description="Embed watcher settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    ticketbot_group = app_commands.Group(
        name="ticketbot",
        description="TicketBot settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    gpt_group = app_commands.Group(
        name="gpt",
        description="Grok / AI settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    invites_group = app_commands.Group(
        name="invites",
        description="Invite tracker settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    reminders_group = app_commands.Group(
        name="reminders",
        description="Reminder settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    gdpr_group = app_commands.Group(
        name="gdpr",
        description="GDPR settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    onboarding_group = app_commands.Group(
        name="onboarding",
        description="Onboarding configuration",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    fyi_group = app_commands.Group(
        name="fyi",
        description="Contextual FYI tips for admins",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    verification_group = app_commands.Group(
        name="verification",
        description="Verification settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    automod_group = app_commands.Group(
        name="automod",
        description="Auto-moderation settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    growth_group = app_commands.Group(
        name="growth",
        description="Growth Check-in settings",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    engagement_group = app_commands.Group(
        name="engagement",
        description="Engagement module settings (challenges, weekly awards, streaks, badges, OG claims)",
        default_permissions=_admin_perms,
        guild_only=True,
    )
    agents_group = app_commands.Group(
        name="agents",
        description="Alphapy personal agent settings (/agent commands)",
        default_permissions=_admin_perms,
        guild_only=True,
        parent=config,
    )
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.rule_processor = RuleProcessor(bot)

        # Validate database pool availability
        from utils.db_helpers import get_bot_db_pool
        if get_bot_db_pool(bot) is None:
            from utils.logger import logger
            logger.warning("⚠️ Database pool not available - auto-moderation features will be limited")

    @config.command(name="scopes", description="Show all available setting scopes")
    @requires_admin()
    async def config_scopes(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        scopes = self.settings.scopes()
        if not scopes:
            await interaction.followup.send("⚠️ No scopes registered.", ephemeral=True)
            return
        lines = ["📁 **Available scopes**:"]
        lines.extend(f"• `{scope}`" for scope in scopes)
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @config.command(
        name="start",
        description="Start the interactive server setup (choose or skip channel/role per step)",
    )
    @requires_admin()
    async def config_start(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command only works in a server.",
                ephemeral=True,
            )
            return
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        view = SetupWizardView(cog=self, guild_id=guild_id, user_id=user_id, steps=SETUP_STEPS)
        first_step = view._current_step()
        if not first_step:
            await interaction.response.send_message(
                "⚠️ No setup steps defined.",
                ephemeral=True,
            )
            return
        embed = view._build_step_embed(first_step)
        view._clear_and_add_components(first_step)
        message = await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = cast(Any, message)
        log_with_guild(f"Setup wizard started (guild_id={guild_id}, user_id={user_id})", guild_id, "debug")

    @fyi_group.command(name="reset", description="Clear a FYI flag.")
    @app_commands.choices(key=[app_commands.Choice(name=k, value=k) for k in sorted(FYI_TIPS_KEYS)])
    @requires_admin()
    async def fyi_reset(
        self,
        interaction: discord.Interaction,
        key: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        from utils.fyi_tips import reset_fyi
        if key not in FYI_TIPS_KEYS:
            await interaction.response.send_message(f"❌ Unknown FYI key: `{key}`. Use one of: {', '.join(sorted(FYI_TIPS_KEYS))}.", ephemeral=True)
            return
        ok = await reset_fyi(self.bot, interaction.guild.id, key)
        if ok:
            await interaction.response.send_message(f"✅ FYI flag `{key}` cleared. The next time that event happens, the tip will be sent again.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Could not clear `{key}`.", ephemeral=True)

    @fyi_group.command(name="send", description="Send a one-off FYI tip to the log channel (admin)")
    @app_commands.choices(key=[app_commands.Choice(name=k, value=k) for k in sorted(FYI_TIPS_KEYS)])
    @requires_admin()
    async def fyi_send(
        self,
        interaction: discord.Interaction,
        key: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from utils.fyi_tips import force_send_fyi
        if key not in FYI_TIPS_KEYS:
            await interaction.followup.send(f"❌ Unknown FYI key: `{key}`. Use one of: {', '.join(sorted(FYI_TIPS_KEYS))}.", ephemeral=True)
            return
        sent = await force_send_fyi(self.bot, interaction.guild.id, key, mark_as_sent=True)
        if sent:
            await interaction.followup.send(f"✅ FYI `{key}` sent to the log channel (and marked as sent).", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not send (e.g. no log channel set or channel not found).", ephemeral=True)

    @system_group.command(name="show", description="Show system settings")
    @requires_admin()
    async def system_show(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "system", "🛠️ System settings")
    @system_group.command(name="set_log_channel", description="Set the log channel")
    @requires_admin()
    @app_commands.describe(channel="Channel for bot logs. Leave empty to reset to default.")
    async def system_set_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("system", "log_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Log channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting updated",
                f"`system.log_channel_id` set to {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("system", "log_channel_id", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("system", "log_channel_id", interaction.guild.id)
            formatted = f"<#{default_value}>" if default_value else "—"
            await interaction.followup.send(f"↩️ Log channel reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting reset",
                f"`system.log_channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @system_group.command(name="set_rules_channel", description="Set the rules/onboarding channel")
    @requires_admin()
    @app_commands.describe(channel="Rules and onboarding channel. Leave empty to reset to default.")
    async def system_set_rules_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("system", "rules_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Rules channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting updated",
                f"`system.rules_channel_id` set to {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("system", "rules_channel_id", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("system", "rules_channel_id", interaction.guild.id)
            formatted = f"<#{default_value}>" if default_value else "—"
            await interaction.followup.send(f"↩️ Rules channel reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting reset",
                f"`system.rules_channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @system_group.command(name="set_log_level", description="Set log verbosity")
    @requires_admin()
    @app_commands.describe(level="Log verbosity level. Leave empty to reset to default.")
    @app_commands.choices(level=[
        app_commands.Choice(name="Verbose - All logs", value="verbose"),
        app_commands.Choice(name="Normal - No debug", value="normal"),
        app_commands.Choice(name="Critical - Errors + config only", value="critical"),
    ])
    async def system_set_log_level(
        self,
        interaction: discord.Interaction,
        level: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if level is not None:
            await self.settings.set("system", "log_level", level.value, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Log level set to **{level.name}**.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting updated",
                f"`system.log_level` set to `{level.value}` by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("system", "log_level", interaction.guild.id, interaction.user.id)
            default_level = self.settings.get("system", "log_level", interaction.guild.id)
            await interaction.followup.send(f"↩️ Log level reset to default: **{default_level}**.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting reset",
                f"`system.log_level` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @embedwatcher_group.command(name="show", description="Show embed watcher settings")
    @requires_admin()
    async def embedwatcher_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "embedwatcher", "📣 Embed watcher settings")

    @embedwatcher_group.command(name="set_announcements", description="Set the channel to monitor")
    @requires_admin()
    @app_commands.describe(channel="Channel to watch for event embeds. Leave empty to reset.")
    async def embedwatcher_set_announcements(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("embedwatcher", "announcements_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Announcement channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.announcements_channel_id` → {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("embedwatcher", "announcements_channel_id", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("embedwatcher", "announcements_channel_id", interaction.guild.id)
            formatted = f"<#{default_value}>" if default_value else "—"
            await interaction.followup.send(f"↩️ Announcement channel reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.announcements_channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @embedwatcher_group.command(name="set_offset", description="Set reminder offset in minutes")
    @requires_admin()
    @app_commands.describe(minutes="Offset in minutes (0–4320). Leave empty to reset to default.")
    async def embedwatcher_set_offset(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 0, 4320] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if minutes is not None:
            await self.settings.set("embedwatcher", "reminder_offset_minutes", minutes, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Reminder offset set to {minutes} minutes.", ephemeral=True)
            await self._send_audit_log(
                "🔁 Reminder offset",
                f"`embedwatcher.reminder_offset_minutes` → {minutes} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("embedwatcher", "reminder_offset_minutes", interaction.guild.id, interaction.user.id)
            default_minutes = self.settings.get("embedwatcher", "reminder_offset_minutes", interaction.guild.id)
            await interaction.followup.send(f"↩️ Reminder offset reset to default: {default_minutes} minutes.", ephemeral=True)
            await self._send_audit_log(
                "🔁 Reminder offset",
                f"`embedwatcher.reminder_offset_minutes` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @embedwatcher_group.command(name="set_non_embed", description="Enable/disable non-embed message parsing")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable. Leave empty to reset to default.")
    async def embedwatcher_set_non_embed(
        self,
        interaction: discord.Interaction,
        enabled: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if enabled is not None:
            await self.settings.set("embedwatcher", "non_embed_enabled", enabled, interaction.guild.id, interaction.user.id)
            status = "enabled" if enabled else "disabled"
            await interaction.followup.send(f"✅ Non-embed message parsing {status}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.non_embed_enabled` → {enabled} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("embedwatcher", "non_embed_enabled", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("embedwatcher", "non_embed_enabled", interaction.guild.id)
            status = "enabled" if default_value else "disabled"
            await interaction.followup.send(f"↩️ Non-embed message parsing reset to default: {status}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.non_embed_enabled` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @embedwatcher_group.command(name="set_process_bot_messages", description="Enable/disable processing of bot messages")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable. Leave empty to reset to default.")
    async def embedwatcher_set_process_bot_messages(
        self,
        interaction: discord.Interaction,
        enabled: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if enabled is not None:
            await self.settings.set("embedwatcher", "process_bot_messages", enabled, interaction.guild.id, interaction.user.id)
            status = "enabled" if enabled else "disabled"
            await interaction.followup.send(f"✅ Processing of bot's own messages {status}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.process_bot_messages` → {enabled} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("embedwatcher", "process_bot_messages", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("embedwatcher", "process_bot_messages", interaction.guild.id)
            status = "enabled" if default_value else "disabled"
            await interaction.followup.send(f"↩️ Processing of bot messages reset to default: {status}.", ephemeral=True)
            await self._send_audit_log(
                "🔔 Embed watcher",
                f"`embedwatcher.process_bot_messages` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @ticketbot_group.command(name="show", description="Show TicketBot settings")
    @requires_admin()
    async def ticketbot_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "ticketbot", "🎟️ TicketBot settings")
    @ticketbot_group.command(name="set_category", description="Set the ticket category")
    @requires_admin()
    @app_commands.describe(category="Category for ticket channels. Leave empty to reset.")
    async def ticketbot_set_category(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if category is not None:
            await self.settings.set("ticketbot", "category_id", category.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Ticket category set to {category.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.category_id` → {category.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("ticketbot", "category_id", interaction.guild.id, interaction.user.id)
            default_category_id = self.settings.get("ticketbot", "category_id", interaction.guild.id)
            formatted = f"<#{default_category_id}>" if default_category_id else "—"
            await interaction.followup.send(f"↩️ Ticket category reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.category_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @ticketbot_group.command(name="set_staff_role", description="Set the support role")
    @requires_admin()
    @app_commands.describe(role="Role assigned to support staff. Leave empty to reset.")
    async def ticketbot_set_staff_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("ticketbot", "staff_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Support role set to {role.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.staff_role_id` → {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("ticketbot", "staff_role_id", interaction.guild.id, interaction.user.id)
            default_role_id = self.settings.get("ticketbot", "staff_role_id", interaction.guild.id)
            formatted = f"<@&{default_role_id}>" if default_role_id else "—"
            await interaction.followup.send(f"↩️ Support role reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.staff_role_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @ticketbot_group.command(name="set_escalation_role", description="Set the escalation role")
    @requires_admin()
    @app_commands.describe(role="Role for escalated tickets. Leave empty to reset.")
    async def ticketbot_set_escalation_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("ticketbot", "escalation_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Escalation role set to {role.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.escalation_role_id` → {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("ticketbot", "escalation_role_id", interaction.guild.id, interaction.user.id)
            default_role_id = self.settings.get("ticketbot", "escalation_role_id", interaction.guild.id)
            formatted = f"<@&{default_role_id}>" if default_role_id else "—"
            await interaction.followup.send(f"↩️ Escalation role reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🎟️ TicketBot",
                f"`ticketbot.escalation_role_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @gpt_group.command(name="show", description="Show Grok / AI settings")
    @requires_admin()
    async def gpt_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "gpt", "🤖 Grok / AI settings")
    @gpt_group.command(name="set_model", description="Set the Grok model (bot owner only)")
    @app_commands.describe(
        model="Grok model name (e.g. grok-3). Leave empty to reset to default.",
        guild_id="Target guild ID — use this to set the model for a guild you are not in",
    )
    async def gpt_set_model(self, interaction: discord.Interaction, model: str | None = None, guild_id: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id not in config.OWNER_IDS:
            await interaction.followup.send("❌ This command is restricted to bot owners.", ephemeral=True)
            return
        effective_guild_id, guild_id_error = self._resolve_guild_id(interaction, guild_id)
        if guild_id_error:
            await interaction.followup.send(guild_id_error, ephemeral=True)
            return
        guild_label = f"guild `{effective_guild_id}`" if effective_guild_id != interaction.guild_id else "this guild"
        if model is not None:
            model_clean = model.strip()
            if not model_clean:
                await interaction.followup.send("❌ Model name cannot be empty.", ephemeral=True)
                return
            await self.settings.set("gpt", "model", model_clean, effective_guild_id, interaction.user.id)
            await interaction.followup.send(f"✅ Grok model set to `{model_clean}` for {guild_label}.", ephemeral=True)
            await self._send_audit_log(
                "🤖 Grok",
                f"`gpt.model` → `{model_clean}` for guild `{effective_guild_id}` by {interaction.user.mention}.",
                interaction.guild_id,
            )
        else:
            await self.settings.clear("gpt", "model", effective_guild_id, interaction.user.id)
            default_model = self.settings.get("gpt", "model", effective_guild_id)
            await interaction.followup.send(f"↩️ Grok model reset to default (`{default_model}`) for {guild_label}.", ephemeral=True)
            await self._send_audit_log(
                "🤖 Grok",
                f"`gpt.model` reset to default for guild `{effective_guild_id}` by {interaction.user.mention}.",
                interaction.guild_id,
            )

    @gpt_group.command(name="set_temperature", description="Set the Grok temperature")
    @requires_admin()
    @app_commands.describe(temperature="Temperature 0.0–2.0. Leave empty to reset to default.")
    async def gpt_set_temperature(
        self,
        interaction: discord.Interaction,
        temperature: app_commands.Range[float, 0.0, 2.0] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if temperature is not None:
            await self.settings.set("gpt", "temperature", float(temperature), interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Grok temperature set to `{temperature}`.", ephemeral=True)
            await self._send_audit_log(
                "🤖 Grok",
                f"`gpt.temperature` → {temperature} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("gpt", "temperature", interaction.guild.id, interaction.user.id)
            default_temp = self.settings.get("gpt", "temperature", interaction.guild.id)
            await interaction.followup.send(f"↩️ Grok temperature reset to default: `{default_temp}`.", ephemeral=True)
            await self._send_audit_log(
                "🤖 Grok",
                f"`gpt.temperature` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @invites_group.command(name="show", description="Show invite tracker settings")
    @requires_admin()
    async def invites_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "invites", "🎉 Invite tracker settings")
    @invites_group.command(name="toggle", description="Enable or disable the invite tracker")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable.")
    async def invites_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await self.settings.set("invites", "enabled", enabled, interaction.guild.id, interaction.user.id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Invite tracker {state}.", ephemeral=True)
        await self._send_audit_log(
            "🎉 Invites",
            f"`invites.enabled` → {enabled} by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @invites_group.command(name="set_channel", description="Set the invite announcement channel")
    @requires_admin()
    @app_commands.describe(channel="Channel for invite announcements. Leave empty to reset.")
    async def invites_set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("invites", "announcement_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Invite announcements channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🎉 Invites",
                f"`invites.announcement_channel_id` → {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("invites", "announcement_channel_id", interaction.guild.id, interaction.user.id)
            default_channel = self.settings.get("invites", "announcement_channel_id", interaction.guild.id)
            formatted = f"<#{default_channel}>" if default_channel else "—"
            await interaction.followup.send(f"↩️ Invite channel reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🎉 Invites",
                f"`invites.announcement_channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id
        )
    @invites_group.command(name="set_template", description="Set or reset invite message template")
    @requires_admin()
    @app_commands.describe(
        variant="Template variant to update.",
        template="Message template. Leave empty to reset to default.",
    )
    @app_commands.choices(
        variant=[
            app_commands.Choice(name="With inviter", value="with"),
            app_commands.Choice(name="Without inviter", value="without"),
        ]
    )
    async def invites_set_template(
        self,
        interaction: discord.Interaction,
        variant: app_commands.Choice[str],
        template: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        key = "with_inviter_template" if variant.value == "with" else "no_inviter_template"
        if template is not None:
            await self.settings.set("invites", key, template, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Invite template for `{variant.name}` updated.", ephemeral=True)
            await self._send_audit_log(
                "🎉 Invites",
                f"`invites.{key}` set by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("invites", key, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"↩️ Invite template for `{variant.name}` reset to default.", ephemeral=True)
            await self._send_audit_log(
                "🎉 Invites",
                f"`invites.{key}` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @reminders_group.command(name="show", description="Show reminder settings")
    @requires_admin()
    async def reminders_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "reminders", "⏰ Reminder settings")
    @reminders_group.command(name="toggle", description="Enable or disable reminders")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable.")
    async def reminders_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await self.settings.set("reminders", "enabled", enabled, interaction.guild.id, interaction.user.id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Reminders {state}.", ephemeral=True)
        await self._send_audit_log(
            "⏰ Reminders",
            f"`reminders.enabled` → {enabled} by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @reminders_group.command(name="set_default_channel", description="Set default reminder channel")
    @requires_admin()
    @app_commands.describe(channel="Default channel for reminders. Leave empty to reset.")
    async def reminders_set_default_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("reminders", "default_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Default reminder channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "⏰ Reminders",
                f"`reminders.default_channel_id` → {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("reminders", "default_channel_id", interaction.guild.id, interaction.user.id)
            default_value = self.settings.get("reminders", "default_channel_id", interaction.guild.id)
            formatted = f"<#{default_value}>" if default_value else "—"
            await interaction.followup.send(f"↩️ Default channel reset to: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "⏰ Reminders",
                f"`reminders.default_channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )
    @reminders_group.command(name="set_everyone", description="Allow or disallow @everyone in reminders")
    @requires_admin()
    async def reminders_set_everyone(
        self,
        interaction: discord.Interaction,
        allow: bool,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await self.settings.set("reminders", "allow_everyone_mentions", allow, interaction.guild.id, interaction.user.id)
        status = "allowed" if allow else "disabled"
        await interaction.followup.send(
            f"✅ @everyone is now {status} for reminders.",
            ephemeral=True,
        )
        await self._send_audit_log(
            "⏰ Reminders",
            f"`reminders.allow_everyone_mentions` → {allow} by {interaction.user.mention}.",
            interaction.guild.id
        )
    @verification_group.command(name="show", description="Show verification settings")
    @requires_admin()
    async def verification_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "verification", "✅ Verification settings")

    @verification_group.command(name="set_verified_role", description="Set role given after verification")
    @requires_admin()
    @app_commands.describe(role="Role to assign after verification. Leave empty to reset.")
    async def verification_set_verified_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("verification", "verified_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Verified role set to {role.mention}.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.verified_role_id` → {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "verified_role_id", interaction.guild.id, interaction.user.id)
            default_role_id = self.settings.get("verification", "verified_role_id", interaction.guild.id)
            formatted = f"<@&{default_role_id}>" if default_role_id else "—"
            await interaction.followup.send(f"↩️ Verified role reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.verified_role_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(name="set_category", description="Set category for verification channels")
    @requires_admin()
    @app_commands.describe(category="Category for verification channels. Leave empty to reset.")
    async def verification_set_category(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if category is not None:
            await self.settings.set("verification", "category_id", category.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Verification category set to {category.mention}.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.category_id` → {category.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "category_id", interaction.guild.id, interaction.user.id)
            default_category_id = self.settings.get("verification", "category_id", interaction.guild.id)
            formatted = f"<#{default_category_id}>" if default_category_id else "—"
            await interaction.followup.send(f"↩️ Verification category reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.category_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(name="set_vision_model", description="Set the vision model for verification")
    @requires_admin()
    @app_commands.describe(model="Vision-capable model name. Leave empty to reset to default.")
    async def verification_set_vision_model(
        self,
        interaction: discord.Interaction,
        model: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if model is not None:
            model_clean = model.strip()
            if not model_clean:
                await interaction.followup.send("❌ Model name cannot be empty.", ephemeral=True)
                return
            await self.settings.set("verification", "vision_model", model_clean, interaction.guild.id, interaction.user.id)
            text_only_models = {"grok-3", "grok-3-mini", "grok-3-fast", "grok-2", "grok-2-mini"}
            vision_note = (
                "\n⚠️ This model is text-only and does not support image inputs — verification will fail."
            ) if model_clean.lower() in text_only_models else ""
            await interaction.followup.send(f"✅ Verification vision model set to `{model_clean}`.{vision_note}", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.vision_model` → `{model_clean}` by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "vision_model", interaction.guild.id, interaction.user.id)
            default_model = self.settings.get("verification", "vision_model", interaction.guild.id)
            formatted = f"`{default_model}`" if default_model else "—"
            await interaction.followup.send(f"↩️ Verification vision model reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.vision_model` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(
        name="set_ai_prompt_context",
        description="Set extra AI verifier context.",
    )
    @requires_admin()
    @app_commands.describe(context="Context shown to the AI with every screenshot (max 1000 chars). Leave empty to clear.")
    async def verification_set_ai_prompt_context(
        self,
        interaction: discord.Interaction,
        context: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if context is not None:
            context_clean = context.strip()[:1000]
            if not context_clean:
                await interaction.followup.send("❌ Context cannot be empty.", ephemeral=True)
                return
            await self.settings.set("verification", "ai_prompt_context", context_clean, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(
                f"✅ AI prompt context set:\n> {safe_embed_text(context_clean, 200)}",
                ephemeral=True,
            )
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.ai_prompt_context` updated by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "ai_prompt_context", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ AI prompt context cleared.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.ai_prompt_context` cleared by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(
        name="set_reviewer_role",
        description="Role to tag for manual review.",
    )
    @requires_admin()
    @app_commands.describe(role="Role to ping on manual review. Leave empty to clear.")
    async def verification_set_reviewer_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("verification", "reviewer_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(
                f"✅ Reviewer role set to {role.mention}. They will be tagged when manual verification review is needed.",
                ephemeral=True,
            )
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.reviewer_role_id` → {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "reviewer_role_id", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ Reviewer role cleared. Manual review notifications will no longer tag a role.", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.reviewer_role_id` cleared by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(
        name="set_max_payment_age",
        description="Set payment screenshot max age in days.",
    )
    @requires_admin()
    @app_commands.describe(days="Max age in days (1–365). Leave empty to reset to default (35).")
    async def verification_set_max_payment_age(
        self,
        interaction: discord.Interaction,
        days: int | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if days is not None:
            if not 1 <= days <= 365:
                await interaction.followup.send("❌ Days must be between 1 and 365.", ephemeral=True)
                return
            await self.settings.set("verification", "max_payment_age_days", days, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(
                f"✅ Payment age limit set to **{days} days**. Screenshots older than that will be rejected.",
                ephemeral=True,
            )
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.max_payment_age_days` → `{days}` by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("verification", "max_payment_age_days", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ Payment age limit reset to default (35 days).", ephemeral=True)
            await self._send_audit_log(
                "✅ Verification",
                f"`verification.max_payment_age_days` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @verification_group.command(
        name="set_reference_image",
        description="Upload a reference screenshot.",
    )
    @requires_admin()
    @app_commands.describe(image="A clear example of a valid payment confirmation for your community.")
    async def verification_set_reference_image(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()

        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.followup.send("❌ Please attach an image file (PNG, JPG, etc.).", ephemeral=True)
            return

        # Post the image to the log channel so the URL stays refreshable via fetch_message
        log_channel_id = self.settings_helper.get_int("system", "log_channel_id", interaction.guild.id, fallback=0)
        storage_channel = self.bot.get_channel(log_channel_id) if log_channel_id else interaction.channel
        if not storage_channel or not hasattr(storage_channel, "send"):
            await interaction.followup.send(
                "❌ Could not find a channel to store the reference image. Please configure a log channel first.",
                ephemeral=True,
            )
            return

        import discord as _discord
        storage_text_channel = cast(_discord.TextChannel, storage_channel)

        try:
            file = await image.to_file()
            stored_msg = await storage_text_channel.send(
                content=f"🖼️ **Verification reference image** — uploaded by {interaction.user.mention} for `/config verification`",
                file=file,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Could not store the reference image: {safe_embed_text(str(e), 200)}", ephemeral=True)
            return

        await self.settings.set(
            "verification", "reference_image_channel_id", stored_msg.channel.id,
            interaction.guild.id, interaction.user.id,
        )
        await self.settings.set(
            "verification", "reference_image_message_id", str(stored_msg.id),
            interaction.guild.id, interaction.user.id,
        )

        await interaction.followup.send(
            f"✅ Reference image saved. The AI will now compare every submitted screenshot against this example.\n"
            f"Stored in: {storage_text_channel.mention}",
            ephemeral=True,
        )
        await self._send_audit_log(
            "✅ Verification",
            f"`verification.reference_image` set by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @verification_group.command(name="reset_reference_image", description="Remove reference screenshot.")
    @requires_admin()
    async def verification_reset_reference_image(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()

        channel_id = self.settings_helper.get_int("verification", "reference_image_channel_id", interaction.guild.id, fallback=0)
        message_id_str = self.settings_helper.get_str("verification", "reference_image_message_id", interaction.guild.id, fallback="")

        # Best-effort delete the stored message
        if channel_id and message_id_str:
            try:
                ch = self.bot.get_channel(channel_id)
                if ch and hasattr(ch, "fetch_message"):
                    import discord as _discord
                    text_ch = cast(_discord.TextChannel, ch)
                    msg = await text_ch.fetch_message(int(message_id_str))
                    await msg.delete()
            except Exception:
                pass

        await self.settings.clear("verification", "reference_image_channel_id", interaction.guild.id, interaction.user.id)
        await self.settings.clear("verification", "reference_image_message_id", interaction.guild.id, interaction.user.id)
        await interaction.followup.send("↩️ Reference image removed.", ephemeral=True)
        await self._send_audit_log(
            "✅ Verification",
            f"`verification.reference_image` cleared by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @gdpr_group.command(name="show", description="Show GDPR settings")
    @requires_admin()
    async def gdpr_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "gdpr", "🔒 GDPR settings")
    @gdpr_group.command(name="toggle", description="Enable or disable GDPR functionality")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable.")
    async def gdpr_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await self.settings.set("gdpr", "enabled", enabled, interaction.guild.id, interaction.user.id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ GDPR functionality {state}.", ephemeral=True)
        await self._send_audit_log(
            "🔒 GDPR",
            f"`gdpr.enabled` → {enabled} by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @gdpr_group.command(name="set_channel", description="Set the GDPR channel")
    @requires_admin()
    @app_commands.describe(channel="GDPR request channel. Leave empty to reset.")
    async def gdpr_set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if channel is not None:
            await self.settings.set("gdpr", "channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ GDPR channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "🔒 GDPR",
                f"`gdpr.channel_id` → {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("gdpr", "channel_id", interaction.guild.id, interaction.user.id)
            default_channel = self.settings.get("gdpr", "channel_id", interaction.guild.id)
            formatted = f"<#{default_channel}>" if default_channel else "—"
            await interaction.followup.send(f"↩️ GDPR channel reset to default: {formatted}.", ephemeral=True)
            await self._send_audit_log(
                "🔒 GDPR",
                f"`gdpr.channel_id` reset to default by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @gdpr_group.command(name="set_acceptance_role", description="Role assigned when a member accepts the GDPR agreement")
    @requires_admin()
    @app_commands.describe(role="Role to assign on acceptance. Leave empty to clear.")
    async def gdpr_set_acceptance_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("gdpr", "acceptance_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(
                f"✅ GDPR acceptance role set to {role.mention}. Members will receive this role when they click \"I Agree\".",
                ephemeral=True,
            )
            await self._send_audit_log(
                "🔒 GDPR",
                f"`gdpr.acceptance_role_id` → {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("gdpr", "acceptance_role_id", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ GDPR acceptance role cleared. No role will be assigned on agreement.", ephemeral=True)
            await self._send_audit_log(
                "🔒 GDPR",
                f"`gdpr.acceptance_role_id` cleared by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @gdpr_group.command(name="post", description="Post the GDPR agreement to the configured channel")
    @requires_admin()
    async def gdpr_post(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()

        enabled = self.settings.get("gdpr", "enabled", interaction.guild.id)
        if not enabled:
            await interaction.followup.send("⚠️ GDPR functionality is currently disabled.", ephemeral=True)
            return

        channel_id = self.settings.get("gdpr", "channel_id", interaction.guild.id)
        if not channel_id:
            await interaction.followup.send(
                "⚠️ No GDPR channel configured. Use `/gdpr set_channel` first.", ephemeral=True
            )
            return

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                channel = None
        if channel is None:
            await interaction.followup.send("⚠️ GDPR channel not found.", ephemeral=True)
            return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("⚠️ GDPR channel must be a text channel.", ephemeral=True)
            return

        gdpr_text = build_gdpr_text(interaction.guild.name)
        embed = EmbedBuilder.info(title="GDPR Data Processing Agreement", description=gdpr_text)
        message = await channel.send(embed=embed, view=GDPRView(self.bot))
        await message.pin()
        await interaction.followup.send(
            f"✅ GDPR agreement posted and pinned in {channel.mention}.", ephemeral=True
        )
        await self._send_audit_log(
            "🔒 GDPR",
            f"GDPR agreement posted in {channel.mention} by {interaction.user.mention}.",
            interaction.guild.id,
        )

    @onboarding_group.command(name="show", description="Show onboarding configuration")
    @requires_admin()
    async def onboarding_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()

        enabled = self.settings.get("onboarding", "enabled", interaction.guild.id)
        mode = self.settings.get("onboarding", "mode", interaction.guild.id)
        completion_role_id = self.settings.get("onboarding", "completion_role_id", interaction.guild.id)

        embed = discord.Embed(title="📝 Onboarding configuration", color=0x5865F2)
        embed.add_field(name="Enabled", value="✅ Yes" if enabled else "❌ No", inline=True)
        embed.add_field(name="Mode", value=f"`{mode}`", inline=True)

        join_role_id = self.settings.get("onboarding", "join_role_id", interaction.guild.id)
        try:
            join_role_id_int = int(join_role_id) if join_role_id else 0
        except (TypeError, ValueError):
            join_role_id_int = 0
        if join_role_id_int:
            join_role = interaction.guild.get_role(join_role_id_int)
            embed.add_field(name="Join role", value=join_role.mention if join_role else f"<@&{join_role_id_int}>", inline=True)
        else:
            embed.add_field(name="Join role", value="Not set", inline=True)

        if completion_role_id and completion_role_id != 0:
            role = interaction.guild.get_role(completion_role_id)
            embed.add_field(name="Completion role", value=role.mention if role else f"<@&{completion_role_id}>", inline=True)
        else:
            embed.add_field(name="Completion role", value="Not set", inline=True)

        # Rules (when mode includes them)
        if mode in ["rules_only", "rules_with_questions"]:
            onboarding_cog = self.bot.get_cog("Onboarding")
            if onboarding_cog:
                rules = await onboarding_cog.get_guild_rules(interaction.guild.id)
                if rules:
                    rule_lines = []
                    for i, rule in enumerate(rules, 1):
                        t = rule["title"] if isinstance(rule, dict) else rule[0]
                        extras = []
                        if isinstance(rule, dict):
                            if rule.get("thumbnail_url"):
                                extras.append("thumb")
                            if rule.get("image_url"):
                                extras.append("img")
                        suffix = f" [{', '.join(extras)}]" if extras else ""
                        rule_lines.append(f"{i}. **{t}**{suffix}")
                    embed.add_field(name=f"Rules ({len(rules)})", value="\n".join(rule_lines[:10]), inline=False)
                else:
                    embed.add_field(name="Rules", value="⚠️ None configured", inline=False)
            else:
                embed.add_field(name="Rules", value="❌ Onboarding module not loaded", inline=False)

        # Questions (when mode includes them)
        if mode in ["rules_with_questions", "questions_only"]:
            onboarding_cog = self.bot.get_cog("Onboarding")
            if onboarding_cog:
                questions = await onboarding_cog.get_guild_questions(interaction.guild.id)
                if questions:
                    q_lines = []
                    for i, q in enumerate(questions, 1):
                        q_type = "Multi" if q.get("multiple") else ("Email" if q.get("type") == "email" else "Single")
                        q_lines.append(f"{i}. {q['question']} *({q_type})*")
                    embed.add_field(name=f"Questions ({len(questions)})", value="\n".join(q_lines[:10]), inline=False)
                else:
                    embed.add_field(name="Questions", value="⚠️ None configured", inline=False)
            else:
                embed.add_field(name="Questions", value="❌ Onboarding module not loaded", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
    @onboarding_group.command(name="toggle", description="Enable or disable onboarding for this server")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable.")
    async def onboarding_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await self.settings.set("onboarding", "enabled", enabled, interaction.guild.id, interaction.user.id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Onboarding {state}.", ephemeral=True)
        await self._send_audit_log(
            "📝 Onboarding",
            f"Onboarding {state} by {interaction.user.mention}.",
            interaction.guild.id,
        )
    @onboarding_group.command(name="set_mode", description="Set onboarding mode")
    @app_commands.describe(mode="Choose the onboarding flow type")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Disabled - No onboarding", value="disabled"),
        app_commands.Choice(name="Rules Only - Role when rules accepted (no questions)", value="rules_only"),
        app_commands.Choice(name="Rules + Questions - Role only after all questions completed", value="rules_with_questions"),
        app_commands.Choice(name="Questions Only - Role only after all questions completed", value="questions_only"),
    ])
    @requires_admin()
    async def onboarding_set_mode(self, interaction: discord.Interaction, mode: str) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        valid_modes = ["disabled", "rules_only", "rules_with_questions", "questions_only"]
        if mode not in valid_modes:
            await interaction.followup.send(f"❌ Invalid mode. Choose from: {', '.join(valid_modes)}", ephemeral=True)
            return
        await self.settings.set("onboarding", "mode", mode, interaction.guild.id, interaction.user.id)
        await interaction.followup.send(f"✅ Onboarding mode set to: **{mode}**", ephemeral=True)
        await self._send_audit_log(
            "📝 Onboarding",
            f"Mode set to '{mode}' by {interaction.user.mention}.",
            interaction.guild.id
        )
    @onboarding_group.command(name="set_role", description="Set completion role")
    @requires_admin()
    @app_commands.describe(role="Role assigned after onboarding completion. Leave empty to remove.")
    async def onboarding_set_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("onboarding", "completion_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Completion role set to {role.mention}.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Completion role set to {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("onboarding", "completion_role_id", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ Completion role removed.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Completion role reset by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @onboarding_group.command(name="set_join_role", description="Set temporary join role")
    @requires_admin()
    @app_commands.describe(role="Role assigned immediately on join. Leave empty to remove.")
    async def onboarding_set_join_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        if role is not None:
            await self.settings.set("onboarding", "join_role_id", role.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Join role set to {role.mention}.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Join role set to {role.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("onboarding", "join_role_id", interaction.guild.id, interaction.user.id)
            await interaction.followup.send("↩️ Join role removed.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Join role reset by {interaction.user.mention}.",
                interaction.guild.id,
            )

    @onboarding_group.command(name="add_question", description="Add a new onboarding question")
    @requires_admin()
    async def onboarding_add_question(
        self,
        interaction: discord.Interaction,
        step: app_commands.Range[int, 1, 20],
        question: str,
        question_type: str = "select",
        required: bool = True
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        # Validate question type
        valid_types = ["select", "multiselect", "text", "email"]
        if question_type not in valid_types:
            await interaction.followup.send(
                f"❌ Invalid question type. Use: {', '.join(valid_types)}",
                ephemeral=True
            )
            return
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        question_data = {
            "question": question,
            "type": question_type if question_type in ["text", "email"] else None,
            "optional": not required
        }
        if question_type in ["select", "multiselect"]:
            question_data["multiple"] = (question_type == "multiselect")
        success = await onboarding_cog.save_guild_question(interaction.guild.id, step, question_data)
        if success:
            await interaction.followup.send(f"✅ Question added at position {step}.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Question '{question}' added at position {step} by {interaction.user.mention}.",
                interaction.guild.id
        )
        else:
            await interaction.followup.send("❌ Could not save question.", ephemeral=True)
    @onboarding_group.command(name="delete_question", description="Delete an onboarding question")
    @requires_admin()
    async def onboarding_delete_question(
        self,
        interaction: discord.Interaction,
        step: app_commands.Range[int, 1, 20]
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        success = await onboarding_cog.delete_guild_question(interaction.guild.id, step)
        if success:
            await interaction.followup.send(f"✅ Question at position {step} deleted.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Question at position {step} deleted by {interaction.user.mention}.",
                interaction.guild.id
        )
        else:
            await interaction.followup.send("❌ Could not delete question.", ephemeral=True)
    @onboarding_group.command(name="add_rule", description="Add a new onboarding rule")
    @app_commands.describe(
        rule_order="Position (1-20)",
        title="Rule title",
        description="Rule description",
        thumbnail_url="Optional: image URL shown right/top (rechts)",
        image_url="Optional: image URL shown at bottom (onderaan)",
    )
    @requires_admin()
    async def onboarding_add_rule(
        self,
        interaction: discord.Interaction,
        rule_order: app_commands.Range[int, 1, 20],
        title: str,
        description: str,
        thumbnail_url: str | None = None,
        image_url: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        success = await onboarding_cog.save_guild_rule(
            interaction.guild.id, rule_order, title, description,
            thumbnail_url=thumbnail_url, image_url=image_url,
        )
        if success:
            await interaction.followup.send(f"✅ Rule added at position {rule_order}.", ephemeral=True)
            if not thumbnail_url and not image_url:
                from utils.fyi_tips import send_fyi_if_first

                await send_fyi_if_first(self.bot, interaction.guild.id, "first_add_rule_no_image")
            await self._send_audit_log(
                "📝 Onboarding",
                f"Rule '{title}' added at position {rule_order} by {interaction.user.mention}.",
                interaction.guild.id
        )
        else:
            await interaction.followup.send("❌ Could not save rule.", ephemeral=True)
    @onboarding_group.command(name="delete_rule", description="Delete an onboarding rule")
    @requires_admin()
    async def onboarding_delete_rule(
        self,
        interaction: discord.Interaction,
        rule_order: app_commands.Range[int, 1, 20]
    ):
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        success = await onboarding_cog.delete_guild_rule(interaction.guild.id, rule_order)
        if success:
            await interaction.followup.send(f"✅ Rule at position {rule_order} deleted.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Rule at position {rule_order} deleted by {interaction.user.mention}.",
                interaction.guild.id
        )
        else:
            await interaction.followup.send("❌ Could not delete rule.", ephemeral=True)
    @onboarding_group.command(name="reset_rules", description="Reset to default onboarding rules")
    @requires_admin()
    async def onboarding_reset_rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        # Delete all custom rules for this guild
        if onboarding_cog.db:
            async with acquire_safe(onboarding_cog.db) as conn:
                await conn.execute("DELETE FROM guild_rules WHERE guild_id = $1", interaction.guild.id)
            # Clear cache
            if interaction.guild.id in onboarding_cog.guild_rules_cache:
                del onboarding_cog.guild_rules_cache[interaction.guild.id]
            await interaction.followup.send("✅ Onboarding rules reset to default.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Rules reset to default by {interaction.user.mention}.",
                interaction.guild.id
        )
        else:
            await interaction.followup.send("❌ Database not available.", ephemeral=True)
    @onboarding_group.command(name="reset_questions", description="Reset onboarding questions.")
    @requires_admin()
    async def onboarding_reset_questions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.followup.send("❌ Onboarding module not found.", ephemeral=True)
            return
        # Delete all custom questions for this guild
        if onboarding_cog.db:
            async with acquire_safe(onboarding_cog.db) as conn:
                await conn.execute("DELETE FROM guild_onboarding_questions WHERE guild_id = $1", interaction.guild.id)
            # Clear cache
            if interaction.guild.id in onboarding_cog.guild_questions_cache:
                del onboarding_cog.guild_questions_cache[interaction.guild.id]
            await interaction.followup.send("✅ Onboarding questions reset to default.", ephemeral=True)
            await self._send_audit_log(
                "📝 Onboarding",
                f"Questions reset to default by {interaction.user.mention}.",
                interaction.guild.id
            )
        else:
            await interaction.followup.send("❌ Database not available.", ephemeral=True)

    @onboarding_group.command(name="panel_post", description="Post onboarding panel.")
    @requires_admin()
    async def onboarding_panel_post(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        target = channel or cast(discord.TextChannel, interaction.channel)
        if target is None:
            await interaction.response.send_message("❌ No channel specified.", ephemeral=True)
            return

        # Check if onboarding is enabled for this guild
        enabled = self.settings.get("onboarding", "enabled", interaction.guild.id)
        if not enabled:
            await interaction.response.send_message("⚠️ Onboarding is not enabled for this server. Enable it with `/onboarding toggle true`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        embed = EmbedBuilder.success(
            title=f"Welcome to {interaction.guild.name}!",
            description="Tap below. Answer a few questions. You're in.",
        )
        embed.set_footer(text="Finish onboarding to unlock the server.")

        view = StartOnboardingView()
        await target.send(embed=embed, view=view)
        await interaction.followup.send("✅ Onboarding panel posted.", ephemeral=True)

    @onboarding_group.command(name="reorder", description="Reorder onboarding questions.")
    @requires_admin()
    async def onboarding_reorder(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        onboarding_cog = getattr(self.bot, "get_cog", lambda name: None)("Onboarding")
        if not onboarding_cog:
            await interaction.response.send_message("❌ Onboarding module not found.", ephemeral=True)
            return
        
        # Fetch all questions for this guild
        questions = await onboarding_cog.get_guild_questions(interaction.guild.id)
        if not questions:
            await interaction.response.send_message("❌ No questions configured for this guild.", ephemeral=True)
            return
        
        # Show modal with current order
        modal = ReorderQuestionsModal(onboarding_cog, interaction.guild.id, questions)
        await interaction.response.send_modal(modal)

    @automod_group.command(name="status", description="Check auto-moderation status")
    @requires_admin()
    async def automod_status(self, interaction: discord.Interaction) -> None:
        """Show current auto-moderation status."""
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        await interaction.response.defer(ephemeral=True)
        try:
            from utils.embed_builder import EmbedBuilder
            from utils.premium_guard import guild_has_premium
            rules = await self.rule_processor.get_active_rules(guild_id)
            premium_status = await guild_has_premium(guild_id)
            automod_enabled = self.settings.get("automod", "enabled", guild_id=guild_id)
            embed = EmbedBuilder.info(
                title="Auto-Moderation Status",
                fields=[
                    {"name": "Status",          "value": "✅ Module enabled" if automod_enabled else "❌ Module disabled", "inline": True},
                    {"name": "Active Rules",     "value": str(len(rules)), "inline": True},
                    {"name": "Premium Features", "value": "✅ Premium enabled" if premium_status else "❌ Premium disabled", "inline": True},
                ],
            )
            rule_counts: dict = {}
            for rule in rules:
                rt = rule.get("rule_type", "unknown")
                rule_counts[rt] = rule_counts.get(rt, 0) + 1
            if rule_counts:
                embed.add_field(
                    name="Rules by Type",
                    value="\n".join(f"• {rt}: {cnt}" for rt, cnt in rule_counts.items()),
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in automod status command: {e}")
            try:
                await interaction.followup.send("❌ Failed to retrieve auto-mod status.", ephemeral=True)
            except Exception:
                pass

    @automod_group.command(name="show", description="Show auto-moderation settings")
    @requires_admin()
    async def automod_show(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "automod", "🛡️ Auto-moderation settings")

    @automod_group.command(name="toggle", description="Enable or disable auto-moderation")
    @requires_admin()
    @app_commands.describe(enabled="True to enable, False to disable.")
    async def automod_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.settings.set("automod", "enabled", enabled, guild_id=interaction.guild.id, updated_by=interaction.user.id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Auto-moderation {state} for this server.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation {state}", user=str(interaction.user))

    @automod_group.command(name="set_log_channel", description="Set auto-moderation log channel")
    @requires_admin()
    @app_commands.describe(channel="Channel for automod logs. Leave empty to reset.")
    async def automod_set_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if channel is not None:
            await self.settings.set("automod", "log_channel_id", channel.id, guild_id=interaction.guild.id, updated_by=interaction.user.id)
            await interaction.followup.send(f"✅ Auto-moderation log channel set to {channel.mention}.", ephemeral=True)
            log_guild_action(interaction.guild.id, f"Auto-moderation log channel set to {channel.mention}", user=str(interaction.user))
        else:
            await self.settings.clear("automod", "log_channel_id", guild_id=interaction.guild.id, updated_by=interaction.user.id)
            await interaction.followup.send("↩️ Auto-moderation log channel reset to default.", ephemeral=True)
            log_guild_action(interaction.guild.id, "Auto-moderation log channel reset", user=str(interaction.user))

    @automod_group.command(name="add_spam_rule", description="Add a spam frequency rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_spam_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        max_messages: app_commands.Range[int, 2, 30] = 5,
        time_window_seconds: app_commands.Range[int, 5, 300] = 60,
        action_type: str = "warn",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)
        rule_config = {
            "spam_type": "frequency",
            "max_messages": int(max_messages),
            "time_window": int(time_window_seconds),
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.SPAM.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(
            f"✅ Spam rule created with ID `{rule_id}`.",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation spam rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_badwords_rule", description="Add a bad-words content rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_badwords_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        words: str,
        action_type: str = "delete",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        parsed_words = [w.strip().lower() for w in words.split(",") if w.strip()]
        if not parsed_words:
            await interaction.response.send_message(
                "❌ Please provide at least one word (comma-separated).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        rule_config = {
            "content_type": "bad_words",
            "words": parsed_words,
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.CONTENT.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(
            f"✅ Bad-words rule created with ID `{rule_id}` ({len(parsed_words)} words).",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation bad-words rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_links_rule", description="Add a link filtering rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_links_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        allow_links: bool = False,
        whitelist: str | None = None,
        blacklist: str | None = None,
        action_type: str = "delete",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)
        whitelist_domains = [d.strip().lower() for d in (whitelist or "").split(",") if d.strip()]
        blacklist_domains = [d.strip().lower() for d in (blacklist or "").split(",") if d.strip()]
        rule_config = {
            "content_type": "links",
            "allow_links": allow_links,
            "whitelist": whitelist_domains,
            "blacklist": blacklist_domains,
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.CONTENT.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(f"✅ Links rule created with ID `{rule_id}`.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation links rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_mentions_rule", description="Add a mention spam rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_mentions_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        max_mentions: app_commands.Range[int, 1, 30] = 5,
        action_type: str = "warn",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)
        rule_config = {
            "content_type": "mentions",
            "max_mentions": int(max_mentions),
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.CONTENT.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(f"✅ Mentions rule created with ID `{rule_id}`.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation mentions rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_caps_rule", description="Add an excessive caps spam rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_caps_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        min_length: app_commands.Range[int, 5, 500] = 10,
        max_caps_ratio: app_commands.Range[float, 0.5, 1.0] = 0.7,
        action_type: str = "warn",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)
        rule_config = {
            "spam_type": "caps",
            "min_length": int(min_length),
            "max_caps_ratio": float(max_caps_ratio),
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.SPAM.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(f"✅ Caps rule created with ID `{rule_id}`.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation caps rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_duplicate_rule", description="Add a duplicate-message spam rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_duplicate_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        max_duplicates: app_commands.Range[int, 2, 10] = 3,
        action_type: str = "warn",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)
        rule_config = {
            "spam_type": "duplicate",
            "max_duplicates": int(max_duplicates),
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.SPAM.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=False,
        )
        await interaction.followup.send(f"✅ Duplicate rule created with ID `{rule_id}`.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation duplicate rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_regex_rule", description="Add a regex content rule (premium)")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_regex_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        patterns: str,
        action_type: str = "delete",
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        has_premium = await guild_has_premium(interaction.guild.id)
        if not has_premium:
            await interaction.response.send_message(
                "❌ Regex rules require an active premium subscription for this guild.",
                ephemeral=True,
            )
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        parsed_patterns = [p.strip() for p in patterns.split(",") if p.strip()]
        if not parsed_patterns:
            await interaction.response.send_message(
                "❌ Please provide at least one regex pattern (comma-separated).",
                ephemeral=True,
            )
            return

        for pattern in parsed_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                await interaction.response.send_message(
                    f"❌ Invalid regex pattern `{pattern}`: {exc}",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        rule_config = {"patterns": parsed_patterns}
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.REGEX.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=True,
        )
        await interaction.followup.send(
            f"✅ Regex rule created with ID `{rule_id}` ({len(parsed_patterns)} patterns).",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation regex rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="add_ai_rule", description="Add AI content rule (premium).")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_add_ai_rule(
        self,
        interaction: discord.Interaction,
        name: str,
        policy: str,
        action_type: str = "delete",
        threshold: app_commands.Range[float, 0.5, 1.0] = 0.7,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        has_premium = await guild_has_premium(interaction.guild.id)
        if not has_premium:
            await interaction.response.send_message(
                "❌ AI moderation rules require an active premium subscription for this guild.",
                ephemeral=True,
            )
            return

        normalized_action = normalize_automod_action_type(action_type)
        if not normalized_action:
            await interaction.response.send_message(
                "❌ Invalid action type. Use: delete, warn, mute, timeout, or ban.",
                ephemeral=True,
            )
            return
        if not await check_advanced_action_premium(interaction, normalized_action):
            return

        await interaction.response.defer(ephemeral=True)

        rule_config = {
            "policy": policy,
            "threshold": threshold,
        }
        action_config: dict[str, Any] = {}
        if normalized_action == ActionType.MUTE.value:
            action_config = {"duration_minutes": 10}
        if normalized_action == ActionType.TIMEOUT.value:
            action_config = {"duration_minutes": 30}

        rule_id = await self.rule_processor.create_rule(
            guild_id=interaction.guild.id,
            rule_type=RuleType.AI.value,
            name=name,
            config=rule_config,
            action_type=normalized_action,
            action_config=action_config,
            created_by=interaction.user.id,
            is_premium=True,
        )
        await interaction.followup.send(
            f"✅ AI moderation rule created with ID `{rule_id}`.\n"
            f"Policy: {policy}\nThreshold: {threshold}",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation AI rule created: {name}", user=str(interaction.user))

    @automod_group.command(name="rules", description="List all auto-moderation rules")
    @requires_admin()
    async def automod_rules(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rows = await self.rule_processor.list_rules(interaction.guild.id)
        if not rows:
            await interaction.followup.send("ℹ️ No auto-moderation rules configured yet.", ephemeral=True)
            return

        lines = ["🛡️ **Auto-moderation rules**"]
        for row in rows[:25]:
            status = "✅" if row.get("enabled") else "🚫"
            lines.append(
                f"{status} `#{row.get('id')}` {row.get('name')} — "
                f"type: `{row.get('rule_type')}` | action: `{row.get('action_type')}`"
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @automod_group.command(name="delete_rule", description="Delete an auto-mod rule.")
    @requires_admin()
    async def automod_delete_rule(self, interaction: discord.Interaction, rule_id: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        deleted = await self.rule_processor.delete_rule(interaction.guild.id, rule_id)
        if not deleted:
            await interaction.followup.send(f"❌ Rule `{rule_id}` not found.", ephemeral=True)
            return

        await interaction.followup.send(f"✅ Rule `{rule_id}` deleted.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation rule deleted: {rule_id}", user=str(interaction.user))

    @automod_group.command(name="set_rule_enabled", description="Enable or disable an auto-moderation rule by ID")
    @requires_admin()
    @app_commands.describe(rule_id="ID of the rule to update.", enabled="True to enable, False to disable.")
    async def automod_set_rule_enabled(self, interaction: discord.Interaction, rule_id: int, enabled: bool) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        updated = await self.rule_processor.update_rule(
            guild_id=interaction.guild.id,
            rule_id=rule_id,
            enabled=enabled,
        )
        if not updated:
            await interaction.followup.send(f"❌ Rule `{rule_id}` not found.", ephemeral=True)
            return

        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Rule `{rule_id}` {state}.", ephemeral=True)
        log_guild_action(interaction.guild.id, f"Auto-moderation rule {state}: {rule_id}", user=str(interaction.user))

    @automod_group.command(name="edit_rule", description="Edit an auto-moderation rule")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_edit_rule(
        self,
        interaction: discord.Interaction,
        rule_id: int,
        name: str | None = None,
        enabled: bool | None = None,
        action_type: str | None = None,
        max_messages: app_commands.Range[int, 2, 30] | None = None,
        time_window_seconds: app_commands.Range[int, 5, 300] | None = None,
        max_mentions: app_commands.Range[int, 1, 30] | None = None,
        max_duplicates: app_commands.Range[int, 2, 10] | None = None,
        min_length: app_commands.Range[int, 5, 500] | None = None,
        max_caps_ratio: app_commands.Range[float, 0.5, 1.0] | None = None,
        words: str | None = None,
        patterns: str | None = None,
        allow_links: bool | None = None,
        whitelist: str | None = None,
        blacklist: str | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        if (
            name is None
            and enabled is None
            and action_type is None
            and max_messages is None
            and time_window_seconds is None
            and max_mentions is None
            and max_duplicates is None
            and min_length is None
            and max_caps_ratio is None
            and words is None
            and patterns is None
            and allow_links is None
            and whitelist is None
            and blacklist is None
        ):
            await interaction.response.send_message(
                "❌ Provide at least one field to update.",
                ephemeral=True,
            )
            return

        normalized_action: str | None = None
        if action_type is not None:
            normalized_action = normalize_automod_action_type(action_type)
            if not normalized_action:
                await interaction.response.send_message(
                    "❌ Invalid action_type. Use: delete, warn, mute, timeout, or ban.",
                    ephemeral=True,
                )
                return
            if not await check_advanced_action_premium(interaction, normalized_action):
                return

        await interaction.response.defer(ephemeral=True)
        current_rule = await self.rule_processor.get_rule(interaction.guild.id, rule_id)
        if not current_rule:
            await interaction.followup.send(f"❌ Rule `{rule_id}` not found.", ephemeral=True)
            return

        requested_fields = {
            "max_messages": max_messages is not None,
            "time_window_seconds": time_window_seconds is not None,
            "max_mentions": max_mentions is not None,
            "max_duplicates": max_duplicates is not None,
            "min_length": min_length is not None,
            "max_caps_ratio": max_caps_ratio is not None,
            "words": words is not None,
            "patterns": patterns is not None,
            "allow_links": allow_links is not None,
            "whitelist": whitelist is not None,
            "blacklist": blacklist is not None,
        }
        invalid_msg = validate_rule_update_fields(
            str(current_rule.get("rule_type", "")),
            dict(current_rule.get("config") or {}),
            requested_fields,
        )
        if invalid_msg:
            await interaction.followup.send(invalid_msg, ephemeral=True)
            return

        new_config = dict(current_rule.get("config") or {})
        config_changed = False
        if max_messages is not None:
            new_config["max_messages"] = int(max_messages)
            config_changed = True
        if time_window_seconds is not None:
            new_config["time_window"] = int(time_window_seconds)
            config_changed = True
        if max_mentions is not None:
            new_config["max_mentions"] = int(max_mentions)
            config_changed = True
        if max_duplicates is not None:
            new_config["max_duplicates"] = int(max_duplicates)
            config_changed = True
        if min_length is not None:
            new_config["min_length"] = int(min_length)
            config_changed = True
        if max_caps_ratio is not None:
            new_config["max_caps_ratio"] = float(max_caps_ratio)
            config_changed = True
        if words is not None:
            parsed_words = [w.strip().lower() for w in words.split(",") if w.strip()]
            if not parsed_words:
                await interaction.followup.send("❌ `words` must include at least one comma-separated value.", ephemeral=True)
                return
            new_config["words"] = parsed_words
            config_changed = True
        if patterns is not None:
            parsed_patterns = [p.strip() for p in patterns.split(",") if p.strip()]
            if not parsed_patterns:
                await interaction.followup.send("❌ `patterns` must include at least one comma-separated regex.", ephemeral=True)
                return
            for pattern in parsed_patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    await interaction.followup.send(
                        f"❌ Invalid regex pattern `{pattern}`: {exc}",
                        ephemeral=True,
                    )
                    return
            new_config["patterns"] = parsed_patterns
            config_changed = True
        if allow_links is not None:
            new_config["allow_links"] = allow_links
            config_changed = True
        if whitelist is not None:
            new_config["whitelist"] = [d.strip().lower() for d in whitelist.split(",") if d.strip()]
            config_changed = True
        if blacklist is not None:
            new_config["blacklist"] = [d.strip().lower() for d in blacklist.split(",") if d.strip()]
            config_changed = True

        updated = await self.rule_processor.update_rule(
            guild_id=interaction.guild.id,
            rule_id=rule_id,
            name=name,
            enabled=enabled,
            action_type=normalized_action,
            config=new_config if config_changed else None,
        )
        if not updated:
            await interaction.followup.send(f"❌ Rule `{rule_id}` not found.", ephemeral=True)
            return

        updates: list[str] = []
        if name is not None:
            updates.append(f"name=`{name}`")
        if enabled is not None:
            updates.append(f"enabled=`{enabled}`")
        if normalized_action is not None:
            updates.append(f"action_type=`{normalized_action}`")
        if config_changed:
            updates.append("config=`updated`")
        updates_text = ", ".join(updates) if updates else "updated"

        await interaction.followup.send(
            f"✅ Rule `{rule_id}` updated: {updates_text}.",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation rule updated: {rule_id}", user=str(interaction.user))

    @automod_group.command(name="logs", description="Show auto-moderation logs.")
    @app_commands.choices(action_type=AUTOMOD_ACTION_CHOICES)
    @requires_admin()
    async def automod_logs(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
        user_id: str | None = None,
        rule_id: int | None = None,
        action_type: str | None = None,
        days: app_commands.Range[int, 1, 90] = 7,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        from utils.db_helpers import get_bot_db_pool
        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send("❌ Database temporarily unavailable. Please try again later.", ephemeral=True)
            return

        query_parts = ["SELECT user_id, rule_id, action_taken, timestamp FROM automod_logs WHERE guild_id = $1"]
        params: list[Any] = [interaction.guild.id]
        param_idx = 2

        if user_id:
            try:
                uid = int(user_id)
                query_parts.append(f"AND user_id = ${param_idx}")
                params.append(uid)
                param_idx += 1
            except ValueError:
                await interaction.followup.send("❌ Invalid user_id format.", ephemeral=True)
                return

        if rule_id is not None:
            query_parts.append(f"AND rule_id = ${param_idx}")
            params.append(rule_id)
            param_idx += 1

        if action_type:
            query_parts.append(f"AND action_taken = ${param_idx}")
            params.append(action_type.lower())
            param_idx += 1

        query_parts.append(f"AND timestamp > NOW() - (${param_idx}::text || ' days')::interval")
        params.append(days)
        param_idx += 1

        query_parts.append("ORDER BY timestamp DESC")
        query_parts.append(f"LIMIT ${param_idx}")
        params.append(int(limit))

        query = " ".join(query_parts)

        try:
            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(query, *params)
        except Exception as e:
            logger.error(f"Failed to load automod logs: {e}")
            await interaction.followup.send("❌ Failed to load logs. Please try again.", ephemeral=True)
            return

        if not rows:
            await interaction.followup.send("ℹ️ No auto-moderation logs found matching filters.", ephemeral=True)
            return

        lines = ["📜 **Recent auto-moderation logs**"]
        filter_desc = []
        if user_id:
            filter_desc.append(f"user={user_id}")
        if rule_id is not None:
            filter_desc.append(f"rule={rule_id}")
        if action_type:
            filter_desc.append(f"action={action_type}")
        filter_desc.append(f"last {days}d")
        lines.append(f"_Filters: {', '.join(filter_desc)}_\n")

        for row in rows:
            ts = row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row.get("timestamp") else "unknown-time"
            lines.append(
                f"`{ts}` user=`{row.get('user_id')}` rule=`{row.get('rule_id')}` action=`{row.get('action_taken')}`"
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @automod_group.command(name="set_severity", description="Set rule priority/severity.")
    @requires_admin()
    async def automod_set_severity(
        self,
        interaction: discord.Interaction,
        rule_id: int,
        severity: app_commands.Range[int, 1, 10],
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        from utils.db_helpers import get_bot_db_pool
        pool = get_bot_db_pool(self.bot)
        if not pool:
            await interaction.followup.send("❌ Database temporarily unavailable. Please try again later.", ephemeral=True)
            return

        try:
            async with acquire_safe(pool) as conn:
                rule_row = await conn.fetchrow(
                    "SELECT id, action_id FROM automod_rules WHERE guild_id = $1 AND id = $2",
                    interaction.guild.id,
                    rule_id,
                )
                if not rule_row:
                    await interaction.followup.send(f"❌ Rule `{rule_id}` not found.", ephemeral=True)
                    return

                await conn.execute(
                    "UPDATE automod_actions SET severity = $1 WHERE id = $2",
                    severity,
                    rule_row["action_id"],
                )
        except Exception as e:
            logger.error(f"Failed to update automod severity: {e}")
            await interaction.followup.send("❌ Failed to update severity. Please try again.", ephemeral=True)
            return

        if hasattr(self, "rule_processor") and self.rule_processor:
            if interaction.guild.id in self.rule_processor._rules_cache:
                del self.rule_processor._rules_cache[interaction.guild.id]

        await interaction.followup.send(
            f"✅ Rule `{rule_id}` severity set to `{severity}` (higher = higher priority).",
            ephemeral=True,
        )
        log_guild_action(interaction.guild.id, f"Auto-moderation rule severity updated: {rule_id} → {severity}", user=str(interaction.user))

    @automod_delete_rule.autocomplete("rule_id")
    @automod_set_rule_enabled.autocomplete("rule_id")
    @automod_edit_rule.autocomplete("rule_id")
    @automod_set_severity.autocomplete("rule_id")
    @automod_logs.autocomplete("rule_id")
    async def automod_rule_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Suggest auto-mod rules for this server (type / name / id filter)."""
        if not interaction.guild:
            return []
        try:
            rows = await self.rule_processor.list_rules(interaction.guild.id)
        except Exception:
            logger.debug("automod_rule_id_autocomplete: list_rules failed", exc_info=True)
            return []

        q = current.lower().strip()
        choices: list[app_commands.Choice[int]] = []
        for row in rows:
            rid = int(row["id"])
            name = str(row.get("name") or "unnamed")
            rtype = str(row.get("rule_type") or "")
            action_t = str(row.get("action_type") or "")
            enabled = bool(row.get("enabled"))
            label = f"#{rid} {name} · {rtype} · {action_t} · {'on' if enabled else 'off'}"
            if len(label) > 100:
                label = label[:97] + "..."
            if q and q not in label.lower() and q not in str(rid):
                continue
            choices.append(app_commands.Choice(name=label, value=rid))
            if len(choices) >= 25:
                break
        return choices

    @growth_group.command(name="set_channel", description="Set Growth Check-in channel")
    @requires_admin()
    @app_commands.describe(channel="Channel for shared Growth Check-ins. Leave empty to remove configuration.")
    async def growth_set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None  # Guaranteed by @requires_admin()
        await interaction.response.defer(ephemeral=True)
        if channel is not None:
            await self.settings.set("growth", "log_channel_id", channel.id, interaction.guild.id, interaction.user.id)
            await interaction.followup.send(f"✅ Growth Check-in channel set to {channel.mention}.", ephemeral=True)
            await self._send_audit_log(
                "⚙️ Setting updated",
                f"`growth.log_channel_id` set to {channel.mention} by {interaction.user.mention}.",
                interaction.guild.id,
            )
        else:
            await self.settings.clear("growth", "log_channel_id", interaction.guild.id, interaction.user.id)
            await interaction.followup.send(
                "↩️ Growth Check-in channel removed. Sharing option will no longer appear.",
                ephemeral=True,
            )
            await self._send_audit_log(
                "⚙️ Setting reset",
                f"`growth.log_channel_id` cleared by {interaction.user.mention}.",
                interaction.guild.id,
            )

    def _resolve_guild_id(
        self, interaction: discord.Interaction, guild_id_str: str | None
    ) -> tuple[int | None, str | None]:
        """Return (effective_guild_id, error_message). error_message is None on success."""
        if guild_id_str is not None:
            try:
                return int(guild_id_str.strip()), None
            except ValueError:
                return None, f"❌ Invalid guild ID `{guild_id_str}` — must be a numeric snowflake."
        if interaction.guild_id is not None:
            return interaction.guild_id, None
        return None, "❌ No guild context. Provide a `guild_id` when using this command in DMs."

    # ------------------------------------------------------------------ #
    #  Settings show — shared embed helpers                               #
    # ------------------------------------------------------------------ #

    _SETTINGS_PAGE_SIZE = 10

    def _settings_embed(self, title: str, items: list, page: int, scope: str = "") -> discord.Embed:
        """Build a paginated embed for a settings scope."""
        page_size = self._SETTINGS_PAGE_SIZE
        start = page * page_size
        slice_ = items[start : start + page_size]
        embed = discord.Embed(title=title, color=0x5865F2)
        for definition, value, _overridden in slice_:
            formatted = self._format_value(definition, value)
            display_name = self._humanize_key(definition.key)
            embed.add_field(
                name=display_name,
                value=formatted,
                inline=False,
            )
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        count = len(items)
        count_label = f"{count} setting{'s' if count != 1 else ''}"
        if total_pages > 1:
            embed.set_footer(text=f"Page {page + 1}/{total_pages} · {count_label}")
        else:
            embed.set_footer(text=count_label)
        return embed

    async def _reply_settings(self, interaction: discord.Interaction, scope: str, title: str) -> None:
        """Fetch a settings scope and send it as a paginated embed."""
        assert interaction.guild is not None
        items = self.settings.list_scope(scope, interaction.guild.id)
        if not items:
            await interaction.followup.send(f"⚠️ No `{scope}` settings registered.", ephemeral=True)
            return
        # Sort items to put "enabled" first if present, then alphabetically
        items.sort(key=lambda x: (x[0].key != "enabled", x[0].key))
        embed = self._settings_embed(title, items, page=0, scope=scope)
        if len(items) > self._SETTINGS_PAGE_SIZE:
            view = Configuration.SettingsPageView(self, title, items, scope=scope)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    class SettingsPageView(discord.ui.View):
        """Reusable prev/next paginator for settings scopes."""

        def __init__(self, cog: "Configuration", title: str, items: list, page: int = 0, scope: str = ""):
            super().__init__(timeout=180)
            self.cog = cog
            self.title = title
            self.items = items
            self.page = page
            self.scope = scope
            self._sync_buttons()

        def _sync_buttons(self) -> None:
            page_size = Configuration._SETTINGS_PAGE_SIZE
            total_pages = max(1, (len(self.items) + page_size - 1) // page_size)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id == "cfg_prev":
                        child.disabled = self.page <= 0
                    if child.custom_id == "cfg_next":
                        child.disabled = self.page >= total_pages - 1

        async def _update(self, interaction: discord.Interaction) -> None:
            self._sync_buttons()
            embed = self.cog._settings_embed(self.title, self.items, self.page, scope=self.scope)
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="⬅ Prev", style=discord.ButtonStyle.secondary, custom_id="cfg_prev")
        async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page > 0:
                self.page -= 1
            await self._update(interaction)

        @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.primary, custom_id="cfg_next")
        async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
            page_size = Configuration._SETTINGS_PAGE_SIZE
            total_pages = max(1, (len(self.items) + page_size - 1) // page_size)
            if self.page < total_pages - 1:
                self.page += 1
            await self._update(interaction)

    def _humanize_key(self, key: str) -> str:
        """Convert a setting key to a human-readable display name."""
        # Special cases
        if key == "enabled":
            return "Enabled"
        # General humanization
        words = key.replace('_', ' ').split()
        humanized = []
        for word in words:
            if word.lower() in ('id', 'ids'):
                continue  # Skip ID suffixes
            humanized.append(word.capitalize())
        return ' '.join(humanized)

    def _format_value(self, definition: SettingDefinition, value: Any) -> str:
        if value is None:
            return "Not set"
        if definition.value_type == "channel":
            return f"<#{int(value)}>" if value else "Not set"
        if definition.value_type == "role":
            return f"<@&{int(value)}>" if value else "Not set"
        if definition.value_type in ("bool", "boolean"):
            return "✅ Yes" if value else "❌ No"
        # For message fields, no backticks; for others, add backticks
        if "message" in definition.key.lower():
            return str(value)
        return f"`{value}`"
    async def _send_audit_log(self, title: str, message: str, guild_id: int | None = None) -> None:
        """Send audit log to the correct guild's log channel. Config changes are always logged (critical)."""
        if guild_id is None:
            # Backwards compatibility - skip logging if no guild_id provided
            return
        try:
            channel_id = int(self.settings.get("system", "log_channel_id", guild_id))
        except Exception:
            # No log channel configured for this guild
            log_with_guild("No audit log channel configured for configuration changes", guild_id, "debug")
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            log_with_guild(f"Audit log channel {channel_id} not found or not accessible", guild_id, "warning")
            return
        embed = EmbedBuilder.warning(title=title, description=message)
        embed.set_footer(text=f"config | Guild: {guild_id}")
        try:
            # Config changes are always logged (critical level), bypass log level check
            await channel.send(embed=embed)
            log_guild_action(guild_id, "AUDIT_LOG", details=f"config: {title}")
        except Exception as e:
            log_with_guild(f"Could not send audit log: {e}", guild_id, "error")


    # -------------------------------------------------------------------------
    # /config engagement — Engagement module
    # -------------------------------------------------------------------------

    @engagement_group.command(
        name="show",
        description="Show all Engagement settings for this server",
    )
    @requires_admin()
    async def engagement_show(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        s = self.settings

        async def g(key: str, default: Any = None) -> Any:
            try:
                val = await s.get(guild_id, "engagement", key)
                return val if val is not None else default
            except Exception:
                return default

        def fmt_bool(v: Any) -> str:
            return "✅ Yes" if v else "❌ No"

        def fmt_channel(v: Any) -> str:
            return f"<#{int(v)}>" if v else "Not set"

        def fmt_role(v: Any) -> str:
            return f"<@&{int(v)}>" if v else "Not set"

        # Gather values
        challenges = await g('challenges_enabled', False)
        weekly = await g('weekly_enabled', False)
        badges = await g('badges_enabled', False)
        streaks = await g('streaks_enabled', False)
        og = await g('og_enabled', False)

        winner_role = await g('challenge_winner_role_id')
        og_role = await g('badge_role_og')

        weekly_channel = await g('weekly_award_channel_id')
        food_channels = await g('weekly_food_channel_ids', 'Not set')

        streaks_nick = await g('streaks_nicknames', False)
        og_cap = await g('og_cap', 50)

        badge_roles = {
            'og': await g('badge_role_og'),
            'winner': await g('badge_role_winner'),
            'motivator': await g('badge_role_motivator'),
            'foodfluencer': await g('badge_role_foodfluencer'),
            'sharpshooter': await g('badge_role_sharpshooter'),
            'star': await g('badge_role_star'),
        }

        embed = EmbedBuilder.info(
            title="⚡ Engagement Settings",
            fields=[
                {"name": "Features", "value": f"{'✅' if challenges else '❌'} Challenges  {'✅' if weekly else '❌'} Weekly  {'✅' if badges else '❌'} Badges\n{'✅' if streaks else '❌'} Streaks  {'✅' if og else '❌'} OG", "inline": False},
                {"name": "Roles", "value": f"Winner role  {fmt_role(winner_role)}\nOG role   {fmt_role(og_role)}", "inline": False},
                {"name": "Channels", "value": f"Weekly award channel  {fmt_channel(weekly_channel)}\nWeekly food channels  {food_channels}", "inline": False},
                {"name": "Streaks", "value": f"Nicknames enabled  {fmt_bool(streaks_nick)}\nOG cap     {og_cap}", "inline": False},
                {"name": f"Badges ({sum(1 for r in badge_roles.values() if r)} total)", "value": "\n".join(f"{k}   {fmt_role(v)}" for k, v in badge_roles.items()), "inline": False},
            ]
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @engagement_group.command(
        name="toggle",
        description="Enable or disable an Engagement feature for this server",
    )
    @app_commands.describe(
        feature="Which feature to toggle",
        enabled="true to enable, false to disable",
    )
    @app_commands.choices(feature=[
        app_commands.Choice(name="challenges",  value="challenges_enabled"),
        app_commands.Choice(name="weekly",      value="weekly_enabled"),
        app_commands.Choice(name="badges",      value="badges_enabled"),
        app_commands.Choice(name="streaks",     value="streaks_enabled"),
        app_commands.Choice(name="og_claims",   value="og_enabled"),
    ])
    @requires_admin()
    async def engagement_toggle(
        self,
        interaction: discord.Interaction,
        feature: app_commands.Choice[str],
        enabled: bool,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", feature.value, enabled, guild_id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(
            f"**{feature.name}** {state} for this server.", ephemeral=True
        )
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.{feature.value}` → {enabled} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_challenge_winner_role",
        description="Set the role awarded to challenge winners",
    )
    @app_commands.describe(role="Role to assign to challenge winners (leave empty to clear)")
    @requires_admin()
    async def engagement_set_challenge_winner_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "challenge_winner_role_id", role.id if role else None, guild_id)
        msg = f"Challenge winner role set to {role.mention}." if role else "Challenge winner role cleared."
        await interaction.followup.send(msg, ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.challenge_winner_role_id` → {role.id if role else None} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_weekly_channel",
        description="Set the channel where weekly award announcements are posted",
    )
    @app_commands.describe(channel="Announcement channel (leave empty to clear)")
    @requires_admin()
    async def engagement_set_weekly_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "weekly_award_channel_id", channel.id if channel else None, guild_id)
        msg = f"Weekly award channel set to {channel.mention}." if channel else "Weekly award channel cleared."
        await interaction.followup.send(msg, ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.weekly_award_channel_id` → {channel.id if channel else None} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_food_channels",
        description="Set which channels count as food channels for weekly awards (comma-separated IDs)",
    )
    @app_commands.describe(channel_ids="Comma-separated channel IDs, e.g. 123456,789012 (leave empty to clear)")
    @requires_admin()
    async def engagement_set_food_channels(
        self,
        interaction: discord.Interaction,
        channel_ids: str | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "weekly_food_channel_ids", channel_ids or "", guild_id)
        msg = f"Food channels set to: `{channel_ids}`." if channel_ids else "Food channels cleared."
        await interaction.followup.send(msg, ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.weekly_food_channel_ids` → {channel_ids or ''} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_weekly_awards",
        description="Set the weekly award categories as JSON (advanced)",
    )
    @app_commands.describe(
        json_config=(
            'JSON list of award configs. Each item: {"key":"x","label":"📣 X","subtitle":"...","filter":"non_food|food|image|reactions"}'
        )
    )
    @requires_admin()
    async def engagement_set_weekly_awards(
        self,
        interaction: discord.Interaction,
        json_config: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        import json as _json
        try:
            parsed = _json.loads(json_config)
            if not isinstance(parsed, list):
                raise ValueError("Expected a JSON list")
            required_keys = {"key", "label", "filter"}
            for item in parsed:
                if not required_keys.issubset(item.keys()):
                    raise ValueError(f"Each item needs: {required_keys}")
        except Exception as exc:
            await interaction.followup.send(f"Invalid JSON: {exc}", ephemeral=True)
            return
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "weekly_award_configs", json_config, guild_id)
        await interaction.followup.send(f"Weekly award configs updated ({len(parsed)} awards).", ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.weekly_award_configs` updated by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_badge_role",
        description="Link a Discord role to a badge key",
    )
    @app_commands.describe(
        badge_key="Badge key e.g. winner, og, motivator, foodfluencer, sharpshooter, star",
        role="Role to assign when this badge is awarded (leave empty to clear)",
    )
    @requires_admin()
    async def engagement_set_badge_role(
        self,
        interaction: discord.Interaction,
        badge_key: str,
        role: discord.Role | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        key = badge_key.lower().strip()
        setting_key = f"badge_role_{key}"
        await self.settings.set("engagement", setting_key, role.id if role else None, guild_id)
        msg = f"Badge **{key}** linked to {role.mention}." if role else f"Badge **{key}** role cleared."
        await interaction.followup.send(msg, ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.{setting_key}` → {role.id if role else None} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_og_cap",
        description="Set the maximum number of OG claims",
    )
    @app_commands.describe(cap="Maximum number of OG claims (default: 50)")
    @requires_admin()
    async def engagement_set_og_cap(
        self,
        interaction: discord.Interaction,
        cap: int,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if cap < 1:
            await interaction.response.send_message("Cap must be at least 1.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "og_cap", cap, guild_id)
        await interaction.followup.send(f"OG claim cap set to **{cap}**.", ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.og_cap` → {cap} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_og_text",
        description="Set the message text posted when running /og setup",
    )
    @app_commands.describe(text="The OG claim message text (leave empty to reset to default)")
    @requires_admin()
    async def engagement_set_og_text(
        self,
        interaction: discord.Interaction,
        text: str | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "og_claim_text", text or "", guild_id)
        msg = "OG claim text updated." if text else "OG claim text reset to default."
        await interaction.followup.send(msg, ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.og_claim_text` → {text or ''} by {interaction.user.mention}.",
            guild_id,
        )

    @engagement_group.command(
        name="set_streaks_nicknames",
        description="Toggle automatic nickname suffixes for streaks (e.g. 'Name | 🔥 week 2')",
    )
    @app_commands.describe(enabled="true to enable nickname suffixes, false to disable")
    @requires_admin()
    async def engagement_set_streaks_nicknames(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        await self.settings.set("engagement", "streaks_nicknames", enabled, guild_id)
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"Streak nickname suffixes {state}.", ephemeral=True)
        await self._send_audit_log(
            "🎉 Engagement",
            f"`engagement.streaks_nicknames` → {enabled} by {interaction.user.mention}.",
            guild_id,
        )

    # -------------------------------------------------------------------------
    # /config agents — Alphapy personal agents (/agent)
    # -------------------------------------------------------------------------

    @agents_group.command(name="show", description="Show Alphapy agent settings for this server")
    @requires_admin()
    async def agents_show(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._reply_settings(interaction, "agents", "🤖 Alphapy agents")

    @agents_group.command(
        name="toggle",
        description="Enable or disable /agent commands for members in this server",
    )
    @requires_admin()
    @app_commands.describe(enabled="True to enable personal agent commands, false to disable.")
    async def agents_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        await self.settings.set(
            "agents",
            "enabled",
            enabled,
            interaction.guild.id,
            interaction.user.id,
        )
        state = "enabled" if enabled else "disabled"
        await interaction.followup.send(
            f"✅ Alphapy agent commands {state} for this server.",
            ephemeral=True,
        )
        await self._send_audit_log(
            "🤖 Alphapy agents",
            f"`agents.enabled` → {enabled} by {interaction.user.mention}.",
            interaction.guild.id,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Configuration(bot))
