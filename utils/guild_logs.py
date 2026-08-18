"""Send user-action embeds to the guild's configured system log channel."""

from __future__ import annotations

from typing import Any

import discord

from utils.embed_builder import EmbedBuilder
from utils.logger import log_guild_action, log_with_guild, should_log_to_discord
from utils.sanitizer import safe_embed_text


def home_guild_id() -> int | None:
    """Innersync home guild (`MAIN_GUILD_ID`), or None if unset."""
    try:
        import config

        gid = int(getattr(config, "MAIN_GUILD_ID", 0) or 0)
    except (TypeError, ValueError):
        return None
    return gid if gid > 0 else None


def guild_id_from_interaction(interaction: discord.Interaction | Any) -> int | None:
    """Return a real guild id from an interaction, or None in DMs / tests."""
    gid = getattr(interaction, "guild_id", None)
    if isinstance(gid, int) and gid > 0:
        return gid
    guild = getattr(interaction, "guild", None)
    if guild is not None:
        gid = getattr(guild, "id", None)
        if isinstance(gid, int) and gid > 0:
            return gid
    return None


def format_user_log_line(user: Any) -> str:
    """Stable, mention-safe user line for guild log embeds."""
    user_id = getattr(user, "id", "?")
    mention = getattr(user, "mention", None)
    if isinstance(mention, str) and mention.startswith("<@"):
        label = mention
    else:
        name = getattr(user, "display_name", None) or getattr(user, "name", None) or str(user)
        label = safe_embed_text(str(name), 128)
    return f"**User:** {label} (`{user_id}`)"


async def send_guild_log(
    bot: Any,
    guild_id: int | None,
    title: str,
    description: str,
    *,
    level: str = "info",
    source: str = "system",
) -> None:
    """Post an embed to `system.log_channel_id`. No-ops if unset or unreachable."""
    if not isinstance(guild_id, int) or guild_id <= 0:
        return
    if bot is None:
        return
    if not should_log_to_discord(level, guild_id):
        return

    settings = getattr(bot, "settings", None)
    if not settings:
        return

    try:
        raw = settings.get("system", "log_channel_id", guild_id)
        channel_id = int(raw) if raw else 0
    except (TypeError, ValueError, KeyError):
        return
    if channel_id <= 0:
        log_with_guild("No log channel configured for user-action logging", guild_id, "debug")
        return

    try:
        embed = EmbedBuilder.log(title, description, level, guild_id)
        embed.set_footer(text=f"{source} | Guild: {guild_id}")
        channel = bot.get_channel(channel_id)
        if channel is None or not hasattr(channel, "send"):
            log_with_guild(
                f"Log channel {channel_id} not found or not accessible",
                guild_id,
                "warning",
            )
            return
        await channel.send(embed=embed)
        log_guild_action(guild_id, "LOG_SENT", details=f"{source}: {title}")
    except Exception as exc:
        log_with_guild(f"Could not send user-action log embed: {exc}", guild_id, "error")


async def send_home_guild_log(
    bot: Any,
    title: str,
    description: str,
    *,
    level: str = "info",
    source: str = "system",
) -> None:
    """Post to the home guild log channel only. No-ops if `MAIN_GUILD_ID` is unset."""
    await send_guild_log(
        bot,
        home_guild_id(),
        title,
        description,
        level=level,
        source=source,
    )
