"""Discord slash commands for Alphapy multi-user agents."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from agents.memory import get_active_session
from agents.registry import list_agents, resolve_agent
from agents.runtime import run_agent_session
from utils.cog_base import AlphaCog
from utils.db_helpers import get_bot_db_pool
from utils.hermit_events import emit_hermit_event
from utils.innersync_identity import get_innersync_id_for_discord
from utils.sanitizer import safe_embed_text

logger = logging.getLogger(__name__)

_AGENT_COLOR = 0x5865F2


def _agents_globally_enabled() -> bool:
    return getattr(config, "ALPHAPY_AGENTS_ENABLED", False)


class AgentsCog(AlphaCog):
    """User-facing Alphapy agent commands (/agent)."""

    agent_group = app_commands.Group(name="agent", description="Run personal Alphapy agents")

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(self.agent_group)

    async def _resolve_user(self, interaction: discord.Interaction) -> tuple[str, int] | None:
        pool = get_bot_db_pool(self.bot)
        if pool is None:
            await interaction.response.send_message(
                "Agent service is temporarily unavailable.",
                ephemeral=True,
            )
            return None

        discord_user_id = interaction.user.id
        innersync_id = await get_innersync_id_for_discord(
            pool,
            discord_user_id,
            allow_profile_fallback=False,
        )
        if not innersync_id:
            await interaction.response.send_message(
                "Link your Innersync account first with `/link`, then try again.",
                ephemeral=True,
            )
            return None
        return innersync_id, discord_user_id

    async def _guild_agents_enabled(self, guild_id: int | None) -> bool:
        if guild_id is None:
            return True
        return self.settings_helper.get_bool("agents", "enabled", guild_id, fallback=False)

    @agent_group.command(name="list", description="List available Alphapy agents")
    async def agent_list(self, interaction: discord.Interaction) -> None:
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment.", ephemeral=True
            )
            return

        lines = []
        for name in list_agents():
            agent = resolve_agent(name)
            desc = agent.description if agent else ""
            lines.append(f"**{name}** — {desc}")
        embed = discord.Embed(
            title="Alphapy Agents",
            description="\n".join(lines) or "No agents registered.",
            color=_AGENT_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @agent_group.command(name="start", description="Start an agent session")
    @app_commands.describe(
        agent="Agent to run (reflection, trade, full)",
        message="Optional focus or question for the agent",
    )
    @app_commands.choices(
        agent=[
            app_commands.Choice(name="reflection", value="reflection"),
            app_commands.Choice(name="trade", value="trade"),
            app_commands.Choice(name="full", value="full"),
        ]
    )
    async def agent_start(
        self,
        interaction: discord.Interaction,
        agent: app_commands.Choice[str],
        message: str | None = None,
    ) -> None:
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment.", ephemeral=True
            )
            return

        guild_id = interaction.guild_id
        if guild_id is not None and not await self._guild_agents_enabled(guild_id):
            await interaction.response.send_message(
                "Agents are disabled in this server. Ask an admin to run `/config agents toggle`.",
                ephemeral=True,
            )
            return

        resolved = await self._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, discord_user_id = resolved

        agent_name = agent.value
        if resolve_agent(agent_name) is None:
            await interaction.response.send_message("Unknown agent.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await run_agent_session(
                innersync_user_id=innersync_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                user_message=message,
                metadata={"source": "discord_slash"},
            )
        except Exception as exc:
            logger.exception("Agent session failed: %s", exc)
            await interaction.followup.send(
                "Something went wrong running the agent.", ephemeral=True
            )
            return

        await emit_hermit_event(
            event_type="gpt_command",
            user_id=discord_user_id,
            guild_id=guild_id,
            payload={"agent": agent_name, "session_id": result.session_id},
        )

        embed = discord.Embed(
            title=f"Agent: {agent_name}",
            description=safe_embed_text(result.summary[:4000]),
            color=_AGENT_COLOR,
        )
        if result.skill_blocks:
            skills_used = ", ".join(result.skill_blocks.keys())
            embed.set_footer(text=f"Session {result.session_id[:8]}… · skills: {skills_used}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @agent_group.command(name="status", description="Show your active agent session")
    @app_commands.describe(agent="Agent name to check")
    @app_commands.choices(
        agent=[
            app_commands.Choice(name="reflection", value="reflection"),
            app_commands.Choice(name="trade", value="trade"),
            app_commands.Choice(name="full", value="full"),
        ]
    )
    async def agent_status(
        self,
        interaction: discord.Interaction,
        agent: app_commands.Choice[str],
    ) -> None:
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment.", ephemeral=True
            )
            return

        resolved = await self._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, _ = resolved

        row = await get_active_session(innersync_id, agent.value)
        if not row:
            await interaction.response.send_message(
                "No active session for this agent.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Active session: {agent.value}",
            description=f"Started: {row.get('started_at', 'unknown')}",
            color=_AGENT_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentsCog(bot))
