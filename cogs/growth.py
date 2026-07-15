import asyncio
import logging
import math
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.app_commands import checks as app_checks
from discord.ext import commands

from gpt.errors import GrokUnavailableError, grok_user_message
from gpt.helpers import ask_gpt, log_gpt_error
from utils.db_helpers import acquire_safe, get_bot_db_pool
from utils.hermit_events import emit_hermit_event
from utils.innersync_identity import get_innersync_id_for_discord
from utils.sanitizer import safe_embed_text
from utils.supabase_client import (
    SupabaseConfigurationError,
    _supabase_get,
    insert_reflection_for_discord,
)

logger = logging.getLogger(__name__)

_GROWTH_EMBED_COLOR = 0x57F287  # green


def _build_growth_embed(
    user: discord.Member | discord.User | None,
    goal: str,
    obstacle: str,
    feeling: str,
    reply: str,
) -> discord.Embed:
    """Build the public growth check-in embed for the growth channel."""
    embed = discord.Embed(
        title="🌱 Growth Check-in",
        color=_GROWTH_EMBED_COLOR,
        timestamp=datetime.now(UTC),
    )
    if user:
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    else:
        embed.set_author(name="Anonymous community member")

    embed.add_field(name="Goal", value=safe_embed_text(goal[:1024]), inline=False)
    embed.add_field(name="Obstacle", value=safe_embed_text(obstacle[:1024]), inline=False)
    embed.add_field(name="How I feel", value=safe_embed_text(feeling[:1024]), inline=False)

    grok_text = reply[:1021] + "…" if len(reply) > 1024 else reply
    embed.add_field(name="Grok's reflection", value=safe_embed_text(grok_text), inline=False)

    embed.set_footer(text="Innersync • Growth")
    return embed


