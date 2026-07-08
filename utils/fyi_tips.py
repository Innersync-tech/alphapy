"""
Contextual FYI tips: send short, relevant tips when certain first-time events happen per guild.
State is stored in bot_settings (scope fyi); per-guild 24h cooldown prevents spam.
"""

from datetime import UTC, datetime
from typing import Any

import discord

from utils.logger import logger
from utils.timezone import BRUSSELS_TZ

FYI_SCOPE = "fyi"
LAST_SENT_KEY = "_last_sent_at"
FYI_COOLDOWN_SECONDS = 24 * 3600  # 24 hours

# All known FYI keys. Phase 1 and Phase 2 triggers are wired in cogs / gpt helpers.
FYI_KEYS = frozenset({
    "first_guild_join",
    "first_onboarding_done",
    "first_config_wizard_complete",
    "first_reminder",
    "first_reminder_watcher",
    "first_ticket",
    "first_gpt",
    "first_invite_leaderboard",
    "first_growthcheckin",
    "first_add_rule_no_image",
})

# Embed content per key: title, description, optional footer. English only; short and actionable.
FYI_CONTENT: dict[str, dict[str, str]] = {
    "first_guild_join": {
        "title": "👋 Welcome",
        "description": "Get started: run `/config start` to set up onboarding and a log channel. I'll send tips there when you use features for the first time.",
        "footer": "Use /config start to configure this server.",
    },
    "first_onboarding_done": {
        "title": "📋 FYI: Onboarding settings",
        "description": "You can manage onboarding rules and the completion role with `/onboarding show`. Add or edit rules with `add_rule`, and set the role members get after completing onboarding.",
        "footer": "Use /onboarding commands to adjust rules and role.",
    },
    "first_config_wizard_complete": {
        "title": "✅ Setup complete",
        "description": "You can change any setting later with `/<scope> show`. Key scopes: **onboarding** (rules, role), **system** (log channel), **reminders** (if enabled).",
        "footer": "Use the respective /<scope> commands to change settings anytime.",
    },
    "first_reminder": {
        "title": "⏰ FYI: Reminders",
        "description": "Reminders can be **recurring** (e.g. weekly) or **one-off**. Use `/reminder_list` to see and manage them. If you have an announcements channel, embeds there can auto-create reminders.",
        "footer": "Use /reminder_edit <id> or /reminder_delete <id> to manage.",
    },
    "first_reminder_watcher": {
        "title": "🤖 FYI: Auto-reminder from announcement",
        "description": "A reminder was created from an announcement embed. You can adjust watcher behaviour in config; use `/reminder_list` to manage reminders.",
        "footer": "Announcement embeds can auto-create reminders when configured.",
    },
    "first_ticket": {
        "title": "🎫 FYI: Tickets",
        "description": "Staff can claim and close tickets from the buttons in the ticket channel.",
        "footer": "Use the ticket channel buttons to claim or close.",
    },
    "first_gpt": {
        "title": "💬 FYI: Grok commands",
        "description": "Use `/gptstatus` to check API status and usage. Rate limits may apply depending on server configuration.",
        "footer": "Use /gptstatus for API status.",
    },
    "first_invite_leaderboard": {
        "title": "📊 FYI: Invite tracking",
        "description": "Counts are based on used invites. Use `/setinvites` to correct a member's count if needed.",
        "footer": "Use /setinvites to adjust counts.",
    },
    "first_growthcheckin": {
        "title": "🌱 FYI: Growth check-in",
        "description": "Members can share to the growth channel or keep responses private. Run `/growth set_channel` to configure sharing.",
        "footer": "Use /growthcheckin anytime.",
    },
    "first_add_rule_no_image": {
        "title": "📜 FYI: Onboarding rules",
        "description": "You can add optional **thumbnail_url** and **image_url** to rules for richer onboarding. Use `/onboarding add_rule` with those parameters.",
        "footer": "Add thumbnail_url or image_url for richer rules.",
    },
}


