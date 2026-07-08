import asyncio
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from gpt.helpers import ask_gpt_vision
from utils.cog_base import AlphaCog
from utils.database_helpers import DatabaseManager
from utils.db_helpers import acquire_safe, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import log_database_event, log_guild_action, log_with_guild, logger
from utils.premium_guard import guild_has_premium
from utils.sanitizer import safe_embed_text, safe_prompt
from utils.timezone import BRUSSELS_TZ
from utils.user_messages import ERR_GUILD_ONLY


class VerificationCog(AlphaCog):
    """
    Lets guilds run their own payment verification: public area + paid area gated by a verified role.
    Members submit a payment screenshot (for the guild's products/events/access); after AI or manual
    review they get the verified role. Not for Alphapy premium—this is a premium feature for guilds.
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("verification", bot=bot)

        # Start async setup without blocking the event loop
        self.bot.loop.create_task(self.setup_db())
        # Register persistent panel view so the button keeps working after restarts
        try:
            self.bot.add_view(VerificationPanelView(self, timeout=None))
        except Exception as e:
            logger.warning(f"⚠️ VerificationCog: could not register VerificationPanelView: {e}")
        try:
            self.bot.add_view(ManualReviewView(self, timeout=None))
        except Exception as e:
            logger.warning(f"⚠️ VerificationCog: could not register ManualReviewView: {e}")
        try:
            self.bot.add_view(VerificationCloseView(self, timeout=None))
        except Exception as e:
            logger.warning(f"⚠️ VerificationCog: could not register VerificationCloseView: {e}")

    async def setup_db(self) -> None:
        """Initialize database pool and ensure verification_tickets table exists."""
        try:
            pool = await self._db_manager.ensure_pool()
            self.db = pool
            log_database_event("DB_CONNECTED", details="Verification using shared bot database pool")

            async with acquire_safe(pool) as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS verification_tickets (
                        id SERIAL PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        ai_can_verify BOOLEAN,
                        ai_needs_manual_review BOOLEAN,
                        ai_reason TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        resolved_at TIMESTAMPTZ
                    );
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_verification_tickets_guild_status ON verification_tickets(guild_id, status);"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_verification_tickets_channel_id ON verification_tickets(channel_id);"
                )
                # Idempotent schema additions for audit trail
                await conn.execute(
                    "ALTER TABLE verification_tickets ADD COLUMN IF NOT EXISTS resolved_by_user_id BIGINT;"
                )
                await conn.execute(
                    "ALTER TABLE verification_tickets ADD COLUMN IF NOT EXISTS rejection_reason TEXT;"
                )
                await conn.execute(
                    "ALTER TABLE verification_tickets ADD COLUMN IF NOT EXISTS payment_date DATE;"
                )

            logger.info("VerificationCog: DB ready (verification_tickets)")
            log_database_event("DB_READY", details="VerificationCog database fully initialized")
        except Exception as e:
            log_database_event("DB_INIT_ERROR", details=f"VerificationCog setup failed: {e}")
            logger.error(f"VerificationCog: DB init error: {e}")
            self.db = None

    async def cog_unload(self) -> None:
        self.db = None

    # ----- Settings helpers -----

    def _get_verified_role_id(self, guild_id: int) -> int | None:
        value = self.settings_helper.get_int("verification", "verified_role_id", guild_id, fallback=0)
        return int(value) if value else None

    def _get_panel_channel_id(self, guild_id: int) -> int | None:
        value = self.settings_helper.get_int("verification", "channel_id", guild_id, fallback=0)
        return int(value) if value else None

    def _get_category_id(self, guild_id: int) -> int | None:
        value = self.settings_helper.get_int("verification", "category_id", guild_id, fallback=0)
        return int(value) if value else None

    def _get_vision_model(self, guild_id: int) -> str | None:
        try:
            raw = self.settings.get("verification", "vision_model", guild_id)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        except Exception:
            pass
        return None

    def _get_log_channel_id(self, guild_id: int) -> int:
        value = self.settings_helper.get_int("system", "log_channel_id", guild_id, fallback=0)
        return int(value) if value else 0

    def _get_ai_prompt_context(self, guild_id: int) -> str:
        return self.settings_helper.get_str("verification", "ai_prompt_context", guild_id, fallback="")

    def _get_max_payment_age_days(self, guild_id: int) -> int:
        value = self.settings_helper.get_int("verification", "max_payment_age_days", guild_id, fallback=0)
        return int(value) if value and value > 0 else 35

    def _get_reviewer_role_id(self, guild_id: int) -> int | None:
        value = self.settings_helper.get_int("verification", "reviewer_role_id", guild_id, fallback=0)
        return int(value) if value else None

    async def _get_reference_image_url(self, guild_id: int) -> str | None:
        """Fetch a fresh URL for the stored reference image by re-fetching its Discord message."""
        channel_id = self.settings_helper.get_int("verification", "reference_image_channel_id", guild_id, fallback=0)
        message_id_str = self.settings_helper.get_str("verification", "reference_image_message_id", guild_id, fallback="").strip("\"'")
        if not channel_id or not message_id_str:
            return None
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel or not hasattr(channel, "fetch_message"):
                channel = await self.bot.fetch_channel(channel_id)
            text_channel = cast(discord.TextChannel, channel)
            msg = await text_channel.fetch_message(int(message_id_str))
            for att in msg.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    return att.url
        except Exception as e:
            logger.warning("VerificationCog: could not fetch reference image: %s", e)
        return None

    async def send_log_embed(self, title: str, description: str, level: str, guild_id: int) -> None:
        """Send log embed to the guild's log channel using EmbedBuilder."""
        if guild_id == 0:
            logger.warning("⚠️ VerificationCog send_log_embed called without guild_id")
            return

        from utils.logger import should_log_to_discord

        if not should_log_to_discord(level, guild_id):
            return

        try:
            embed = EmbedBuilder.log(title, description, level, guild_id)
            embed.set_footer(text=f"verification | Guild: {guild_id}")

            channel_id = self._get_log_channel_id(guild_id)
            if channel_id == 0:
                log_with_guild("No log channel configured for verification logging", guild_id, "debug")
                return

            channel = self.bot.get_channel(channel_id)
            if channel and hasattr(channel, "send"):
                text_channel = cast(discord.TextChannel, channel)
                await text_channel.send(embed=embed)
                log_guild_action(guild_id, "LOG_SENT", details=f"verification: {title}")
            else:
                log_with_guild(f"Verification log channel {channel_id} not found or not accessible", guild_id, "warning")
        except Exception as e:
            log_with_guild(f"Could not send verification log embed: {e}", guild_id, "error")

    # ----- Commands -----

    @app_commands.command(
        name="verification_panel_post",
        description="Post a verification panel with a Start verification button.",
    )
    @app_commands.guild_only()
    @app_commands.describe(channel="Channel where the panel should be posted (defaults to current channel)")
    async def verification_panel_post(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        from utils.validators import validate_admin

        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message(ERR_GUILD_ONLY, ephemeral=True)
            return

        target = channel or cast(discord.TextChannel, interaction.channel)
        if target is None:
            await interaction.response.send_message("❌ No channel specified.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        if not await guild_has_premium(guild_id):
            await interaction.response.send_message(
                "Verification is a premium feature for this server. Use /premium to assign premium to this server first, then you can post the verification panel.",
                ephemeral=True,
            )
            return

        category_id = self._get_category_id(guild_id)
        verified_role_id = self._get_verified_role_id(guild_id)

        if not category_id or not verified_role_id:
            await interaction.response.send_message(
                "⚠️ Verification is not fully configured yet. "
                "Set a verification category and verified role first (`/verification set_category`, `/verification set_verified_role`).",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ Verify your access",
            description=(
                "Unlock the verified role in three steps:\n\n"
                "1. Tap **Start verification**.\n"
                "2. Upload a clear payment screenshot (name visible if possible).\n"
                "3. Get auto-approved or wait for team review."
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(BRUSSELS_TZ),
        )
        from version import CODENAME, __version__
        embed.set_footer(text=f"Innersync • Alphapy v{__version__} — {CODENAME}")

        view = VerificationPanelView(self, timeout=None)
        await target.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Verification panel posted.", ephemeral=True)

    @app_commands.command(
        name="verification_close",
        description="Close a verification channel after manual review.",
    )
    @app_commands.guild_only()
    async def verification_close(self, interaction: discord.Interaction) -> None:
        from utils.validators import validate_admin

        if not interaction.guild:
            await interaction.response.send_message(
                ERR_GUILD_ONLY,
                ephemeral=True,
            )
            return

        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This command can only be used in a text channel.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id
        category_id = self._get_category_id(guild_id)
        if not category_id or not channel.category or channel.category.id != category_id:
            await interaction.response.send_message(
                "❌ This command can only be used inside a verification channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        member = guild.get_member(interaction.user.id) if guild else None

        await interaction.followup.send("✅ Verification channel is being closed.", ephemeral=True)

        await self._resolve_verification(
            channel=channel,
            member=member,
            guild=guild,
            resolved_by=interaction.user,
            outcome="closed",
        )

    # ----- Ticket helpers -----

    async def _create_verification_channel(self, interaction: discord.Interaction) -> discord.TextChannel | None:
        if not interaction.guild:
            return None

        guild = interaction.guild
        guild_id = guild.id
        user = interaction.user

        category_id = self._get_category_id(guild_id)
        if not category_id:
            await interaction.followup.send(
                "❌ Verification category is not configured. Please ask an admin to configure verification.",
                ephemeral=True,
            )
            return None

        try:
            fetched_channel = guild.get_channel(category_id) or await self.bot.fetch_channel(category_id)
            if not isinstance(fetched_channel, discord.CategoryChannel):
                raise RuntimeError("Configured verification category is not a category channel")
            category = fetched_channel
        except Exception as e:
            logger.warning(f"VerificationCog: could not fetch category {category_id}: {e}")
            await interaction.followup.send(
                "❌ Verification category could not be found. Please ask an admin to fix the configuration.",
                ephemeral=True,
            )
            return None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        channel = await guild.create_text_channel(
            name=f"verify-{user.name}".lower().replace(" ", "-"),
            category=category,
            overwrites=overwrites,
            reason=f"Verification ticket for {user}",
        )

        await self._store_verification_ticket(guild_id=guild_id, user_id=int(user.id), channel_id=int(channel.id))

        intro = EmbedBuilder.info(
            title="📸 Verification started",
            description=(
                "Please upload **one clear screenshot** of your payment or subscription confirmation.\n\n"
                "- Make sure the **amount** and **date** are visible.\n"
                "- Make sure your **name or account username** is visible.\n"
                "- You may blur sensitive details like card numbers.\n\n"
                "Once you upload the screenshot, our AI will review it."
            ),
        )
        await channel.send(content=user.mention, embed=intro)
        return channel

    async def _store_verification_ticket(self, guild_id: int, user_id: int, channel_id: int) -> None:
        if not is_pool_healthy(self.db):
            return
        try:
            async with acquire_safe(self.db) as conn:
                await conn.execute(
                    """
                    INSERT INTO verification_tickets (guild_id, user_id, channel_id, status)
                    VALUES ($1, $2, $3, 'pending')
                    """,
                    guild_id,
                    user_id,
                    channel_id,
                )
        except RuntimeError:
            logger.debug("VerificationCog: database pool not available when storing ticket")
        except Exception as e:
            logger.warning(f"VerificationCog: failed to store verification ticket: {e}")

    async def _update_verification_ticket(
        self,
        channel_id: int,
        *,
        status: str,
        ai_can_verify: bool | None,
        ai_needs_manual_review: bool | None,
        ai_reason: str | None,
        resolved_by_user_id: int | None = None,
        rejection_reason: str | None = None,
        payment_date: date | None = None,
    ) -> None:
        if not is_pool_healthy(self.db):
            return
        try:
            async with acquire_safe(self.db) as conn:
                await conn.execute(
                    """
                    UPDATE verification_tickets
                    SET status = $1,
                        ai_can_verify = $2,
                        ai_needs_manual_review = $3,
                        ai_reason = $4,
                        resolved_at = CASE WHEN $1 <> 'pending' THEN NOW() ELSE resolved_at END,
                        resolved_by_user_id = COALESCE($6, resolved_by_user_id),
                        rejection_reason = COALESCE($7, rejection_reason),
                        payment_date = COALESCE($8, payment_date)
                    WHERE channel_id = $5
                    """,
                    status,
                    ai_can_verify,
                    ai_needs_manual_review,
                    ai_reason,
                    channel_id,
                    resolved_by_user_id,
                    rejection_reason,
                    payment_date,
                )
        except RuntimeError:
            logger.debug("VerificationCog: database pool not available when updating ticket")
        except Exception as e:
            logger.warning(f"VerificationCog: failed to update verification ticket: {e}")

    async def _resolve_verification(
        self,
        *,
        channel: discord.TextChannel,
        member: discord.Member | None,
        guild: discord.Guild,
        resolved_by: discord.abc.User | None,
        outcome: Literal["approved", "rejected", "closed"],
        reason: str = "",
        started_at: datetime | None = None,
    ) -> None:
        """Unified resolution handler for all verification outcomes.

        Assigns/removes roles (on approval), updates DB, sends in-channel embed,
        sends a standardised log summary, and deletes the channel after 5 seconds.
        """
        guild_id = guild.id

        # 1. Role assignment / removal on approval
        if outcome == "approved" and member:
            verified_role_id = self._get_verified_role_id(guild_id)
            if verified_role_id:
                role = guild.get_role(verified_role_id)
                if role:
                    try:
                        await member.add_roles(role, reason="Verification approved")
                    except Exception as e:
                        logger.warning(f"VerificationCog: could not assign verified role: {e}")
            try:
                join_role_id = self.settings_helper.get_int("onboarding", "join_role_id", guild_id, fallback=0)
            except Exception:
                join_role_id = 0
            if join_role_id:
                join_role = guild.get_role(int(join_role_id))
                if join_role and member and any(r.id == join_role.id for r in member.roles):
                    try:
                        await member.remove_roles(join_role, reason="Remove join role after verification")
                    except Exception as e:
                        logger.warning(f"VerificationCog: could not remove join role: {e}")

        # 2. DB update — only resolution fields; AI fields already set by on_message
        resolved_by_user_id = int(resolved_by.id) if resolved_by else None
        db_status = {"approved": "verified", "rejected": "rejected", "closed": "closed_manual"}[outcome]
        if is_pool_healthy(self.db):
            try:
                async with acquire_safe(self.db) as conn:
                    await conn.execute(
                        """
                        UPDATE verification_tickets
                        SET status = $1,
                            resolved_at = NOW(),
                            resolved_by_user_id = $2,
                            rejection_reason = COALESCE($3, rejection_reason)
                        WHERE channel_id = $4
                        """,
                        db_status,
                        resolved_by_user_id,
                        reason if outcome == "rejected" else None,
                        int(channel.id),
                    )
            except Exception as e:
                logger.warning(f"VerificationCog: failed to update ticket resolution: {e}")

        # 3. In-channel closing embed
        if outcome == "approved":
            closing_embed = EmbedBuilder.success(
                title="✅ Verification approved",
                description="Your verification has been approved. You now have access to the verified area.",
            )
        elif outcome == "rejected":
            closing_embed = EmbedBuilder.error(
                title="❌ Verification rejected",
                description=(
                    "Your verification was not approved.\n\n"
                    + (f"Reason: {safe_embed_text(reason, 512)}\n\n" if reason else "")
                    + "If you think this is a mistake, please contact the team."
                ),
            )
        else:
            closing_embed = EmbedBuilder.warning(
                title="🔒 Verification closed",
                description="This verification has been closed. If you think this is a mistake, please contact the team.",
            )

        try:
            await channel.send(embed=closing_embed)
        except Exception:
            pass

        # 4. Standardised log summary (no payment details)
        resolver_label = resolved_by.mention if resolved_by else "AI (auto)"
        outcome_label = {"approved": "✅ Approved", "rejected": "❌ Rejected", "closed": "🔒 Closed"}[outcome]
        log_desc_parts = [
            f"User: {member.mention if member else 'unknown'} ({member.id if member else '?'})",
            f"Resolved by: {resolver_label}",
            f"Outcome: {outcome_label}",
        ]
        if started_at:
            log_desc_parts.insert(1, f"Started: {started_at.strftime('%Y-%m-%d %H:%M UTC')}")

        log_level = "success" if outcome == "approved" else ("error" if outcome == "rejected" else "info")
        await self.send_log_embed(
            title=f"Verification {outcome_label}",
            description="\n".join(log_desc_parts),
            level=log_level,
            guild_id=guild_id,
        )

        # 5. On close: delete channel after 5s.
        #    On approve/reject: lock channel and post a Close button for the admin.
        if outcome == "closed":
            async def _delete_channel() -> None:
                await asyncio.sleep(5)
                try:
                    await channel.delete(reason="Verification closed — auto-cleanup")
                except Exception as e:
                    logger.debug(f"VerificationCog: could not delete verification channel: {e}")

            asyncio.create_task(_delete_channel())
        else:
            try:
                overwrites = channel.overwrites
                overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                if member:
                    overwrites[member] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=False,
                        read_message_history=True,
                    )
                await channel.edit(overwrites=overwrites, reason=f"Verification {outcome} — locked for review")
            except Exception as e:
                logger.debug(f"VerificationCog: could not lock verification channel: {e}")

            try:
                await channel.send(view=VerificationCloseView(self, timeout=None))
            except Exception as e:
                logger.debug(f"VerificationCog: could not send close button: {e}")

    # ----- Listener -----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle screenshots uploaded in verification channels."""
        if message.author.bot:
            return
        if not message.guild:
            return
        if not message.attachments:
            return

        guild = message.guild
        guild_id = guild.id
        category_id = self._get_category_id(guild_id)
        if not category_id:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return
        if not message.channel.category or message.channel.category.id != category_id:
            return

        # Only process the first image attachment
        attachment = None
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                attachment = att
                break
        if not attachment:
            return

        # Defer basic acknowledgement in-channel
        processing_embed = EmbedBuilder.status(
            title="🔍 Verifying your screenshot",
            description="Please wait a moment while we review your payment confirmation.",
        )
        await message.channel.send(embed=processing_embed)

        # Build verification prompt
        vision_model = self._get_vision_model(guild_id)
        reference_image_url = await self._get_reference_image_url(guild_id)
        max_payment_age_days = self._get_max_payment_age_days(guild_id)
        today_iso = date.today().isoformat()
        discord_display_name = safe_prompt(message.author.display_name)
        discord_username = safe_prompt(message.author.name)

        identity_block = (
            f'\nThe user\'s Discord display name is: "{discord_display_name}".\n'
            f'The user\'s Discord username is: "{discord_username}".\n\n'
            "Check whether any name, username, or account ID visible in the screenshot could correspond to this Discord user.\n"
            'Set identifier_match to "match" if something in the screenshot (name, username, user ID) plausibly corresponds to this Discord user, '
            '"mismatch" if a name or ID IS clearly visible but does NOT match, '
            'or "not_visible" if no name, username, or account ID is readable in the screenshot.\n'
        )

        if reference_image_url:
            prompt = (
                "You are given two images.\n"
                "- Image 1: submitted by a user who wants access.\n"
                "- Image 2: a reference example set by the server admin that defines what an acceptable submission looks like.\n\n"
                f"Today's date is {today_iso}. The maximum allowed payment age is {max_payment_age_days} days.\n\n"
                "Step 1 — Document match: decide whether Image 1 shows the same kind of document or screen as Image 2.\n"
                "Step 2 — Classify Image 1 as one of two proof types:\n"
                '  - "payment": a receipt or transaction confirmation with a visible payment date.\n'
                '  - "subscription": an account or billing page showing an active subscription (e.g. "Status: Active", a future next-billing date, active plan count) — no payment date required.\n'
                "Step 3 — Validate:\n"
                '  - If proof_type is "payment": the payment date must be within the allowed window. If older, set can_verify to false. If date is unreadable, set needs_manual_review to true.\n'
                '  - If proof_type is "subscription": set can_verify to true if the screenshot clearly shows an active, paid subscription right now.\n'
                "Step 4 — Identity check:\n"
                + identity_block +
                "\nSet can_verify to true only if Steps 1, 3, and 4 all pass (identifier_match must not be \"mismatch\").\n"
                "Set needs_manual_review to true if the image is too blurry/cropped to compare, a payment date is required but unreadable, or identifier_match is \"not_visible\".\n\n"
                "Respond in JSON only, no other text:\n"
                '{"can_verify": boolean, "needs_manual_review": boolean, "proof_type": "payment" | "subscription" | "unknown", '
                '"identifier_match": "match" | "mismatch" | "not_visible", "reason": "one sentence max, no PII", "payment_date": "YYYY-MM-DD or null"}'
            )
        else:
            prompt = (
                "You are a payment verification assistant. Classify what the screenshot shows and decide whether it proves the user has an active, paid subscription.\n\n"
                f"Today's date is {today_iso}. The maximum allowed payment age is {max_payment_age_days} days.\n\n"
                "Classify the screenshot as one of two proof types:\n"
                '- "payment": a payment receipt, transaction confirmation, or order summary that shows a completed charge with a visible date.\n'
                '- "subscription": an account or billing page that shows an active subscription without a specific payment date\n'
                '  (e.g. "Status: Active", a future next-billing date like "Next billing: May 11", active plan count, active subscription badge).\n\n'
                "Rules:\n"
                '- If proof_type is "payment": set can_verify to true only if a payment date is visible AND within the allowed window. Set to false if older.\n'
                '- If proof_type is "subscription": set can_verify to true if the screenshot clearly shows an active, paid subscription right now. No payment date required.\n'
                "- Set needs_manual_review to true if the screenshot is too blurry, cropped, or ambiguous to make a confident decision.\n\n"
                "Identity check:\n"
                + identity_block +
                "\nRespond in **JSON only**, no other text:\n"
                "{\n"
                '  "can_verify": boolean,\n'
                '  "needs_manual_review": boolean,\n'
                '  "proof_type": "payment" | "subscription" | "unknown",\n'
                '  "identifier_match": "match" | "mismatch" | "not_visible",\n'
                '  "reason": "one sentence, no PII",\n'
                '  "payment_date": "YYYY-MM-DD or null"\n'
                "}\n\n"
                '- payment_date: the transaction date if proof_type is "payment", otherwise null.\n'
                '- can_verify must be false if identifier_match is "mismatch".'
            )

        ai_prompt_context = self._get_ai_prompt_context(guild_id)
        if ai_prompt_context:
            prompt += f"\n\nAdditional context from the server admin:\n{safe_prompt(ai_prompt_context)}"

        image_url = attachment.url

        try:
            result_text = await ask_gpt_vision(
                prompt,
                image_url,
                user_id=int(message.author.id),
                model=vision_model,
                guild_id=guild_id,
                extra_image_urls=[reference_image_url] if reference_image_url else None,
                system_prompt=(
                    "You are an image analysis assistant. "
                    "You analyze screenshots and respond ONLY with the JSON object specified in the user message. "
                    "Never include any text, explanation, or formatting outside the JSON object."
                ),
            )
        except Exception as e:
            logger.exception(f"VerificationCog: vision call failed: {e}")
            await message.channel.send(
                embed=EmbedBuilder.error(
                    title="❌ Verification failed",
                    description="Something went wrong while contacting the AI service. Please try again later or contact support.",
                )
            )
            await self._update_verification_ticket(
                channel_id=int(message.channel.id),
                status="error",
                ai_can_verify=None,
                ai_needs_manual_review=None,
                ai_reason=str(e),
            )
            return

        can_verify = False
        needs_manual_review = True
        reason = "Unclear AI result."
        parsed: dict[str, Any] = {}

        try:
            raw = (result_text or "").strip().lstrip("\ufeff")  # strip BOM if present

            # Strategy 1: extract from ``` fences (handles ```json and plain ```)
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
            if fence_match:
                clean_text = fence_match.group(1).strip()
            else:
                # Strategy 2: find first { to last }
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end > start:
                    clean_text = raw[start: end + 1]
                else:
                    # No JSON structure found — log format for diagnosis, default to manual review
                    logger.warning(
                        "VerificationCog: AI returned no JSON structure (length=%s): %r",
                        len(raw),
                        raw,
                    )
                    clean_text = "{}"

            parsed = json.loads(clean_text or "{}")
            if isinstance(parsed, dict) and "can_verify" in parsed:
                can_verify = bool(parsed.get("can_verify", False))
                needs_manual_review = bool(parsed.get("needs_manual_review", not can_verify))
                reason_val = parsed.get("reason")
                if isinstance(reason_val, str) and reason_val.strip():
                    reason = reason_val.strip()
            elif isinstance(parsed, dict):
                # AI returned JSON but with unexpected keys — log it
                logger.warning(
                    "VerificationCog: AI returned unexpected JSON schema (keys=%s)",
                    list(parsed.keys()),
                )
        except Exception as e:
            # Avoid logging the full AI response to reduce risk of PII in logs
            logger.warning(
                "VerificationCog: could not parse vision JSON: %s (length=%s, start=%r)",
                e,
                len(result_text or ""),
                (result_text or "")[:80],
            )

        # Server-side validation — branches on proof_type
        extracted_payment_date: date | None = None
        proof_type = parsed.get("proof_type", "unknown") if isinstance(parsed, dict) else "unknown"

        if proof_type == "subscription":
            # Active-subscription proof: no date check needed — trust the AI's can_verify decision
            pass
        else:
            # proof_type == "payment" or "unknown": apply date recency check
            try:
                payment_date_str = parsed.get("payment_date") if isinstance(parsed, dict) else None
                if isinstance(payment_date_str, str) and payment_date_str.strip() and payment_date_str.strip().lower() != "null":
                    extracted_payment_date = date.fromisoformat(payment_date_str.strip())
                    cutoff = date.today() - timedelta(days=max_payment_age_days)
                    if extracted_payment_date < cutoff:
                        # AI may have passed it but the date is provably too old — hard reject
                        can_verify = False
                        needs_manual_review = False
                        reason = (
                            f"Payment date ({extracted_payment_date.isoformat()}) is outside the "
                            f"allowed {max_payment_age_days}-day window. Please submit a more recent proof of payment."
                        )
                        logger.info(
                            "VerificationCog: server-side date check rejected payment (date=%s, cutoff=%s, guild=%s)",
                            extracted_payment_date,
                            cutoff,
                            guild_id,
                        )
                elif can_verify and not needs_manual_review:
                    # AI approved a payment receipt but no date was readable — escalate to manual review
                    needs_manual_review = True
                    reason = "Payment date could not be determined from the screenshot. Manual review required."
            except (ValueError, AttributeError):
                pass  # Malformed date string from AI — fall through to AI's own decision

        # Server-side identity check (belt-and-suspenders on top of AI identifier_match)
        identifier_match = parsed.get("identifier_match", "not_visible") if isinstance(parsed, dict) else "not_visible"
        if identifier_match == "mismatch":
            can_verify = False
            needs_manual_review = False
            reason = "The name or account ID in the screenshot does not match your Discord identity. Please submit a screenshot from your own account."
            logger.info(
                "VerificationCog: identity mismatch rejected (user=%s, guild=%s)",
                message.author.id,
                guild_id,
            )
        elif identifier_match == "not_visible" and can_verify and not needs_manual_review:
            needs_manual_review = True
            reason = "Could not verify your identity from the screenshot. Please make sure your name or account username is visible. Manual review required."

        # Update DB
        await self._update_verification_ticket(
            channel_id=int(message.channel.id),
            status="verified" if can_verify and not needs_manual_review else "manual_review",
            ai_can_verify=can_verify,
            ai_needs_manual_review=needs_manual_review,
            ai_reason=reason,
            payment_date=extracted_payment_date,
        )

        # Act on result
        member = guild.get_member(message.author.id)
        channel = cast(discord.TextChannel, message.channel)

        if can_verify and not needs_manual_review:
            await self._resolve_verification(
                channel=channel,
                member=member,
                guild=guild,
                resolved_by=None,
                outcome="approved",
                reason=reason,
            )
        else:
            warn_embed = EmbedBuilder.warning(
                title="👀 Manual review required",
                description=(
                    "The AI could not confidently verify this screenshot.\n"
                    "A member of the team will review your submission manually.\n\n"
                    f"Reason: {safe_embed_text(reason, 512)}"
                ),
            )
            reviewer_role_id = self._get_reviewer_role_id(guild_id)
            reviewer_mention = f"<@&{reviewer_role_id}>" if reviewer_role_id else None
            await channel.send(
                content=reviewer_mention,
                embed=warn_embed,
                view=ManualReviewView(self, timeout=None),
            )

            await self.send_log_embed(
                title="⚠️ Verification needs manual review",
                description=(
                    f"User: {message.author.mention} ({message.author.id})\n"
                    f"Channel: {channel.mention}\n"
                    f"Started: {datetime.now(BRUSSELS_TZ).strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"AI reason: {safe_embed_text(reason, 300)}"
                ),
                level="warning",
                guild_id=guild_id,
            )


async def _fetch_ticket_member(cog: "VerificationCog", channel: discord.TextChannel, guild: discord.Guild) -> discord.Member | None:
    """Look up the member who owns a verification ticket by channel_id."""
    if not (cog.db and is_pool_healthy(cog.db)):
        return None
    try:
        async with acquire_safe(cog.db) as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM verification_tickets WHERE channel_id = $1",
                int(channel.id),
            )
            if row:
                return guild.get_member(int(row["user_id"]))
    except Exception as e:
        logger.warning(f"ManualReviewView: could not fetch ticket member: {e}")
    return None


class RejectReasonModal(discord.ui.Modal, title="Rejection reason"):
    reason = discord.ui.TextInput(
        label="Reason (shown to the user)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Screenshot is too blurry or does not show a valid payment.",
        required=False,
        max_length=512,
    )

    def __init__(self, cog: "VerificationCog", channel: discord.TextChannel, guild: discord.Guild) -> None:
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        reason_text = self.reason.value.strip() if self.reason.value else ""
        member = await _fetch_ticket_member(self.cog, self.channel, self.guild)
        await self.cog._resolve_verification(
            channel=self.channel,
            member=member,
            guild=self.guild,
            resolved_by=interaction.user,
            outcome="rejected",
            reason=reason_text,
        )
        await interaction.followup.send("❌ Verification rejected.", ephemeral=True)


class ManualReviewView(discord.ui.View):
    def __init__(self, cog: "VerificationCog", timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Return True if the interaction passes admin and channel checks."""
        from utils.validators import validate_admin

        if not interaction.guild:
            await interaction.response.send_message("❌ Only works in a server.", ephemeral=True)
            return False
        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return False
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ Unexpected channel type.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="verification_approve_btn",
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        guild = cast(discord.Guild, interaction.guild)
        channel = cast(discord.TextChannel, interaction.channel)
        member = await _fetch_ticket_member(self.cog, channel, guild)

        await self.cog._resolve_verification(
            channel=channel,
            member=member,
            guild=guild,
            resolved_by=interaction.user,
            outcome="approved",
        )
        await interaction.followup.send("✅ Verification approved.", ephemeral=True)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id="verification_reject_btn",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return

        guild = cast(discord.Guild, interaction.guild)
        channel = cast(discord.TextChannel, interaction.channel)
        await interaction.response.send_modal(RejectReasonModal(self.cog, channel, guild))


class VerificationCloseView(discord.ui.View):
    """Posted after approve/reject so an admin can delete the channel via button."""

    def __init__(self, cog: "VerificationCog", timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    @discord.ui.button(
        label="Close channel",
        style=discord.ButtonStyle.secondary,
        custom_id="verification_close_btn",
        emoji="🔒",
    )
    async def close_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from utils.validators import validate_admin

        if not interaction.guild:
            await interaction.response.send_message("❌ Only works in a server.", ephemeral=True)
            return

        is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
        if not is_admin:
            await interaction.response.send_message(error_msg or "⛔ Admins only.", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ Unexpected channel type.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = cast(discord.Guild, interaction.guild)
        channel = cast(discord.TextChannel, interaction.channel)
        member = await _fetch_ticket_member(self.cog, channel, guild)

        await self.cog._resolve_verification(
            channel=channel,
            member=member,
            guild=guild,
            resolved_by=interaction.user,
            outcome="closed",
        )
        await interaction.followup.send("🔒 Channel will be deleted shortly.", ephemeral=True)


class VerificationPanelView(discord.ui.View):
    def __init__(self, cog: VerificationCog, timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    @discord.ui.button(
        label="Start verification",
        style=discord.ButtonStyle.primary,
        custom_id="verification_start_btn",
    )
    async def start_verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(ERR_GUILD_ONLY, ephemeral=True)
            return

        if not await guild_has_premium(interaction.guild.id):
            await interaction.followup.send(
                "Verification is a premium feature for this server. An admin can use /premium to unlock it for this server.",
                ephemeral=True,
            )
            return

        try:
            channel = await self.cog._create_verification_channel(interaction)
        except Exception as e:
            logger.exception(f"VerificationPanelView: could not create verification channel: {e}")
            await interaction.followup.send(
                "❌ Something went wrong while creating your verification channel. Please try again later.",
                ephemeral=True,
            )
            return

        if not channel:
            return

        await interaction.followup.send(f"✅ Verification channel created: {channel.mention}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))

