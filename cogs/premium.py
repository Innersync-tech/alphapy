"""Premium tier: /premium command and admin /premium_check."""

import logging
import time
from datetime import UTC, datetime

import asyncpg
import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

# Version string of the currently published Terms of Service / Privacy Policy.
# Must match the `_Last updated:` date in docs/privacy-policy.md.
# Updating this causes existing acceptors to be re-prompted on their next /premium use.
CURRENT_TERMS_VERSION = "2026-03-02"

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

if True:
    from utils.database_helpers import DatabaseManager
    from utils.db_helpers import acquire_safe, is_pool_healthy
    from utils.premium_guard import (
        get_active_premium_guild,
        get_premium_status,
        get_user_tier,
        is_premium,
        transfer_premium_to_guild,
    )
    from utils.premium_tiers import GPT_DAILY_LIMIT, REMINDER_LIMIT
    from utils.timezone import BRUSSELS_TZ
    from utils.validators import validate_admin
    from version import CODENAME, __version__

logger = logging.getLogger(__name__)

# Founder guild ID from environment (for founder role assignment)
FOUNDER_GUILD_ID = int(getattr(config, 'FOUNDER_GUILD_ID', 0))


class TermsAcceptanceView(discord.ui.View):
    """View with Accept/Decline buttons for Terms acceptance."""

    def __init__(self, user_id: int):
        super().__init__(timeout=300)  # 5 minutes
        self.user_id = user_id

    @discord.ui.button(label="✅ Accept Terms", style=discord.ButtonStyle.success)
    async def accept_terms(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not for you!", ephemeral=True)
            return

        # Save acceptance to database
        cog = interaction.client.get_cog('PremiumCog')  # type: ignore
        if not cog:
            logger.error("PremiumCog not found for terms acceptance")
            await interaction.response.send_message(
                "❌ There was an error processing your acceptance. Please contact [support@innersync.tech](mailto:support@innersync.tech) for assistance.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await _save_terms_acceptance(cog, interaction.user.id, interaction.user.id)
            guild_id = interaction.guild.id if interaction.guild else 0
            guild_name = interaction.guild.name if interaction.guild else None
            embed, view = await _build_premium_embed_and_view(
                guild_id, interaction.user.id, guild_name
            )
            # Ephemeral messages can only be updated via the interaction webhook, not Message.edit()
            await interaction.edit_original_response(
                content="Terms accepted. Choose your plan below.",
                embed=embed,
                view=view,
            )
        except Exception as e:
            logger.error(f"Failed to save terms acceptance for user {interaction.user.id}: {e}")
            error_msg = (
                "There was an error processing your acceptance. "
                "Please contact [support@innersync.tech](mailto:support@innersync.tech) for assistance."
            )
            # Ephemeral messages can only be updated via the interaction webhook
            await interaction.edit_original_response(content=error_msg)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline_terms(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not for you!", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Terms Declined",
            description="You must accept our Terms and Privacy Policy to use premium features.\n\nYou can try again later with `/premium`.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)


async def _save_terms_acceptance(cog: 'PremiumCog', user_id: int, accepted_by: int) -> None:
    """Save terms acceptance to database for GDPR compliance. Uses cog's shared pool."""
    if not is_pool_healthy(cog.db):
        raise RuntimeError("Database pool not available")
    try:
        async with acquire_safe(cog.db) as conn:
            await conn.execute(
                "INSERT INTO terms_acceptance (user_id, accepted_at, version) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
                user_id, datetime.utcnow(), CURRENT_TERMS_VERSION
            )
        logger.info(f"Terms accepted by user {user_id}")
    except Exception as e:
        logger.error(f"Failed to save terms acceptance for user {user_id}: {e}")
        raise


async def _has_accepted_terms(cog: 'PremiumCog', user_id: int) -> bool:
    """Check if user has accepted current terms version. Uses cog's shared pool."""
    if not is_pool_healthy(cog.db):
        return False
    try:
        async with acquire_safe(cog.db) as conn:
            result = await conn.fetchval(
                "SELECT 1 FROM terms_acceptance WHERE user_id = $1 AND version = $2",
                user_id, CURRENT_TERMS_VERSION
            )
        return result is not None
    except Exception as e:
        logger.error(f"Failed to check terms acceptance for user {user_id}: {e}")
        return False


async def _assign_founder_role_if_eligible(cog: 'PremiumCog', user_id: int, guild_id: int, tier: str) -> None:
    """Assign founder role to early bird lifetime members in founder guild."""
    # Only assign founder role in configured founder guild
    if guild_id != FOUNDER_GUILD_ID or FOUNDER_GUILD_ID == 0:
        return

    # Only lifetime tier gets founder role (early bird pricing)
    if tier != "lifetime":
        return

    # Get the guild and member efficiently
    guild = cog.bot.get_guild(FOUNDER_GUILD_ID)
    if not guild:
        logger.warning(f"Founder role: Could not find founder guild {FOUNDER_GUILD_ID}")
        return

    if cog.bot.user is None:
        return

    # Check bot permissions before attempting role assignment
    bot_member = guild.get_member(cog.bot.user.id)
    if not bot_member or not bot_member.guild_permissions.manage_roles:
        logger.warning(f"Founder role: Bot lacks manage_roles permission in founder guild {FOUNDER_GUILD_ID}")
        return

    member = guild.get_member(user_id)
    if not member:
        logger.warning(f"Founder role: Could not find member {user_id} in founder guild")
        return

    # Check if member already has founder role (avoid duplicate assignments)
    founder_role_name = getattr(config, 'FOUNDER_ROLE_NAME', 'Founder')
    founder_role = discord.utils.get(guild.roles, name=founder_role_name)
    if not founder_role:
        logger.warning(f"Founder role: '{founder_role_name}' role not found in founder guild {guild_id}")
        return

    # Check if bot can assign this role (role hierarchy)
    if founder_role.position >= bot_member.top_role.position:
        logger.warning(f"Founder role: Bot cannot assign '{founder_role_name}' role due to role hierarchy")
        return

    if founder_role in member.roles:
        logger.debug(f"Founder role: User {user_id} already has founder role")
        return

    # Assign the founder role with rate limiting consideration
    try:
        await member.add_roles(founder_role, reason="Early bird lifetime member - founder recognition")
        logger.info(f"Founder role: Assigned '{founder_role_name}' role to user {user_id} in founder guild")
    except discord.Forbidden:
        logger.error(f"Founder role: Forbidden - Bot lacks permission to assign role to user {user_id}")
    except discord.HTTPException as e:
        logger.error(f"Founder role: HTTP error assigning role to user {user_id}: {e}")
    except Exception as e:
        logger.error(f"Founder role: Unexpected error assigning role to user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Early bird availability check
# ---------------------------------------------------------------------------

_EARLY_BIRD_CACHE: bool | None = None
_EARLY_BIRD_CACHE_TS: float = 0.0
_EARLY_BIRD_CACHE_TTL: int = 300  # seconds


async def _check_early_bird_available() -> bool:
    """Return True if early bird spots are still available.

    Calls POST /billing/early-bird/validate on Core-API using a sentinel
    discord_user_id (1) — Discord's own account — which will never have
    redeemed early bird. Caches the result for 5 minutes to avoid hammering
    Core-API on every /premium invocation. Fails open (returns True) on any
    error so the embed never hides early bird access incorrectly.
    """
    global _EARLY_BIRD_CACHE, _EARLY_BIRD_CACHE_TS

    now = time.monotonic()
    if _EARLY_BIRD_CACHE is not None and (now - _EARLY_BIRD_CACHE_TS) < _EARLY_BIRD_CACHE_TTL:
        return _EARLY_BIRD_CACHE

    core_url = getattr(config, "CORE_API_URL", "") or ""
    payments_token = getattr(config, "CORE_API_PAYMENTS_TOKEN", "") or ""
    earlybird_code = getattr(config, "EARLY_BIRD_CODE", "EARLYBIRD50") or "EARLYBIRD50"

    if not core_url or not payments_token:
        logger.debug("Early bird check: CORE_API_URL or CORE_API_PAYMENTS_TOKEN not configured — assuming available")
        return True

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{core_url}/billing/early-bird/validate",
                json={"code": earlybird_code, "discord_user_id": 1},
                headers={"X-Payments-Token": payments_token},
            )
        if response.is_success:
            data = response.json()
            reason = data.get("reason", "")
            # Only mark unavailable when Core-API explicitly says the limit is hit.
            # "Already used" or any other reason means spots still remain.
            is_available = reason != "Early bird limit reached"
        else:
            logger.warning("Early bird check: Core-API returned %s", response.status_code)
            is_available = True  # fail open
    except Exception as exc:
        logger.warning("Early bird check: %s", exc)
        is_available = True  # fail open

    _EARLY_BIRD_CACHE = is_available
    _EARLY_BIRD_CACHE_TS = now
    logger.debug("Early bird available: %s (cached for %ss)", is_available, _EARLY_BIRD_CACHE_TTL)
    return is_available


async def _create_checkout_url(tier: str, guild_id: int, user_id: int) -> str | None:
    """Return a tier-specific checkout URL from PREMIUM_CHECKOUT_URL config.

    The pricing site handles Discord OAuth and payment itself — no server-side
    session creation is needed from the bot side.
    """
    base_url = getattr(config, "PREMIUM_CHECKOUT_URL", "") or ""
    if not base_url:
        logger.debug("Premium checkout: PREMIUM_CHECKOUT_URL not configured, buttons disabled")
        return None
    return base_url


async def _build_premium_embed_and_view(guild_id: int, user_id: int, guild_name: str | None = None) -> tuple[discord.Embed, discord.ui.View]:
    """Build the premium info embed and checkout buttons view. Reused after terms acceptance."""
    embed = discord.Embed(
        title="Premium",
        description="More Grok, image reminders, live sessions, spicy growth mode.",
        color=discord.Color.blue(),
        timestamp=datetime.now(BRUSSELS_TZ),
    )
    embed.add_field(
        name="✨ What you unlock",
        value=(
            "• Reminders with images and banners\n"
            "• Live session presets with image support\n"
            "• Mockingbird spicy mode in growth check-ins\n"
            "• Ticket GPT summaries (guild-level)\n"
            "• Unlimited daily Grok interactions\n"
            "• Unlimited active reminders"
        ),
        inline=False,
    )
    embed.add_field(
        name="📊 Tier comparison",
        value=(
            "```\n"
            f"{'Feature':<22} {'Free':>6} {'Monthly':>8} {'Yearly+':>8}\n"
            f"{'─' * 46}\n"
            f"{'Grok calls / day':<22} {'5':>6} {'25':>8} {'∞':>8}\n"
            f"{'Active reminders':<22} {'10':>6} {'∞':>8} {'∞':>8}\n"
            f"{'Image reminders':<22} {'✗':>6} {'✓':>8} {'✓':>8}\n"
            f"{'Ticket summaries':<22} {'✗':>6} {'✓':>8} {'✓':>8}\n"
            f"{'Spicy mode':<22} {'✗':>6} {'✓':>8} {'✓':>8}\n"
            "```"
        ),
        inline=False,
    )
    how_it_works = (
        "Premium applies to **one server**.\n"
        "Pay once → choose where you want full Mockingbird power, vision verification, and image reminders.\n"
        "Want to switch servers later? Use `/premium_transfer` in the server you want, "
        "or visit pricing.alphapy.innersync.tech."
    )
    if guild_id != 0 and guild_name:
        how_it_works += f"\n\n**This purchase will apply Premium to this server** ({guild_name})."
    elif guild_id != 0:
        how_it_works += "\n\n**This purchase will apply Premium to this server.**"
    else:
        how_it_works += "\n\n**You'll choose your server after payment via `/premium_transfer`.**"
    embed.add_field(
        name="📍 How it works",
        value=how_it_works,
        inline=False,
    )
    embed.set_footer(text=f"v{__version__} — {CODENAME}")

    early_bird = await _check_early_bird_available()
    total_spots = getattr(config, "EARLY_BIRD_TOTAL_SPOTS", 50)
    price_monthly = getattr(config, "PRICE_MONTHLY", "€4.99") or "€4.99"
    price_yearly_eb = getattr(config, "PRICE_YEARLY_EARLY_BIRD", "€29") or "€29"
    price_yearly_reg = getattr(config, "PRICE_YEARLY_REGULAR", "€59.99") or "€59.99"
    price_lifetime_eb = getattr(config, "PRICE_LIFETIME_EARLY_BIRD", "€49") or "€49"
    price_lifetime_reg = getattr(config, "PRICE_LIFETIME_REGULAR", "€99.99") or "€99.99"

    if early_bird:
        embed.add_field(
            name="🎉 Premium is Live!",
            value=(
                f"Choose your plan below. "
                f"🔥 Early bird pricing for the first {total_spots} founders — "
                f"Annual {price_yearly_eb} (reg. {price_yearly_reg}) · "
                f"Lifetime {price_lifetime_eb} (reg. {price_lifetime_reg})!"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="🎉 Premium is Live!",
            value="Choose your plan below.",
            inline=False,
        )

    checkout_urls = {}
    for tier in ["monthly", "yearly", "lifetime"]:
        checkout_urls[tier] = await _create_checkout_url(tier, guild_id, user_id)

    view = discord.ui.View(timeout=None)
    # When early bird is active, show early bird price on yearly and lifetime buttons.
    # When sold out, fall back to regular prices (pricing site is authoritative).
    tier_info: list[tuple[str, str, str]] = [
        ("monthly", "Monthly", price_monthly),
        ("yearly", "Annual", f"Early Bird {price_yearly_eb}" if early_bird else price_yearly_reg),
        ("lifetime", "Lifetime", f"Early Bird {price_lifetime_eb}" if early_bird else price_lifetime_reg),
    ]
    for tier, label, price in tier_info:
        button_label = f"Get {label} ({price})"
        url = checkout_urls.get(tier)
        if url:
            view.add_item(
                discord.ui.Button(
                    label=button_label,
                    url=url,
                    style=discord.ButtonStyle.link,
                )
            )
        else:
            view.add_item(
                discord.ui.Button(
                    label=button_label,
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
            )
    return embed, view


class PremiumCog(commands.Cog):
    """Premium tier UX: pricing embed and checkout."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: asyncpg.Pool | None = None
        self._db_manager = DatabaseManager("premium", bot=bot)
        self.bot.loop.create_task(self._connect_database())

    async def _connect_database(self) -> None:
        """Bind to the shared bot database pool for premium features."""
        try:
            self.db = await self._db_manager.ensure_pool()
            logger.info("Premium cog: Database pool created")
            self.check_expiry_warnings.start()
        except Exception as e:
            logger.error(f"Premium cog: Failed to create database pool: {e}")
            return

    async def cog_unload(self) -> None:
        self.check_expiry_warnings.cancel()

    async def _get_gpt_calls_today(self, user_id: int, guild_id: int) -> int:
        """Return how many GPT calls this user has made today in this guild."""
        if not self.db:
            return 0
        try:
            from utils.db_helpers import acquire_safe
            async with acquire_safe(self.db) as conn:
                return await conn.fetchval(
                    "SELECT call_count FROM gpt_usage WHERE user_id=$1 AND guild_id=$2 AND usage_date=CURRENT_DATE",
                    user_id, guild_id,
                ) or 0
        except Exception:
            return 0

    async def _get_reminder_count(self, user_id: int, guild_id: int) -> int:
        """Return the number of active reminders this user has in this guild."""
        if not self.db:
            return 0
        try:
            from utils.db_helpers import acquire_safe
            async with acquire_safe(self.db) as conn:
                return await conn.fetchval(
                    "SELECT COUNT(*) FROM reminders WHERE created_by=$1 AND guild_id=$2",
                    user_id, guild_id,
                ) or 0
        except Exception:
            return 0

    @tasks.loop(hours=24)
    async def check_expiry_warnings(self) -> None:
        """Send a DM to users whose premium expires within 7 days (once per subscription)."""
        if not self.db:
            return
        try:
            from utils.db_helpers import acquire_safe
            async with acquire_safe(self.db) as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id, guild_id, tier, expires_at
                    FROM premium_subs
                    WHERE status = 'active'
                      AND expires_at BETWEEN NOW() AND NOW() + INTERVAL '7 days'
                      AND expiry_warning_sent_at IS NULL
                    """
                )
            for row in rows:
                await self._send_expiry_warning_dm(row["user_id"], row["expires_at"])
                async with acquire_safe(self.db) as conn:
                    await conn.execute(
                        "UPDATE premium_subs SET expiry_warning_sent_at = NOW() "
                        "WHERE user_id = $1 AND guild_id = $2 AND status = 'active'",
                        row["user_id"], row["guild_id"],
                    )
        except Exception as e:
            logger.error("Premium expiry warning task failed: %s", e)

    @check_expiry_warnings.before_loop
    async def before_expiry_warnings(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_expiry_warning_dm(self, user_id: int, expires_at) -> None:
        """Send a DM warning that premium expires soon."""
        try:
            user = await self.bot.fetch_user(user_id)
            if user is None:
                return
            try:
                exp_str = expires_at.astimezone(BRUSSELS_TZ).strftime("%d %B %Y")
            except Exception:
                exp_str = str(expires_at)
            await user.send(
                f"⚠️ Your Premium subscription expires on **{exp_str}**.\n"
                "Renew via `/premium` to keep your features."
            )
            logger.info("Premium expiry warning sent to user %s (expires %s)", user_id, exp_str)
        except Exception as e:
            logger.warning("Could not send expiry warning DM to user %s: %s", user_id, e)

    @app_commands.command(
        name="premium",
        description="See plans, limits, and checkout.",
    )
    async def premium(self, interaction: discord.Interaction) -> None:
        # Check if user has accepted terms
        if not await _has_accepted_terms(self, interaction.user.id):
            # Show terms acceptance embed with buttons
            embed = discord.Embed(
                title="📋 Terms & Privacy Policy",
                description=(
                    "To access premium features, you must accept our Terms of Service and Privacy Policy.\n\n"
                    "📄 [Terms of Service](https://docs.alphapy.innersync.tech/terms-of-service/)\n"
                    "🔒 [Privacy Policy](https://docs.alphapy.innersync.tech/privacy-policy/)\n\n"
                    "Please review these documents and click Accept below."
                ),
                color=discord.Color.blue()
            )
            view = TermsAcceptanceView(interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild.id if interaction.guild else 0
        guild_name = interaction.guild.name if interaction.guild else None
        embed, view = await _build_premium_embed_and_view(
            guild_id, interaction.user.id, guild_name
        )
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="premium_check",
        description="(Admin) Check if a user has premium in this guild.",
    )
    @app_commands.describe(user="User to check (default: yourself)")
    async def premium_check(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await validate_admin(interaction, raise_on_fail=True)
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return
        target = user or interaction.user
        premium = await is_premium(target.id, interaction.guild.id)
        await interaction.response.send_message(
            f"**Premium:** `{str(premium).lower()}` for {target.mention} in this guild.",
            ephemeral=True,
        )

    @app_commands.command(
        name="my_premium",
        description="Check your Premium status and expiry in this server.",
    )
    async def my_premium(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        guild_id = interaction.guild.id

        status = await get_premium_status(user_id, guild_id)
        tier = await get_user_tier(user_id, guild_id)

        embed = discord.Embed(
            title="⚡ Your Premium Status",
            color=discord.Color.gold() if status["premium"] else discord.Color.greyple(),
            timestamp=datetime.now(BRUSSELS_TZ),
        )

        if status["premium"]:
            active_guild = await get_active_premium_guild(user_id)
            if active_guild is not None and active_guild != 0 and active_guild != guild_id:
                embed.description = "Your Premium is active in another server. Use `/premium_transfer` here to move it."
                embed.color = discord.Color.orange()
            else:
                tier_display = tier.capitalize()
                expires = status.get("expires_at")
                if expires:
                    try:
                        exp_str = expires.astimezone(BRUSSELS_TZ).strftime("%d %B %Y")
                        days_left = (expires.replace(tzinfo=UTC) - datetime.now(UTC)).days
                        expiry_value = f"{exp_str} ({days_left}d remaining)"
                    except Exception:
                        expiry_value = str(expires)
                else:
                    expiry_value = "Never (lifetime)"

                embed.description = f"✅ **{tier_display}** — active in this server"
                embed.add_field(name="Expires", value=expiry_value, inline=True)
                embed.add_field(name="Tier", value=tier_display, inline=True)
                embed.add_field(name="\u200b", value="\u200b", inline=True)

                # GPT quota
                gpt_limit = GPT_DAILY_LIMIT.get(tier)
                gpt_label = "∞" if gpt_limit is None else str(gpt_limit)
                gpt_used = await self._get_gpt_calls_today(user_id, guild_id)
                embed.add_field(
                    name="Grok calls today",
                    value=f"{gpt_used} / {gpt_label}",
                    inline=True,
                )

                # Reminder count
                reminder_limit = REMINDER_LIMIT.get(tier)
                reminder_label = "∞" if reminder_limit is None else str(reminder_limit)
                reminder_count = await self._get_reminder_count(user_id, guild_id)
                embed.add_field(
                    name="Active reminders",
                    value=f"{reminder_count} / {reminder_label}",
                    inline=True,
                )
        else:
            current_guild = await get_active_premium_guild(user_id)
            if current_guild is not None and current_guild != 0 and current_guild != guild_id:
                embed.description = "Your Premium is active in another server. Use `/premium_transfer` here to move it."
                embed.color = discord.Color.orange()
            else:
                embed.description = "You don't have Premium in this server. Get power with `/premium`."

            # Show free tier limits for context
            embed.add_field(
                name="Your current limits (Free)",
                value=(
                    f"Grok calls / day: **{GPT_DAILY_LIMIT.get('free')}**\n"
                    f"Active reminders: **{REMINDER_LIMIT.get('free')}**"
                ),
                inline=False,
            )

        embed.set_footer(text="❓ Questions? support@innersync.tech")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="premium_transfer",
        description="Move your Premium to this server (one subscription per account).",
    )
    async def premium_transfer(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        guild_id = interaction.guild.id
        current_guild = await get_active_premium_guild(user_id)
        if current_guild is None:
            await interaction.followup.send(
                "You don't have an active Premium subscription to transfer. Get power with /premium.",
                ephemeral=True,
            )
            return
        if current_guild == guild_id:
            await interaction.followup.send(
                "Your Premium is already active in this server.",
                ephemeral=True,
            )
            return
        ok, reason = await transfer_premium_to_guild(user_id, guild_id)
        if ok:
            # Check for founder role assignment if transferring to founder guild
            try:
                premium_status = await get_premium_status(user_id, guild_id)
                if premium_status.get("premium") and premium_status.get("tier") == "lifetime":
                    await _assign_founder_role_if_eligible(self, user_id, guild_id, "lifetime")
            except Exception as e:
                logger.debug(f"Founder role check failed during transfer: {e}")

            await interaction.followup.send(
                "Premium is now active in this server.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Transfer failed: {reason}. Try again or contact [support@innersync.tech](mailto:support@innersync.tech)",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PremiumCog(bot))
