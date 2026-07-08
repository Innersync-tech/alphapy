"""
Innersync identity: /link, /unlink, and /profile (Discord ↔ central user id).
"""

from __future__ import annotations

import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from utils.cog_base import AlphaCog
from utils.core_discord_integration import (
    extract_link_url,
    fetch_innersync_profile_for_discord,
    request_discord_link_session,
    request_discord_unlink,
)
from utils.db_helpers import get_bot_db_pool
from utils.embed_builder import EmbedBuilder
from utils.innersync_identity import (
    delete_discord_link_for_discord_user,
    get_innersync_id_for_discord,
)
from utils.logger import logger
from utils.sanitizer import safe_embed_text
from utils.user_messages import ERR_DB

_LINK_RATELIMIT: dict[int, list[float]] = defaultdict(list)
_LINK_WINDOW_SEC = 60.0
_LINK_MAX_PER_WINDOW = 3


def _link_rate_ok(discord_user_id: int) -> bool:
    now = time.monotonic()
    cutoff = now - _LINK_WINDOW_SEC
    window = [t for t in _LINK_RATELIMIT[discord_user_id] if t > cutoff]
    if len(window) >= _LINK_MAX_PER_WINDOW:
        _LINK_RATELIMIT[discord_user_id] = window
        return False
    window.append(now)
    _LINK_RATELIMIT[discord_user_id] = window
    return True


@app_commands.command(name="link", description="Link this Discord account to your Innersync identity")
async def link_slash(interaction: discord.Interaction) -> None:
    if not _link_rate_ok(interaction.user.id):
        await interaction.response.send_message(
            "You are using this command too often. Please wait about a minute and try again.",
            ephemeral=True,
        )
        return

    pool = get_bot_db_pool(interaction.client)
    if pool is None:
        await interaction.response.send_message(ERR_DB, ephemeral=True)
        return

    existing = await get_innersync_id_for_discord(
        pool, interaction.user.id, allow_profile_fallback=False
    )
    if existing:
        await interaction.response.send_message(
            embed=EmbedBuilder.info(
                title="Already linked",
                description=(
                    "This Discord account is already linked to an Innersync profile. "
                    "Use `/profile` to see your details, or `/unlink` if you need to disconnect."
                ),
            ),
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    session = await request_discord_link_session(interaction.user.id)
    url = extract_link_url(session)
    if not url:
        await interaction.followup.send(
            embed=EmbedBuilder.info(
                title="Link unavailable",
                description=(
                    "Linking is temporarily unavailable. "
                    "Try again later or contact support@innersync.tech if this persists."
                ),
            ),
            ephemeral=True,
        )
        return

    # Link button instead of markdown — tokens often contain "_" which breaks [text](url) in embeds.
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="Open link session",
            url=url,
            style=discord.ButtonStyle.link,
        )
    )
    await interaction.followup.send(
        embed=EmbedBuilder.info(
            title="Link Innersync",
            description=(
                "Use the button below to open the Innersync App while signed in. "
                "When you finish, Core will confirm the link and you will receive a DM from this bot."
            ),
        ),
        view=view,
        ephemeral=True,
    )


@app_commands.command(name="unlink", description="Remove the Innersync link for this Discord account")
async def unlink_slash(interaction: discord.Interaction) -> None:
    pool = get_bot_db_pool(interaction.client)
    if pool is None:
        await interaction.response.send_message(ERR_DB, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    unlinked = False
    try:
        unlinked = await request_discord_unlink(interaction.user.id)
    except Exception as e:
        logger.warning("unlink: Core request failed: %s", e)

    if not unlinked:
        try:
            unlinked = await delete_discord_link_for_discord_user(pool, interaction.user.id)
        except Exception as e:
            logger.warning("unlink: local delete failed: %s", e)
            await interaction.followup.send(
                "Could not remove the link. Please try again later.",
                ephemeral=True,
            )
            return

    if unlinked:
        await interaction.followup.send(
            embed=EmbedBuilder.info(
                title="Unlinked",
                description=(
                    "Your Discord account is no longer linked to Innersync. "
                    "You can run `/link` again anytime."
                ),
            ),
            ephemeral=True,
        )
    else:
        legacy_profile = await get_innersync_id_for_discord(
            pool, interaction.user.id, allow_profile_fallback=True
        )
        if legacy_profile:
            desc = (
                "No active link was found. If you still see a linked profile, "
                "open App → Settings → Integrations and disconnect, or run `/link` to refresh."
            )
        else:
            desc = "There was no link stored for this Discord account."
        await interaction.followup.send(
            embed=EmbedBuilder.info(
                title="Nothing to unlink",
                description=desc,
            ),
            ephemeral=True,
        )


@app_commands.command(name="profile", description="Show your Innersync-linked profile (or Discord fallback)")
async def profile_slash(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    profile = await fetch_innersync_profile_for_discord(interaction.user.id)
    if profile:
        name = profile.get("display_name") or profile.get("name") or interaction.user.display_name
        avatar = profile.get("avatar_url") or profile.get("avatar")
        iid = profile.get("innersync_user_id") or profile.get("user_id")
        lines = [f"**{safe_embed_text(str(name), 256)}**"]
        if iid:
            lines.append(f"Innersync user id: `{safe_embed_text(str(iid), 64)}`")
        embed = EmbedBuilder.info(
            title="Innersync profile",
            description="\n".join(lines),
        )
        if isinstance(avatar, str) and avatar.startswith("http"):
            embed.set_thumbnail(url=avatar[:2048])
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    pool = get_bot_db_pool(interaction.client)
    linked_id = None
    if pool is not None:
        try:
            linked_id = await get_innersync_id_for_discord(pool, interaction.user.id)
        except Exception as e:
            logger.debug("profile: link lookup failed: %s", e)

    if linked_id:
        desc = (
            f"Your account is linked (Innersync id `{safe_embed_text(linked_id, 48)}`), "
            "but the central profile service did not return details. Try again later."
        )
    else:
        desc = (
            "No central profile is available yet. Run `/link` to connect this Discord account "
            "to your Innersync identity."
        )

    embed = EmbedBuilder.info(
        title="Profile",
        description=(
            f"**Discord:** {safe_embed_text(interaction.user.display_name, 128)}\n\n{desc}"
        ),
    )
    if interaction.user.display_avatar:
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed, ephemeral=True)


class InnersyncIdentityCog(AlphaCog):
    """Slash commands for Innersync identity linking and profile."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InnersyncIdentityCog(bot))
    bot.tree.add_command(link_slash)
    bot.tree.add_command(unlink_slash)
    bot.tree.add_command(profile_slash)
