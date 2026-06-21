"""
Engagement Cog

Provides community engagement features for guilds:
  - /challenge  — message-count contests (leaderboard or random draw)
  - /weekly     — weekly award computation
  - /badge      — manual badge management
  - /og         — limited reaction-based OG claim system

Each feature is independently toggled per guild via:
  /engagement toggle challenges true|false
  /engagement toggle weekly      true|false
  /engagement toggle badges      true|false
  /engagement toggle streaks     true|false
  /engagement toggle og_claims   true|false

on_message: indexes messages for weekly awards, tracks challenge counts, updates streaks.
on_raw_reaction_add: increments reaction counts for weekly awards and handles OG claims.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.db_helpers import get_bot_db_pool
from utils.embed_builder import EmbedBuilder
from utils.sanitizer import safe_embed_text
from utils.engagement_service import (
    _active_challenges,
    _challenge_lock,
    # Badges
    add_badge,
    challenge_add_participant,
    challenge_cancel,
    challenge_create,
    challenge_get_participants_count,
    challenge_get_top,
    challenge_remove_participant,
    challenge_update_ends_at,
    challenge_update_mode,
    challenge_update_participant_count,
    challenge_update_title,
    compute_weekly_awards,
    ensure_streak_nickname,
    finalize_and_announce_challenge,
    format_duration,
    # Challenges
    get_guild_challenges,
    # Streaks
    get_streak,
    get_user_badges,
    # OG
    og_count_claims,
    og_get_setup,
    og_has_claim,
    og_insert_claim,
    og_set_setup,
    parse_duration_to_seconds,
    rehydrate_challenges,
    reschedule_challenge,
    schedule_challenge,
    set_streak,
    weekly_increment_reaction,
    # Weekly
    weekly_index_message,
)
from utils.logger import logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FEATURE_FLAG_TTL = 30.0
_FOOD_CHANNELS_TTL = 60.0
_feature_flag_cache: dict[tuple[int, str], tuple[bool, float]] = {}
_food_channels_cache: dict[int, tuple[set[int], float]] = {}
_cache_stats = {
    "feature_flag_hits": 0,
    "feature_flag_misses": 0,
    "food_channels_hits": 0,
    "food_channels_misses": 0,
}


def _invalidate_engagement_cache(scope: str, key: str, guild_id: int) -> None:
    """Drop local engagement cache entries when related settings change."""
    if scope != "engagement":
        return
    if key.endswith("_enabled"):
        feature = key[:-8]
        _feature_flag_cache.pop((guild_id, feature), None)
    if key == "weekly_food_channel_ids":
        _food_channels_cache.pop(guild_id, None)


def get_engagement_cache_stats() -> dict[str, int]:
    """Expose engagement cache metrics for dashboard/health endpoints."""
    return {
        "engagement_feature_flag_cache_size": len(_feature_flag_cache),
        "engagement_food_channels_cache_size": len(_food_channels_cache),
        "engagement_feature_flag_cache_hits": int(_cache_stats["feature_flag_hits"]),
        "engagement_feature_flag_cache_misses": int(_cache_stats["feature_flag_misses"]),
        "engagement_food_channels_cache_hits": int(_cache_stats["food_channels_hits"]),
        "engagement_food_channels_cache_misses": int(_cache_stats["food_channels_misses"]),
    }


async def _is_enabled(bot: commands.Bot, guild_id: int, feature: str) -> bool:
    """Return True if the given engagement feature is enabled for the guild."""
    now = time.monotonic()
    cache_key = (guild_id, feature)
    cached = _feature_flag_cache.get(cache_key)
    if cached and cached[1] > now:
        _cache_stats["feature_flag_hits"] += 1
        return cached[0]
    _cache_stats["feature_flag_misses"] += 1

    settings = getattr(bot, "settings", None)
    if not settings:
        _feature_flag_cache[cache_key] = (False, now + _FEATURE_FLAG_TTL)
        return False
    try:
        val = settings.get("engagement", f"{feature}_enabled", guild_id=guild_id)
        is_enabled = bool(val)
        _feature_flag_cache[cache_key] = (is_enabled, now + _FEATURE_FLAG_TTL)
        return is_enabled
    except Exception:
        _feature_flag_cache[cache_key] = (False, now + _FEATURE_FLAG_TTL)
        return False


async def _get_setting(
    bot: commands.Bot, guild_id: int, key: str, default: Any = None
) -> Any:
    settings = getattr(bot, "settings", None)
    if not settings:
        return default
    try:
        val = settings.get("engagement", key, guild_id=guild_id)
        return val if val is not None else default
    except Exception:
        return default


async def _get_award_configs(bot: commands.Bot, guild_id: int) -> list[dict[str, Any]]:
    """
    Load configured weekly award categories for a guild.
    Stored as JSON in engagement.weekly_award_configs.

    Falls back to a dynamic default set when not configured:
    - 'food' filter awards are only included when food channels are configured.
    - Custom JSON configs are returned as-is; the admin owns that setup.
    """
    raw = await _get_setting(bot, guild_id, "weekly_award_configs")
    if raw:
        try:
            configs = json.loads(raw)
            if isinstance(configs, list) and configs:
                return configs
        except Exception:
            pass

    # Build default award set — omit food filter if no food channels are configured
    food_channels = await _get_food_channel_ids(bot, guild_id)
    defaults = [
        {"key": "motivator", "label": "📣 Motivator",          "subtitle": "Most messages",           "filter": "non_food"},
        {"key": "sharpshooter", "label": "💥 Sharpshooter of the week", "subtitle": "Most messages with image", "filter": "image"},
        {"key": "star",      "label": "⭐ Star of the week",    "subtitle": "Most reactions on a photo","filter": "reactions"},
    ]
    if food_channels:
        defaults.insert(1, {
            "key": "foodfluencer",
            "label": "🍎 Foodfluencer",
            "subtitle": "Most food-channel messages",
            "filter": "food",
        })
    return defaults


async def _get_food_channel_ids(bot: commands.Bot, guild_id: int) -> set:
    """Load the set of channel IDs designated as food channels for a guild."""
    now = time.monotonic()
    cached = _food_channels_cache.get(guild_id)
    if cached and cached[1] > now:
        _cache_stats["food_channels_hits"] += 1
        return set(cached[0])
    _cache_stats["food_channels_misses"] += 1

    raw = await _get_setting(bot, guild_id, "weekly_food_channel_ids", "")
    ids: set[int] = set()
    if raw:
        for tok in str(raw).split(","):
            tok = tok.strip()
            if tok.isdigit():
                ids.add(int(tok))
    _food_channels_cache[guild_id] = (set(ids), now + _FOOD_CHANNELS_TTL)
    return ids


# ---------------------------------------------------------------------------
# /challenge group
# ---------------------------------------------------------------------------


class ChallengeGroup(app_commands.Group):
    """Message-count challenge commands."""

    def __init__(self, cog: EngagementCog):
        super().__init__(name="challenge", description="Challenge commands")
        self.cog = cog

    @app_commands.command(name="start", description="Start a new challenge")
    @app_commands.describe(
        duration="Duration e.g. 10d, 3h30m, 900 (seconds)",
        mode="leaderboard or random",
        title="Optional challenge title",
        channel="Channel to count messages in",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="leaderboard", value="leaderboard"),
        app_commands.Choice(name="random", value="random"),
    ])
    async def start(
        self,
        interaction: discord.Interaction,
        duration: str | None = None,
        mode: app_commands.Choice[str] | None = None,
        title: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "challenges"):
            await interaction.response.send_message(
                "Challenges are not enabled for this server. "
                "An admin can enable them via `/engagement toggle challenges true`.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not perms or not (perms.manage_guild or perms.administrator):
            await interaction.response.send_message("You need Manage Server to start a challenge.", ephemeral=True)
            return

        seconds = parse_duration_to_seconds(duration) or 86400
        selected_mode = (mode.value if mode else "leaderboard").lower()
        target_channel = channel or (
            interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        )
        if target_channel is None:
            await interaction.response.send_message("Could not determine the target channel.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=False, thinking=True)
        except Exception:
            pass

        pool = get_bot_db_pool(self.cog.bot)
        async with _challenge_lock:
            challenge_id = await challenge_create(
                pool, interaction.guild.id, target_channel.id, selected_mode, seconds, title
            )
            if challenge_id is None:
                await interaction.followup.send("Failed to create challenge in the database.", ephemeral=True)
                return
            await schedule_challenge(
                self.cog.bot, pool, challenge_id, interaction.guild.id,
                selected_mode, title, seconds, target_channel.id,
            )

        desc = (
            f"Challenge started for **{format_duration(seconds)}** "
            f"in {target_channel.mention} (mode: {selected_mode})."
        )
        if title:
            desc += f"\nTitle: {title}"
        await interaction.followup.send(desc)

    @app_commands.command(name="end", description="End the active challenge and announce the winner")
    @app_commands.describe(challenge_id="Specific challenge ID (optional)")
    async def end(
        self, interaction: discord.Interaction, challenge_id: int | None = None
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "challenges"):
            await interaction.response.send_message("Challenges are not enabled.", ephemeral=True)
            return

        guild_challenges = get_guild_challenges(interaction.guild.id)
        if not guild_challenges:
            await interaction.response.send_message("No active challenge.", ephemeral=True)
            return

        chal_id = (
            challenge_id
            if challenge_id and challenge_id in guild_challenges
            else max(guild_challenges.keys())
        )
        task = guild_challenges[chal_id].get("task")
        if task and not task.done():
            task.cancel()
        await finalize_and_announce_challenge(self.cog.bot, chal_id)
        await interaction.response.send_message("Challenge ended. Results posted.")

    @app_commands.command(name="cancel", description="Cancel the active challenge without a winner")
    @app_commands.describe(challenge_id="Specific challenge ID (optional)")
    async def cancel(
        self, interaction: discord.Interaction, challenge_id: int | None = None
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "challenges"):
            await interaction.response.send_message("Challenges are not enabled.", ephemeral=True)
            return

        guild_challenges = get_guild_challenges(interaction.guild.id)
        if not guild_challenges:
            await interaction.response.send_message("No active challenge.", ephemeral=True)
            return

        chal_id = (
            challenge_id
            if challenge_id and challenge_id in guild_challenges
            else max(guild_challenges.keys())
        )
        pool = get_bot_db_pool(self.cog.bot)
        await challenge_cancel(pool, chal_id)
        await interaction.response.send_message("Challenge cancelled.")

    @app_commands.command(name="status", description="Show remaining time and leaderboard")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "challenges"):
            await interaction.response.send_message("Challenges are not enabled.", ephemeral=True)
            return

        guild_challenges = get_guild_challenges(interaction.guild.id)
        if not guild_challenges:
            await interaction.response.send_message("No active challenge.", ephemeral=True)
            return

        import time as _time
        pool = get_bot_db_pool(self.cog.bot)
        embeds: list[discord.Embed] = []
        for chal_id, rt in guild_challenges.items():
            mode = rt.get("mode", "leaderboard")
            title = rt.get("title")
            end_ts = rt.get("end_ts")
            start_ts = rt.get("start_ts")
            channel_id = rt.get("channel_id", 0)

            participants_count = await challenge_get_participants_count(pool, chal_id)
            end_dt_str = f"<t:{int(end_ts)}:F> (<t:{int(end_ts)}:R>)" if end_ts else ""

            progress_bar = ""
            if start_ts and end_ts:
                total = max(1, int(end_ts - start_ts))
                elapsed = max(0, int(_time.time() - start_ts))
                pct = max(0.0, min(1.0, elapsed / total))
                filled = int(round(pct * 20))
                progress_bar = "█" * filled + "░" * (20 - filled)

            embed = EmbedBuilder.info(
                title=f"📊 Challenge Status — #{chal_id}",
                description=f"Status: Active\nMode: {mode}\nParticipants: {participants_count}",
            )
            if title:
                embed.add_field(name="Title", value=safe_embed_text(title, 1024), inline=False)
            embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=False)
            if end_dt_str:
                embed.add_field(name="End time", value=end_dt_str, inline=False)
            if progress_bar:
                embed.add_field(name="Progress", value=f"`{progress_bar}`", inline=False)
            if mode == "leaderboard":
                top = await challenge_get_top(pool, chal_id, 5)
                lines = []
                for rank, (uid, cnt) in enumerate(top, 1):
                    member = interaction.guild.get_member(uid) if interaction.guild else None
                    name = member.mention if member else f"<@{uid}>"
                    lines.append(f"{rank}. {name} — {cnt}")
                embed.add_field(
                    name="Top 5",
                    value="\n".join(lines) if lines else "No entries yet.",
                    inline=False,
                )
            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    @app_commands.command(name="edit", description="Edit an active challenge")
    @app_commands.describe(
        field="What to change",
        mode="New mode",
        duration="New duration",
        member="Member to add/remove or set count for",
        set_count="New message count (leaderboard only)",
        title="New title",
        challenge_id="Specific challenge ID (optional)",
    )
    @app_commands.choices(field=[
        app_commands.Choice(name="mode",              value="mode"),
        app_commands.Choice(name="duration",          value="duration"),
        app_commands.Choice(name="title",             value="title"),
        app_commands.Choice(name="add_participant",   value="add_participant"),
        app_commands.Choice(name="remove_participant",value="remove_participant"),
        app_commands.Choice(name="set_count",         value="set_count"),
    ])
    @app_commands.choices(mode=[
        app_commands.Choice(name="leaderboard", value="leaderboard"),
        app_commands.Choice(name="random",      value="random"),
    ])
    async def edit(
        self,
        interaction: discord.Interaction,
        field: app_commands.Choice[str],
        mode: app_commands.Choice[str] | None = None,
        duration: str | None = None,
        member: discord.Member | None = None,
        set_count: int | None = None,
        title: str | None = None,
        challenge_id: int | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "challenges"):
            await interaction.response.send_message("Challenges are not enabled.", ephemeral=True)
            return

        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not perms or not (perms.manage_guild or perms.administrator):
            await interaction.response.send_message("You need Manage Server to edit a challenge.", ephemeral=True)
            return

        guild_challenges = get_guild_challenges(interaction.guild.id)
        if not guild_challenges:
            await interaction.response.send_message("No active challenge.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        pool = get_bot_db_pool(self.cog.bot)
        chal_id = (
            challenge_id
            if challenge_id and challenge_id in guild_challenges
            else max(guild_challenges.keys())
        )
        choice = field.value

        async with _challenge_lock:
            if choice == "mode":
                if mode is None:
                    await interaction.followup.send("Provide a mode value.", ephemeral=True)
                    return
                await challenge_update_mode(pool, chal_id, mode.value)
                _active_challenges[chal_id]["mode"] = mode.value
                await interaction.followup.send(f"Mode changed to: {mode.value}", ephemeral=True)

            elif choice == "duration":
                secs = parse_duration_to_seconds(duration)
                if not secs:
                    await interaction.followup.send("Invalid duration.", ephemeral=True)
                    return
                await challenge_update_ends_at(pool, chal_id, secs)
                await reschedule_challenge(self.cog.bot, pool, chal_id, secs)
                await interaction.followup.send(
                    f"Duration updated. New remaining: {format_duration(secs)}", ephemeral=True
                )

            elif choice == "title":
                if not title or not title.strip():
                    await interaction.followup.send("Provide a title.", ephemeral=True)
                    return
                new_title = title.strip()
                _active_challenges[chal_id]["title"] = new_title
                await challenge_update_title(pool, chal_id, new_title)
                await interaction.followup.send(f"Title changed to: {new_title}", ephemeral=True)

            elif choice == "add_participant":
                if member is None:
                    await interaction.followup.send("Provide a member.", ephemeral=True)
                    return
                chal_mode = _active_challenges[chal_id]["mode"]
                counts = _active_challenges[chal_id]["message_counts"]
                if chal_mode == "leaderboard":
                    await challenge_add_participant(pool, chal_id, member.id, increment=True)
                    counts[member.id] = counts.get(member.id, 0) + 1
                else:
                    await challenge_add_participant(pool, chal_id, member.id, increment=False)
                    counts.setdefault(member.id, 1)
                await interaction.followup.send(f"Added: {member.mention}", ephemeral=True)

            elif choice == "remove_participant":
                if member is None:
                    await interaction.followup.send("Provide a member.", ephemeral=True)
                    return
                await challenge_remove_participant(pool, chal_id, member.id)
                _active_challenges[chal_id]["message_counts"].pop(member.id, None)
                await interaction.followup.send(f"Removed: {member.mention}", ephemeral=True)

            elif choice == "set_count":
                if _active_challenges[chal_id]["mode"] != "leaderboard":
                    await interaction.followup.send("set_count is leaderboard-only.", ephemeral=True)
                    return
                if member is None or set_count is None or set_count < 0:
                    await interaction.followup.send("Provide member and a non-negative count.", ephemeral=True)
                    return
                await challenge_update_participant_count(pool, chal_id, member.id, set_count)
                _active_challenges[chal_id]["message_counts"][member.id] = set_count
                await interaction.followup.send(
                    f"Count for {member.mention} set to {set_count}.", ephemeral=True
                )
            else:
                await interaction.followup.send("Unknown field.", ephemeral=True)

    # Autocomplete helpers
    @start.autocomplete("duration")
    @edit.autocomplete("duration")
    async def duration_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        presets = [
            ("15m", "900"), ("30m", "1800"), ("1h", "3600"),
            ("3h", "10800"), ("6h", "21600"), ("1d", "86400"),
            ("3d", "259200"), ("7d", "604800"), ("10d", "864000"),
        ]
        q = current.lower().strip()
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in presets
            if not q or q in label
        ][:25]

    @end.autocomplete("challenge_id")
    @cancel.autocomplete("challenge_id")
    @edit.autocomplete("challenge_id")
    async def challenge_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild:
            return []
        gc = get_guild_challenges(interaction.guild.id)
        q = current.strip()
        return [
            app_commands.Choice(name=f"#{cid}", value=cid)
            for cid in sorted(gc.keys())
            if not q or q in str(cid)
        ][:25]


# ---------------------------------------------------------------------------
# /badge group
# ---------------------------------------------------------------------------


class BadgeGroup(app_commands.Group):
    """Badge management commands."""

    def __init__(self, cog: EngagementCog):
        super().__init__(name="badge", description="Badge commands")
        self.cog = cog

    @app_commands.command(name="give", description="Grant a badge (and optional role) to a member")
    @app_commands.describe(member="Member to award", badge_key="Badge key e.g. winner, og, motivator")
    async def give(
        self, interaction: discord.Interaction, member: discord.Member, badge_key: str
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "badges"):
            await interaction.response.send_message(
                "Badges are not enabled. Enable via `/engagement toggle badges true`.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not perms or not (perms.manage_roles or perms.administrator):
            await interaction.response.send_message("You need Manage Roles to give badges.", ephemeral=True)
            return

        key = badge_key.lower().strip()
        pool = get_bot_db_pool(self.cog.bot)

        # Assign configured role if any
        role_id = await _get_setting(self.cog.bot, interaction.guild.id, f"badge_role_{key}")
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason=f"Badge: {key}")
                except Exception as exc:
                    await interaction.response.send_message(
                        f"Could not assign role: {exc}", ephemeral=True
                    )
                    return

        if pool:
            await add_badge(pool, interaction.guild.id, member.id, key)

        await interaction.response.send_message(
            f"Badge **{key}** granted to {member.mention}.", ephemeral=True
        )

    @app_commands.command(name="list", description="List all badges a member has earned")
    @app_commands.describe(member="Member to look up (defaults to yourself)")
    async def list_badges(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "badges"):
            await interaction.response.send_message("Badges are not enabled.", ephemeral=True)
            return

        target = member or (interaction.user if isinstance(interaction.user, discord.Member) else None)
        if target is None:
            await interaction.response.send_message("Could not determine target member.", ephemeral=True)
            return

        pool = get_bot_db_pool(self.cog.bot)
        badges: list[str] = []
        if pool:
            badges = await get_user_badges(pool, interaction.guild.id, target.id)

        embed = EmbedBuilder.info(
            title=f"🎖 Badges — {safe_embed_text(target.display_name, 256)}",
        )
        embed.description = "\n".join(f"• {safe_embed_text(b, 256)}" for b in badges) if badges else "No badges yet."
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /og group
# ---------------------------------------------------------------------------


class OGGroup(app_commands.Group):
    """OG claim commands."""

    def __init__(self, cog: EngagementCog):
        super().__init__(name="og", description="OG claim commands")
        self.cog = cog

    @app_commands.command(name="setup", description="Post the OG claim message in a channel")
    @app_commands.describe(channel="Channel to post the OG message in")
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "og"):
            await interaction.response.send_message(
                "OG claims are not enabled. Enable via `/engagement toggle og_claims true`.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not perms or not (perms.manage_guild or perms.administrator):
            await interaction.response.send_message("You need Manage Server for this.", ephemeral=True)
            return

        post_channel = channel or (
            interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        )
        if not isinstance(post_channel, discord.TextChannel):
            await interaction.response.send_message("Provide a valid text channel.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        cap: int = int(await _get_setting(self.cog.bot, interaction.guild.id, "og_cap", 50))
        await _get_setting(
            self.cog.bot, interaction.guild.id, "og_deadline_text", "limited time"
        )
        og_text: str = await _get_setting(
            self.cog.bot, interaction.guild.id, "og_claim_text",
            f"React with ⚜ to claim your OG badge! Only {cap} spots available."
        )

        try:
            msg = await post_channel.send(og_text)
            await msg.add_reaction("⚜")
            pool = get_bot_db_pool(self.cog.bot)
            if pool:
                await og_set_setup(pool, interaction.guild.id, msg.id, post_channel.id)
            # Store in runtime cache
            self.cog.og_message_ids[interaction.guild.id] = msg.id
            await interaction.followup.send(
                f"OG message posted in {post_channel.mention}. Message ID: `{msg.id}`.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Failed to post: {exc}", ephemeral=True)

    @app_commands.command(name="status", description="Show current OG claim count and remaining spots")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "og"):
            await interaction.response.send_message("OG claims are not enabled.", ephemeral=True)
            return

        pool = get_bot_db_pool(self.cog.bot)
        total = await og_count_claims(pool, interaction.guild.id) if pool else 0
        cap: int = int(await _get_setting(self.cog.bot, interaction.guild.id, "og_cap", 50))
        remaining = max(0, cap - total)
        await interaction.response.send_message(
            f"OG claims: **{total}/{cap}**\nRemaining: **{remaining}**",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# /weekly group
# ---------------------------------------------------------------------------


class WeeklyGroup(app_commands.Group):
    """Weekly award commands."""

    def __init__(self, cog: EngagementCog):
        super().__init__(name="weekly", description="Weekly award commands")
        self.cog = cog

    @app_commands.command(name="compute", description="Manually compute and announce weekly awards")
    async def compute(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if not await _is_enabled(self.cog.bot, interaction.guild.id, "weekly"):
            await interaction.response.send_message(
                "Weekly awards are not enabled. Enable via `/engagement toggle weekly true`.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not perms or not (perms.manage_guild or perms.administrator):
            await interaction.response.send_message(
                "You need Manage Server to trigger weekly awards.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        award_channel_id = await _get_setting(
            self.cog.bot, interaction.guild.id, "weekly_award_channel_id"
        )
        configs = await _get_award_configs(self.cog.bot, interaction.guild.id)

        await compute_weekly_awards(
            self.cog.bot,
            interaction.guild.id,
            int(award_channel_id) if award_channel_id else None,
            configs,
        )
        await interaction.followup.send("Weekly awards computed.", ephemeral=True)


# ---------------------------------------------------------------------------
# Main Cog
# ---------------------------------------------------------------------------


class EngagementCog(commands.Cog):
    """
    Engagement module — challenges, weekly awards, streaks, badges and OG claims.
    Each feature is independently enabled per guild via /engagement toggle.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._settings_listener = self._on_setting_changed
        # Runtime cache: guild_id -> OG message_id
        self.og_message_ids: dict[int, int] = {}
        # Add command groups
        self.challenge_group = ChallengeGroup(self)
        self.badge_group = BadgeGroup(self)
        self.og_group = OGGroup(self)
        self.weekly_group = WeeklyGroup(self)
        bot.tree.add_command(self.challenge_group)
        bot.tree.add_command(self.badge_group)
        bot.tree.add_command(self.og_group)
        bot.tree.add_command(self.weekly_group)
        settings = getattr(bot, "settings", None)
        if settings:
            settings.add_global_listener(self._settings_listener)

    async def _on_setting_changed(self, scope: str, key: str, guild_id: int, _value: Any) -> None:
        _invalidate_engagement_cache(scope, key, guild_id)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("challenge")
        self.bot.tree.remove_command("badge")
        self.bot.tree.remove_command("og")
        self.bot.tree.remove_command("weekly")

    # -----------------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Rehydrate active challenges and load OG message IDs from DB."""
        pool = get_bot_db_pool(self.bot)
        if pool:
            await rehydrate_challenges(self.bot, pool)
            # Load OG setup for all guilds
            for guild in self.bot.guilds:
                try:
                    setup = await og_get_setup(pool, guild.id)
                    if setup:
                        self.og_message_ids[guild.id] = setup[0]
                except Exception as exc:
                    logger.warning(f"[engagement] OG setup load error guild={guild.id}: {exc}")

    # -----------------------------------------------------------------------
    # on_message — challenges + weekly indexing + streaks
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not message.guild:
            return

        guild_id: int = message.guild.id
        user_id: int = message.author.id
        pool = get_bot_db_pool(self.bot)

        # --- Challenges: count messages per active challenge ---
        if await _is_enabled(self.bot, guild_id, "challenges"):
            guild_challenges = get_guild_challenges(guild_id)
            for chal_id, rt in list(guild_challenges.items()):
                if message.channel.id != rt.get("channel_id"):
                    continue
                mode = rt.get("mode", "leaderboard")
                counts = rt.setdefault("message_counts", {})
                try:
                    if mode == "leaderboard":
                        await challenge_add_participant(pool, chal_id, user_id, increment=True)
                        counts[user_id] = counts.get(user_id, 0) + 1
                    else:
                        await challenge_add_participant(pool, chal_id, user_id, increment=False)
                        counts.setdefault(user_id, 1)
                except Exception as exc:
                    logger.warning(f"[engagement] challenge count error: {exc}")
                    if mode == "leaderboard":
                        counts[user_id] = counts.get(user_id, 0) + 1
                    else:
                        counts.setdefault(user_id, 1)

        # --- Weekly: index message ---
        if await _is_enabled(self.bot, guild_id, "weekly") and pool:
            try:
                has_image = any(
                    getattr(a, "content_type", "") and a.content_type.startswith("image/")
                    for a in getattr(message, "attachments", [])
                )
                food_channel_ids = await _get_food_channel_ids(self.bot, guild_id)
                ch_name = getattr(message.channel, "name", "") or ""
                food_hints = {"health-food", "health", "food"}
                is_food = (
                    message.channel.id in food_channel_ids
                    or any(hint in ch_name for hint in food_hints)
                )
                await weekly_index_message(
                    pool,
                    guild_id,
                    message.id,
                    message.channel.id,
                    user_id,
                    int(message.created_at.timestamp()),
                    has_image,
                    is_food,
                )
            except Exception as exc:
                logger.warning(f"[engagement] weekly index error: {exc}")

        # --- Streaks ---
        if await _is_enabled(self.bot, guild_id, "streaks") and pool:
            try:
                if not isinstance(message.author, discord.Member):
                    return
                today = datetime.now(UTC).date()
                row = await get_streak(pool, guild_id, user_id)
                stored_base: str | None = None
                update_db = False

                if not row:
                    new_days = 1
                    update_db = True
                else:
                    last_day, days, stored_base = row
                    if last_day == today:
                        new_days = days  # already counted today
                    elif last_day is None or (today - last_day).days > 1:
                        # last_day is None should not occur for existing rows,
                        # but guard against corrupt data
                        new_days = 1  # streak broken or corrupt — reset
                        update_db = True
                    else:
                        new_days = days + 1
                        update_db = True

                # Nickname toggle
                nickname_enabled = await _get_setting(
                    self.bot, guild_id, "streaks_nicknames", False
                )
                base_for_db = stored_base or message.author.name
                if nickname_enabled:
                    base_for_db, _, _ = await ensure_streak_nickname(
                        message.author, stored_base, new_days
                    )
                    if stored_base != base_for_db:
                        update_db = True

                if update_db:
                    await set_streak(pool, guild_id, user_id, today, new_days, base_for_db)
            except Exception as exc:
                logger.warning(f"[engagement] streak update error: {exc}")

    # -----------------------------------------------------------------------
    # on_raw_reaction_add — weekly reactions + OG claims
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id or not payload.user_id:
            return

        guild_id: int = payload.guild_id
        pool = get_bot_db_pool(self.bot)

        # --- Weekly: increment reaction count ---
        if await _is_enabled(self.bot, guild_id, "weekly") and pool:
            try:
                await weekly_increment_reaction(pool, guild_id, payload.message_id)
            except Exception as exc:
                logger.warning(f"[engagement] weekly reaction increment error: {exc}")

        # --- OG Claims ---
        if not await _is_enabled(self.bot, guild_id, "og"):
            return
        if str(payload.emoji) != "⚜":
            return

        og_msg_id = self.og_message_ids.get(guild_id)
        if not og_msg_id or payload.message_id != og_msg_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except Exception:
                return

        if pool is None:
            return

        cap: int = int(await _get_setting(self.bot, guild_id, "og_cap", 50))
        total = await og_count_claims(pool, guild_id)
        already = await og_has_claim(pool, guild_id, member.id)

        if not already and total >= cap:
            # Remove reaction and notify
            try:
                ch = self.bot.get_channel(payload.channel_id)
                if isinstance(ch, discord.TextChannel):
                    msg = await ch.fetch_message(payload.message_id)
                    await msg.remove_reaction("⚜", member)
            except Exception:
                pass
            try:
                await member.send("All OG spots have been claimed.")
            except Exception:
                pass
            return

        # Grant badge + role
        og_role_id = await _get_setting(self.bot, guild_id, "badge_role_og")
        if og_role_id:
            role = guild.get_role(int(og_role_id))
            if role:
                try:
                    await member.add_roles(role, reason="OG claim via reaction")
                except Exception as exc:
                    logger.warning(f"[engagement] OG role assign error: {exc}")

        await add_badge(pool, guild_id, member.id, "og")
        await og_insert_claim(pool, guild_id, member.id)
        logger.info(f"[engagement] OG claimed by {member.id} in guild {guild_id}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EngagementCog(bot))
