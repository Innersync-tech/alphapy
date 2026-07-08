from typing import cast

import discord
from discord.ext import commands

from utils.embed_builder import EmbedBuilder
from utils.logger import log_with_guild, logger
from utils.operational_logs import EventType, log_operational_event


class StartOnboardingView(discord.ui.View):
    """View with a button to start onboarding directly."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(StartOnboardingButton())

class StartOnboardingButton(discord.ui.Button):
    """Button to accept rules and start onboarding directly."""
    def __init__(self):
        # Use a fixed custom_id so this is recognized as a persistent view.
        super().__init__(label="🚀 Start Onboarding", style=discord.ButtonStyle.success, custom_id="start_onboarding")
        
    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return
        member = interaction.user
        bot = cast(commands.Bot, interaction.client)
        settings = getattr(bot, "settings", None)
        if not settings:
            await interaction.response.send_message("❌ Bot configuration unavailable.", ephemeral=True)
            return

        # Check onboarding settings
        enabled = settings.get("onboarding", "enabled", interaction.guild.id)
        mode = settings.get("onboarding", "mode", interaction.guild.id)

        if not enabled or mode == "disabled":
            await interaction.response.send_message("🚫 Onboarding is currently disabled.", ephemeral=True)
            return

        # Handle different modes
        if mode == "questions_only":
            # Skip rules, go directly to questions
            onboarding_cog = bot.get_cog("Onboarding")
            if onboarding_cog is not None:
                await interaction.response.defer(ephemeral=True)
                await interaction.followup.send("📝 Starting onboarding questions...", ephemeral=True)
                await onboarding_cog.send_next_question(interaction)  # type: ignore[union-attr]
            else:
                await interaction.response.send_message("❌ Onboarding system unavailable.", ephemeral=True)

        elif mode in ["rules_only", "rules_with_questions"]:
            # Start with the rules
            rules_view = RuleAcceptanceView(member, interaction.guild.id)
            await rules_view.load_rules(bot)
            if not rules_view.rules:
                await interaction.response.send_message(
                    "⚠️ No onboarding rules configured. Ask an admin to run `/onboarding add_rule`.",
                    ephemeral=True,
                )
                # Log to configured log channel (via /config system set_log_channel)
                log_channel_id = settings.get("system", "log_channel_id", interaction.guild.id)
                log_channel = bot.get_channel(log_channel_id) if log_channel_id else None
                if log_channel and isinstance(log_channel, discord.abc.Messageable):
                    log_embed = EmbedBuilder.warning(
                        title="⚠️ Onboarding: No rules configured",
                        description=f"No onboarding rules are set for this server. User {member.mention} attempted to start onboarding."
                    )
                    log_embed.add_field(name="Action", value="Use `/onboarding add_rule` to add rules.", inline=False)
                    await log_channel.send(embed=log_embed)

                log_operational_event(
                    EventType.ONBOARDING_ERROR,
                    f"User {interaction.user.id} attempted onboarding with no rules configured",
                    guild_id=interaction.guild.id,
                    details={"user_id": interaction.user.id, "error_type": "no_rules"}
                )
                return
            embed = rules_view._build_rule_embed(0)
            kwargs = {"view": rules_view, "ephemeral": True}
            if embed is not None:
                kwargs["embed"] = embed
            await interaction.response.send_message(**kwargs)

        else:
            await interaction.response.send_message("🚫 Unknown onboarding mode.", ephemeral=True)

class RuleAcceptanceView(discord.ui.View):
    """View where users must accept rules one by one before proceeding to onboarding."""
    def __init__(self, member, guild_id: int):
        super().__init__(timeout=None)
        self.member = member
        self.guild_id = guild_id
        self.current_rule = 0
        self.accepted_rules = set()
        self.rules = []  # Will be loaded asynchronously
        # Don't call update_buttons() here - it will be called after rules are loaded

    async def load_rules(self, bot):
        """Load rules for this guild."""
        self.bot = bot
        onboarding_cog = bot.get_cog("Onboarding")
        self.rules = await onboarding_cog.get_guild_rules(self.guild_id) if onboarding_cog else []
        self.update_buttons()

    def get_final_button_label(self) -> str:
        """Return the final accept button label based on onboarding mode (rules only vs rules + questions)."""
        default = "✅ Accept All Rules & Start Onboarding"
        bot = getattr(self, "bot", None)
        if not bot:
            return default
        settings = getattr(bot, "settings", None)
        if not settings:
            return default
        mode = settings.get("onboarding", "mode", self.guild_id)
        if mode == "rules_only":
            return "✅ Accept All Rules & Get Role"
        if mode in ["rules_with_questions", "questions_only"]:
            return "✅ Accept All Rules & Start Onboarding"
        return default

    def _build_rule_embed(self, rule_index: int):
        """Build an embed for a rule at the given index, with optional thumbnail (right) and image (bottom)."""
        from utils.sanitizer import safe_embed_text
        if rule_index < 0 or rule_index >= len(self.rules):
            return None
        rule = self.rules[rule_index]
        title = rule["title"] if isinstance(rule, dict) else rule[0]
        description = rule["description"] if isinstance(rule, dict) else rule[1]
        embed = EmbedBuilder.success(title=safe_embed_text(title), description=safe_embed_text(description))
        embed.set_footer(text="Continue to the next rule.")
        if isinstance(rule, dict):
            thumb = rule.get("thumbnail_url")
            img = rule.get("image_url")
            if thumb:
                embed.set_thumbnail(url=thumb)
            if img:
                embed.set_image(url=img)
        return embed

    def update_buttons(self):
        """Update the buttons so only the current rule is shown."""
        self.clear_items()
        if self.current_rule < len(self.rules):
            rule = self.rules[self.current_rule]
            rule_title = rule["title"] if isinstance(rule, dict) else rule[0]
            self.add_item(RuleButton(rule_index=self.current_rule, rule_text=rule_title, view=self))
        elif len(self.accepted_rules) == len(self.rules):
            self.add_item(FinalAcceptButton(label=self.get_final_button_label()))

class RuleButton(discord.ui.Button):
    """Button to accept a rule."""
    def __init__(self, rule_index: int, rule_text: str, view: RuleAcceptanceView):
        super().__init__(label="✅ I Accept", style=discord.ButtonStyle.primary, custom_id=f"rule_{rule_index}")
        self.rule_index = rule_index
        self.rule_text = rule_text
        self.rule_view = view

    async def callback(self, interaction: discord.Interaction):
        view_obj = self.view
        if not isinstance(view_obj, RuleAcceptanceView):
            await interaction.response.send_message("⚠️ Invalid view state.", ephemeral=True)
            return
        if interaction.user.id != view_obj.member.id:
            await interaction.response.send_message("🚫 You cannot interact with this!", ephemeral=True)
            return

        view_obj.accepted_rules.add(self.rule_index)
        view_obj.current_rule += 1
        view_obj.update_buttons()
        if view_obj.current_rule < len(view_obj.rules):
            embed = view_obj._build_rule_embed(view_obj.current_rule)
            edit_kwargs: dict = {"view": view_obj}
            if embed is not None:
                edit_kwargs["embed"] = embed
            await interaction.response.edit_message(**edit_kwargs)
        else:
            view_obj.clear_items()
            view_obj.add_item(FinalAcceptButton(label=view_obj.get_final_button_label()))
            await interaction.response.edit_message(view=view_obj)

class FinalAcceptButton(discord.ui.Button):
    """Button where user indicates they accept all rules; label varies by mode (get role vs start onboarding)."""
    DEFAULT_LABEL = "✅ Accept All Rules & Start Onboarding"

    def __init__(self, label: str | None = None):
        super().__init__(
            label=label or self.DEFAULT_LABEL,
            style=discord.ButtonStyle.success,
            custom_id="final_accept"
        )

    async def callback(self, interaction: discord.Interaction):
        view_obj = self.view
        if not isinstance(view_obj, RuleAcceptanceView):
            await interaction.response.send_message("⚠️ Invalid view state.", ephemeral=True)
            return
        if interaction.user.id != view_obj.member.id:
            await interaction.response.send_message("🚫 You cannot interact with this!", ephemeral=True)
            return
        
        if len(view_obj.accepted_rules) < len(view_obj.rules):
            await interaction.response.send_message("⚠️ You must accept all rules before proceeding!", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        bot_client = cast(commands.Bot, interaction.client)
        settings = getattr(bot_client, "settings", None)
        if not settings:
            await interaction.response.send_message("❌ Bot configuration unavailable.", ephemeral=True)
            return
        enabled = settings.get("onboarding", "enabled", interaction.guild.id)
        mode = settings.get("onboarding", "mode", interaction.guild.id)
        completion_role_id = settings.get("onboarding", "completion_role_id", interaction.guild.id)

        if not enabled or mode == "disabled":
            await interaction.response.send_message("✅ Rules accepted! Welcome to the server.", ephemeral=True)
            return

        # Handle different onboarding modes
        if mode == "rules_only":
            # Assign completion role only when no questions follow (full flow = rules only)
            assigned_role = False
            role = None
            if completion_role_id and completion_role_id != 0:
                try:
                    role = interaction.guild.get_role(completion_role_id)
                    if role:
                        await view_obj.member.add_roles(role)
                        assigned_role = True
                        logger.info(f"✅ Role {role.name} assigned to {view_obj.member.display_name}")
                except Exception as e:
                    logger.warning(f"Could not assign role: {e}")
                    log_operational_event(
                        EventType.ONBOARDING_ERROR,
                        f"Failed to assign completion role: {e}",
                        guild_id=interaction.guild.id,
                        details={
                            "user_id": view_obj.member.id,
                            "role_id": completion_role_id,
                            "error_type": "role_assignment_failed",
                            "error": str(e)
                        }
                    )
            message = "✅ Rules accepted!"
            if assigned_role and role:
                message += f" You have been assigned the {role.mention} role."
            message += " Welcome to the server!"
            await interaction.response.send_message(message, ephemeral=True)

        elif mode in ["rules_with_questions", "questions_only"]:
            # Role is assigned only after full onboarding (in onboarding cog on completion).
            # Do not defer: onboarding will use response.edit_message to replace this rules
            # message with the first question so the user sees one updating message.
            onboarding_cog = bot_client.get_cog("Onboarding")
            if onboarding_cog is not None:
                logger.info("Onboarding Cog found, starting onboarding")
                await onboarding_cog.send_next_question(interaction)  # type: ignore[union-attr]
            else:
                logger.warning("Onboarding Cog not found; check if the onboarding module is loaded")
                await interaction.response.send_message("✅ Rules accepted! Welcome to the server.", ephemeral=True)

        else:
            await interaction.response.send_message("✅ Rules accepted! Welcome to the server.", ephemeral=True)

class ReactionRole(commands.Cog):
    """Rule system that starts onboarding via a button in #rules."""
    def __init__(self, bot):
        self.bot = bot
        # Register the persistent view for the start button
        self.bot.add_view(StartOnboardingView())

    @commands.Cog.listener()
    async def on_ready(self):
        """Place the onboarding button in #rules for all guilds the bot is in."""
        settings = getattr(self.bot, "settings", None)
        if not settings:
            return
        for guild in self.bot.guilds:
            # Get rules channel from settings for this guild
            try:
                rules_channel_id = int(settings.get("system", "rules_channel_id", guild.id))
                if rules_channel_id == 0:
                    # No channel configured for this guild
                    log_with_guild(f"No rules channel configured for guild {guild.name}", guild.id, "debug")
                    continue
                channel = guild.get_channel(rules_channel_id)
                if not channel or not isinstance(channel, discord.abc.Messageable):
                    if not channel:
                        log_with_guild(f"Rules channel {rules_channel_id} not found in guild {guild.name}", guild.id, "warning")
                    continue
            except (KeyError, ValueError):
                # No channel configured, skip
                log_with_guild(f"No rules channel set for guild {guild.name}", guild.id, "debug")
                continue

            # Helper: check if a message contains the Start Onboarding button via custom_id
            def has_start_button(message: discord.Message) -> bool:
                try:
                    for row in getattr(message, "components", []) or []:
                        # ActionRow can have 'children' (discord.py UI) or 'components' (API representation)
                        children = getattr(row, "children", None) or getattr(row, "components", [])
                        for comp in children:
                            if getattr(comp, "custom_id", None) == "start_onboarding":
                                return True
                    return False
                except Exception:
                    return False

            # Check if there's already a message with the onboarding button based on custom_id
            try:
                messages = [message async for message in channel.history(limit=100)]
            except Exception as e:
                log_with_guild(f"Could not read channel history: {e}", guild.id, "warning")
                continue

            persistent_message = None
            for msg in messages:
                if has_start_button(msg):
                    persistent_message = msg
                    break

            if not persistent_message:
                embed = EmbedBuilder.info(
                    title=f"Welcome to {guild.name}",
                    description="The place where your learning and growth journey begins! 🌟\n\nTo get started, complete the verification by clicking the button below:"
                )
                embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1263189905555849317/1336037428049477724/Alpha_afbeelding_vierkant.png")
                try:
                    await channel.send(embed=embed, view=StartOnboardingView())
                    log_with_guild(f"Onboarding button placed in #{getattr(channel, 'name', 'channel')}", guild.id, "info")
                except Exception as e:
                    log_with_guild(f"Could not place onboarding button: {e}", guild.id, "warning")
            else:
                log_with_guild("Persistent onboarding message found; no duplicate sent", guild.id, "debug")

async def setup(bot):
    await bot.add_cog(ReactionRole(bot))