class GrowthShareView(discord.ui.View):
    """Ephemeral view shown after a check-in, letting the user share to the growth channel."""

    def __init__(
        self,
        goal: str,
        obstacle: str,
        feeling: str,
        reply: str,
        growth_channel: discord.TextChannel,
    ):
        super().__init__(timeout=300)
        self.goal = goal
        self.obstacle = obstacle
        self.feeling = feeling
        self.reply = reply
        self.growth_channel = growth_channel

    @discord.ui.button(label="Share anonymously", style=discord.ButtonStyle.secondary, emoji="🌱")
    async def share_anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_growth_embed(None, self.goal, self.obstacle, self.feeling, self.reply)
        try:
            await self.growth_channel.send(embed=embed)
            await interaction.response.edit_message(
                content="✅ Shared anonymously to the growth channel.", view=None
            )
        except discord.Forbidden:
            await interaction.response.edit_message(
                content="❌ I can't post in the growth channel. Ask an admin to check my permissions.", view=None
            )
        self.stop()

    @discord.ui.button(label="Share with my name", style=discord.ButtonStyle.primary, emoji="🌿")
    async def share_named(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_growth_embed(interaction.user, self.goal, self.obstacle, self.feeling, self.reply)
        try:
            await self.growth_channel.send(embed=embed)
            await interaction.response.edit_message(
                content=f"✅ Shared to {self.growth_channel.mention}!", view=None
            )
        except discord.Forbidden:
            await interaction.response.edit_message(
                content="❌ I can't post in the growth channel. Ask an admin to check my permissions.", view=None
            )
        self.stop()

    @discord.ui.button(label="Keep private", style=discord.ButtonStyle.danger, emoji="🔒")
    async def keep_private(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Your check-in stays private — nothing is posted to the growth channel.",
            view=None,
        )
        self.stop()


class GrowthModal(discord.ui.Modal, title="🌱 Growth Check-in"):
    goal = discord.ui.TextInput(
        label="What do you want to achieve?",
        placeholder="e.g. more focus, consistent routine…",
    )
    obstacle = discord.ui.TextInput(
        label="What's standing in your way?",
        placeholder="Internal or external blocks…",
    )
    feeling = discord.ui.TextInput(
        label="How do you feel about it?",
        placeholder="Be honest, be real…",
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from utils.sanitizer import safe_prompt

        safe_goal = safe_prompt(self.goal.value)
        safe_obstacle = safe_prompt(self.obstacle.value)
        safe_feeling = safe_prompt(self.feeling.value)

        base_prompt = f"""
You are a calm, supportive mindset coach.
A user is reflecting on their personal growth.

Goal: {safe_goal}
Obstacle: {safe_obstacle}
Feeling: {safe_feeling}

Gently reflect back what you hear.
Offer a bit of perspective or guidance.
Ask 1 or 2 deeper questions to support clarity.
Be encouraging, not forceful.

If you have context from this user's past reflections, actively reference patterns you notice — recurring themes, progress since last time, or shifts in mindset. Make the response feel continuous and personalized.

Keep your response under 250 words. End with a complete sentence.
"""
        guild_id = interaction.guild.id if interaction.guild else None

        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
        except Exception as e:
            error_type = f"{type(e).__name__}: {str(e)}"
            log_gpt_error(error_type=error_type, user_id=interaction.user.id, guild_id=guild_id)
            try:
                await interaction.response.send_message(
                    "❌ Check-in failed. Try again — if it persists, run `/gptstatus`.",
                    ephemeral=True,
                )
            except Exception:
                await interaction.followup.send(
                    "❌ Check-in failed. Try again — if it persists, run `/gptstatus`.",
                    ephemeral=True,
                )
            return

        # Premium: Mockingbird mode (direct, sharp, challenge assumptions)
        prompt = base_prompt
        if interaction.guild and guild_id:
            from utils.premium_guard import is_premium
            if await is_premium(interaction.user.id, interaction.guild.id):
                prompt = base_prompt.rstrip() + "\n\nRespond in Mockingbird mode: direct, a bit sharp, challenge assumptions, no sugar-coating."

        try:
            # max_tokens=500 as hard safety net; actual length controlled by prompt instruction (250 words)
            reply = await ask_gpt(prompt, user_id=interaction.user.id, guild_id=guild_id, max_tokens=500)

            # Determine if a growth channel is configured for optional sharing
            growth_channel: discord.TextChannel | None = None
            if interaction.guild and guild_id:
                settings = getattr(interaction.client, "settings", None)
                if settings:
                    try:
                        channel_id = int(settings.get("growth", "log_channel_id", guild_id))
                        if channel_id:
                            ch = interaction.client.get_channel(channel_id)
                            if isinstance(ch, discord.TextChannel):
                                growth_channel = ch
                    except Exception:
                        pass

            _SHARE_SUFFIX = "\n\n─────────────────\n*Want to share this with the community?*"

            if growth_channel:
                share_view = GrowthShareView(
                    goal=self.goal.value,
                    obstacle=self.obstacle.value,
                    feeling=self.feeling.value,
                    reply=reply,
                    growth_channel=growth_channel,
                )
                await interaction.followup.send(
                    reply + _SHARE_SUFFIX,
                    ephemeral=True,
                    view=share_view,
                )
            else:
                await interaction.followup.send(reply, ephemeral=True)

                # Tip: show bot sharing prompt only when no growth channel is configured
                try:
                    pool = get_bot_db_pool(interaction.client)
                    user_id = await get_innersync_id_for_discord(
                        pool, interaction.user.id, allow_profile_fallback=False
                    )
                    if user_id:
                        profile_rows = await _supabase_get(
                            "profiles",
                            {
                                "select": "bot_sharing_enabled",
                                "user_id": f"eq.{user_id}",
                                "limit": 1,
                            },
                        )
                        bot_sharing_enabled = profile_rows[0].get("bot_sharing_enabled", False) if profile_rows else False
                        if not bot_sharing_enabled:
                            tip_message = (
                                "💡 **Tip:** Enable bot sharing in your Innersync App settings to get "
                                "more personalized responses based on your reflection history!"
                            )
                            await interaction.followup.send(tip_message, ephemeral=True)
                except Exception as tip_error:
                    logger.debug("Failed to check bot sharing status (non-critical): %s", tip_error)

            asyncio.create_task(
                emit_hermit_event(
                    "gpt_command",
                    interaction.user.id,
                    guild_id=guild_id,
                    payload={"command": "growthcheckin"},
                )
            )

            async def _store_reflection() -> None:
                reflection_text = (
                    f"Goal: {self.goal.value}\n"
                    f"Obstacle: {self.obstacle.value}\n"
                    f"Feeling: {self.feeling.value}"
                )
                try:
                    success = await insert_reflection_for_discord(
                        interaction.user.id,
                        reflection=reflection_text,
                        future_message=reply,
                        date=datetime.now(UTC),
                    )
                    if not success:
                        logger.debug(
                            "Skipping Supabase reflection sync: no profile linked to discord_id=%s",
                            interaction.user.id,
                        )
                except SupabaseConfigurationError:
                    logger.debug("Supabase credentials missing; skipping reflection sync.")
                except Exception as exc:  # pragma: no cover - network path
                    logger.warning(
                        "Failed to sync reflection to Supabase for discord_id=%s: %s",
                        interaction.user.id,
                        exc,
                    )

            asyncio.create_task(_store_reflection())

            async def _log_checkin_to_railway() -> None:
                try:
                    pool = get_bot_db_pool(interaction.client)
                    if not pool:
                        return
                    async with acquire_safe(pool) as conn:
                        await conn.execute(
                            """
                            INSERT INTO growth_checkins (
                                guild_id, user_id, shared,
                                goal, obstacle, feeling, grok_response
                            )
                            VALUES ($1, $2, FALSE, $3, $4, $5, $6)
                            """,
                            guild_id or 0,
                            interaction.user.id,
                            self.goal.value,
                            self.obstacle.value,
                            self.feeling.value,
                            reply,
                        )
                except Exception as exc:
                    logger.warning("Failed to log growth check-in to Railway: %s", exc)

            asyncio.create_task(_log_checkin_to_railway())

            if guild_id:
                from utils.fyi_tips import send_fyi_if_first

                await send_fyi_if_first(interaction.client, guild_id, "first_growthcheckin")
        except GrokUnavailableError as e:
            await interaction.followup.send(grok_user_message(e), ephemeral=True)
        except Exception:
            await interaction.followup.send(
                "❌ Check-in failed. Try again — if it persists, run `/gptstatus`.",
                ephemeral=True,
            )


_HISTORY_PER_PAGE = 3
_HISTORY_MAX_FETCH = 15  # 5 pages max


def _checkin_date_str(row: dict) -> str:
    """Format a growth_checkins row date for display labels."""
    created = row.get("created_at")
    if created is None:
        return ""
    if hasattr(created, "date"):
        return created.date().isoformat()
    return str(created)[:10]


def _checkin_field(row: dict, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else (str(value) if value else "")


class GrowthDeleteConfirmView(discord.ui.View):
    """Confirmation step before deleting a check-in."""

    def __init__(
        self,
        row: dict,
        discord_user_id: int,
        back_view: "GrowthHistoryView",
        detail_view: "GrowthDetailView",
    ):
        super().__init__(timeout=60)
        self.row = row
        self.discord_user_id = discord_user_id
        self.back_view = back_view
        self.detail_view = detail_view

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        row_id = self.row.get("id")
        if not row_id:
            await interaction.response.edit_message(
                content="❌ Could not identify the check-in to delete.", view=None
            )
            return
        try:
            pool = get_bot_db_pool(interaction.client)
            if not pool:
                raise RuntimeError("Database pool unavailable")
            async with acquire_safe(pool) as conn:
                result = await conn.execute(
                    """
                    DELETE FROM growth_checkins
                    WHERE id = $1 AND user_id = $2
                    """,
                    int(row_id),
                    self.discord_user_id,
                )
            deleted_count = int(str(result).split()[-1])
            if deleted_count == 0:
                await interaction.response.edit_message(
                    content="❌ Could not delete the check-in. Please try again later.",
                    view=self.detail_view,
                )
                return
            checkins = self.back_view.checkins
            self.back_view.checkins = [r for r in checkins if r.get("id") != row_id]
            if self.back_view.page >= self.back_view.total_pages:
                self.back_view.page = max(0, self.back_view.total_pages - 1)
            self.back_view._rebuild_items()
            await interaction.response.edit_message(
                content="✅ Check-in deleted.",
                embed=self.back_view.build_embed(),
                view=self.back_view,
            )
        except Exception as e:
            logger.warning("Failed to delete growth_checkins id=%s: %s", row_id, e)
            await interaction.response.edit_message(
                content="❌ Could not delete the check-in. Please try again later.",
                view=self.detail_view,
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None, embed=self.detail_view.build_embed(), view=self.detail_view
        )
        self.stop()


class GrowthDetailView(discord.ui.View):
    """Full detail view for one check-in, including Grok response."""

    def __init__(
        self,
        row: dict,
        back_view: "GrowthHistoryView",
        discord_user_id: int,
    ):
        super().__init__(timeout=300)
        self.row = row
        self.back_view = back_view
        self.discord_user_id = discord_user_id

    def build_embed(self) -> discord.Embed:
        date_str = _checkin_date_str(self.row)
        goal = _checkin_field(self.row, "goal")
        obstacle = _checkin_field(self.row, "obstacle")
        feeling = _checkin_field(self.row, "feeling")
        grok_response = _checkin_field(self.row, "grok_response")

        embed = discord.Embed(
            title=f"🌱 Growth Check-in — {date_str}",
            color=_GROWTH_EMBED_COLOR,
            timestamp=datetime.now(UTC),
        )
        if goal:
            embed.add_field(name="Goal", value=safe_embed_text(goal[:1024]), inline=False)
        if obstacle:
            embed.add_field(name="Obstacle", value=safe_embed_text(obstacle[:1024]), inline=False)
        if feeling:
            embed.add_field(name="How I felt", value=safe_embed_text(feeling[:1024]), inline=False)
        if grok_response:
            grok_text = grok_response[:1021] + "…" if len(grok_response) > 1024 else grok_response
            embed.add_field(name="🤖 Grok's reflection", value=safe_embed_text(grok_text), inline=False)
        embed.set_footer(text="Innersync • Growth")
        return embed

    @discord.ui.button(label="← Back to list", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.back_view._rebuild_items()
        await interaction.response.edit_message(
            content=None,
            embed=self.back_view.build_embed(),
            view=self.back_view,
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        date_str = _checkin_date_str(self.row)
        confirm_view = GrowthDeleteConfirmView(
            row=self.row,
            discord_user_id=self.discord_user_id,
            back_view=self.back_view,
            detail_view=self,
        )
        await interaction.response.edit_message(
            content=f"⚠️ Delete the check-in from **{date_str}**? This cannot be undone.",
            embed=None,
            view=confirm_view,
        )


class GrowthHistoryView(discord.ui.View):
    """Paginated list of growth check-ins (3 per page) with a Select for detail."""

    def __init__(self, checkins: list, page: int = 0, discord_user_id: int = 0):
        super().__init__(timeout=300)
        self.checkins = checkins
        self.page = page
        self.discord_user_id = discord_user_id
        self._rebuild_items()

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.checkins) / _HISTORY_PER_PAGE))

    @property
    def page_items(self) -> list:
        start = self.page * _HISTORY_PER_PAGE
        return self.checkins[start:start + _HISTORY_PER_PAGE]

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🌱 Growth History",
            color=_GROWTH_EMBED_COLOR,
            timestamp=datetime.now(UTC),
        )
        if not self.checkins:
            embed.description = "No check-ins found yet. Use `/growthcheckin` to get started."
        else:
            for row in self.page_items:
                date_str = _checkin_date_str(row)
                goal = _checkin_field(row, "goal")
                obstacle = _checkin_field(row, "obstacle")
                preview_parts = []
                if goal:
                    preview_parts.append(f"**Goal:** {goal[:120]}")
                if obstacle:
                    preview_parts.append(f"**Obstacle:** {obstacle[:120]}")
                embed.add_field(
                    name=f"📅 {date_str}",
                    value=safe_embed_text("\n".join(preview_parts) or "—"),
                    inline=False,
                )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • Innersync • Growth")
        return embed

    def _rebuild_items(self):
        self.clear_items()
        items = self.page_items
        if items:
            options = []
            for i, row in enumerate(items):
                date_str = _checkin_date_str(row)
                goal = _checkin_field(row, "goal")
                desc = goal[:97] + "…" if len(goal) > 97 else goal
                options.append(discord.SelectOption(
                    label=date_str or f"Check-in {self.page * _HISTORY_PER_PAGE + i + 1}",
                    value=str(self.page * _HISTORY_PER_PAGE + i),
                    description=desc or "View full detail",
                ))
            select = discord.ui.Select(
                placeholder="Open a check-in for full detail + Grok response…",
                options=options,
                row=0,
            )
            select.callback = self._on_select
            self.add_item(select)

        prev_btn = discord.ui.Button(
            label="← Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.page == 0,
            row=1,
        )
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Next →",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.total_pages - 1,
            row=1,
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _on_select(self, interaction: discord.Interaction):
        idx = int(interaction.data["values"][0])
        row = self.checkins[idx]
        detail_view = GrowthDetailView(
            row=row, back_view=self, discord_user_id=self.discord_user_id
        )
        await interaction.response.edit_message(
            content=None, embed=detail_view.build_embed(), view=detail_view
        )

    async def _on_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        self.page += 1
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class GrowthCheckin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="growthcheckin",
        description="Name your goal, blocker, and feeling. Grok responds.",
    )
    @app_checks.cooldown(2, 300.0, key=lambda i: (i.guild.id, i.user.id) if i.guild else i.user.id)
    async def growthcheckin(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GrowthModal())

    @app_commands.command(
        name="growthhistory",
        description="View your recent Growth Check-ins.",
    )
    @app_checks.cooldown(1, 30.0, key=lambda i: i.user.id)
    async def growthhistory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            pool = get_bot_db_pool(interaction.client)
            if not pool:
                await interaction.followup.send(
                    "❌ Database unavailable. Please try again later.",
                    ephemeral=True,
                )
                return

            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, goal, obstacle, feeling, grok_response, created_at
                    FROM growth_checkins
                    WHERE user_id = $1
                      AND (
                        goal IS NOT NULL
                        OR obstacle IS NOT NULL
                        OR feeling IS NOT NULL
                        OR grok_response IS NOT NULL
                      )
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    interaction.user.id,
                    _HISTORY_MAX_FETCH,
                )

            checkins = [dict(row) for row in rows]
            view = GrowthHistoryView(
                checkins=checkins,
                discord_user_id=interaction.user.id,
            )
            await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)
        except Exception as e:
            logger.warning(
                "Failed to load growth history for discord_id=%s: %s",
                interaction.user.id,
                e,
            )
            await interaction.followup.send(
                "❌ Could not load your growth history. Please try again later.",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(GrowthCheckin(bot))