def _build_fyi_embed(key: str, bot_name: str | None = None) -> discord.Embed | None:
    """Build the FYI embed for a given key. Returns None if key has no content."""
    content = FYI_CONTENT.get(key)
    if not content:
        return None
    title = content.get("title", "FYI")
    description = content.get("description", "")
    footer = content.get("footer", "")
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blue(),
        timestamp=datetime.now(BRUSSELS_TZ),
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


def _parse_last_sent(value: Any) -> datetime | None:
    """Parse _last_sent_at from stored value (ISO string or number)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None


async def send_fyi_if_first(
    bot: Any,
    guild_id: int,
    key: str,
    *,
    channel_id_override: int | None = None,
) -> None:
    """
    If this is the first time for this guild and this key, and the guild is not in cooldown,
    send the FYI embed to the log channel (or override). Then mark the key and update last_sent_at.
    """
    if key not in FYI_KEYS:
        logger.debug("fyi_tips: unknown key %s, skipping", key)
        return
    if not hasattr(bot, "settings") or not hasattr(bot.settings, "get_raw"):
        return

    already = await bot.settings.get_raw(FYI_SCOPE, key, guild_id, fallback=None)
    if already:
        return

    last_sent_val = await bot.settings.get_raw(FYI_SCOPE, LAST_SENT_KEY, guild_id, fallback=None)
    last_sent = _parse_last_sent(last_sent_val)
    if last_sent is not None:
        now = datetime.now(UTC)
        if (now - last_sent).total_seconds() < FYI_COOLDOWN_SECONDS:
            return

    channel_id = channel_id_override
    if channel_id is None:
        try:
            channel_id = bot.settings.get("system", "log_channel_id", guild_id)
        except KeyError:
            channel_id = 0
    if not channel_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not channel or not hasattr(channel, "send"):
        return

    embed = _build_fyi_embed(key)
    if not embed:
        return

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning("fyi_tips: could not send FYI %s to guild %s: %s", key, guild_id, e)
        return

    now_iso = datetime.now(UTC).isoformat()
    await bot.settings.set_raw(FYI_SCOPE, key, True, guild_id)
    await bot.settings.set_raw(FYI_SCOPE, LAST_SENT_KEY, now_iso, guild_id)
    logger.info("fyi_tips: sent FYI %s for guild %s", key, guild_id)


async def force_send_fyi(
    bot: Any,
    guild_id: int,
    key: str,
    *,
    channel_id_override: int | None = None,
    mark_as_sent: bool = True,
) -> bool:
    """
    Force-send the FYI for the given key (e.g. for /fyi send). Returns True if sent.
    If mark_as_sent is True, sets the key and _last_sent_at so it won't send again until reset.
    """
    if key not in FYI_KEYS:
        return False
    content = FYI_CONTENT.get(key)
    if not content:
        return False

    channel_id = channel_id_override
    if channel_id is None:
        try:
            channel_id = bot.settings.get("system", "log_channel_id", guild_id)
        except KeyError:
            channel_id = 0
    if not channel_id:
        return False

    channel = bot.get_channel(int(channel_id))
    if not channel or not hasattr(channel, "send"):
        return False

    embed = _build_fyi_embed(key)
    if not embed:
        return False

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning("fyi_tips: force_send FYI %s failed for guild %s: %s", key, guild_id, e)
        return False

    if mark_as_sent:
        now_iso = datetime.now(UTC).isoformat()
        await bot.settings.set_raw(FYI_SCOPE, key, True, guild_id)
        await bot.settings.set_raw(FYI_SCOPE, LAST_SENT_KEY, now_iso, guild_id)
    return True


async def reset_fyi(bot: Any, guild_id: int, key: str) -> bool:
    """Clear the FYI flag and cooldown for the given key so the next natural trigger will send again. Returns True if key was valid."""
    if key not in FYI_KEYS:
        return False
    await bot.settings.clear_raw(FYI_SCOPE, key, guild_id)
    await bot.settings.clear_raw(FYI_SCOPE, LAST_SENT_KEY, guild_id)
    return True
