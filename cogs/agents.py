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

from agents.base import AgentResult
from agents.fatigue import should_prompt_fatigue_check
from agents.fatigue_ui import FatigueQuickCheckView
from agents.memory import get_active_session, get_session_messages
from agents.registry import list_agents, resolve_agent
from agents.runtime import (
    ActiveAgentSessionError,
    AgentSessionQuotaExceededError,
    NoActiveAgentSessionError,
    continue_agent_session,
    end_agent_session,
    start_agent_session,
)
from utils.cog_base import AlphaCog
from utils.db_helpers import get_bot_db_pool
from utils.core_discord_integration import normalize_http_url
from utils.hermit_events import emit_hermit_event
from utils.innersync_identity import get_innersync_id_for_discord
from utils.sanitizer import safe_embed_text

logger = logging.getLogger(__name__)

_AGENT_COLOR = 0x5865F2
_DEFAULT_APP_BASE = "https://app.innersync.tech"


def _app_agent_home_url() -> str:
    """Public App agent chat surface (Phase 4 cross-platform)."""
    raw = getattr(config, "INNERSYNC_APP_URL", None) or _DEFAULT_APP_BASE
    base = normalize_http_url(str(raw).strip().rstrip("/")) or _DEFAULT_APP_BASE
    return f"{base.rstrip('/')}/dashboard/agent"


def _agent_response_embed(result: AgentResult, *, active_session: bool = False) -> discord.Embed:
    """Build the /agent start reply: user display name as title, agent type in footer."""
    agent_name = result.agent_name
    if result.display_name:
        title = safe_embed_text(result.display_name, 256)
    else:
        title = safe_embed_text(agent_name.replace("_", " ").title(), 256)

    footer_parts = [f"Agent: {agent_name}", f"Session {result.session_id[:8]}…"]
    if result.turn_count > 1:
        footer_parts.append(f"turn {result.turn_count}")
    if result.skill_blocks:
        skills_used = ", ".join(result.skill_blocks.keys())
        footer_parts.append(f"skills: {skills_used}")
    if active_session:
        footer_parts.append(f"App → {_app_agent_home_url()}")

    embed = discord.Embed(
        title=title,
        description=safe_embed_text(result.summary[:4000]),
        color=_AGENT_COLOR,
    )
    embed.set_footer(text=" · ".join(footer_parts))
    return embed


def _agents_globally_enabled() -> bool:
    return getattr(config, "ALPHAPY_AGENTS_ENABLED", False)


class AgentGroup(app_commands.Group):
    """Slash command group: /agent list|start|continue|end|status"""

    def __init__(self, cog: AgentsCog) -> None:
        super().__init__(name="agent", description="Run personal Alphapy agents")
        self.cog = cog

    @app_commands.command(name="list", description="List available Alphapy agents")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment. "
                "Set `ALPHAPY_AGENTS_ENABLED=true` on the bot service.",
                ephemeral=True,
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

    @app_commands.command(name="start", description="Start an agent session")
    @app_commands.describe(
        message="Optional focus or question for the agent",
    )
    async def start_cmd(
        self,
        interaction: discord.Interaction,
        message: str | None = None,
    ) -> None:
        cog = self.cog
        agent_name = "reflection"
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment. "
                "Set `ALPHAPY_AGENTS_ENABLED=true` on the bot service.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild_id
        if guild_id is not None and not cog._guild_agents_enabled(guild_id):
            await interaction.response.send_message(
                "Agents are disabled in this server. Ask an admin to run `/config agents toggle`.",
                ephemeral=True,
            )
            return

        resolved = await cog._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, discord_user_id = resolved

        if resolve_agent(agent_name) is None:
            await interaction.response.send_message("Unknown agent.", ephemeral=True)
            return

        try:
            prompt_fatigue = await should_prompt_fatigue_check(innersync_id)
        except Exception:
            prompt_fatigue = False

        if prompt_fatigue:
            view = FatigueQuickCheckView(
                cog,
                innersync_user_id=innersync_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                user_message=message,
            )
            await interaction.response.send_message(
                "**Quick energy check-in** — How is your energy right now? "
                "This helps your reflection agent pace the session. "
                "You can also set this in Innersync App → Settings → Agent memory.",
                view=view,
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await start_agent_session(
                innersync_user_id=innersync_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                user_message=message,
                channel="discord",
            )
        except ActiveAgentSessionError:
            await interaction.followup.send(
                "You already have an active session. Use `/agent continue`, continue in the "
                f"App ({_app_agent_home_url()}), or `/agent end` to finish.",
                ephemeral=True,
            )
            return
        except AgentSessionQuotaExceededError as exc:
            await interaction.followup.send(
                f"You've reached your daily limit of **{exc.limit}** agent sessions. "
                "Try again tomorrow or upgrade for more: `/premium`",
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("Agent session failed: %s", exc)
            await interaction.followup.send(
                "Something went wrong running the agent.", ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=_agent_response_embed(result, active_session=True), ephemeral=True
        )

    @app_commands.command(name="continue", description="Continue your active agent session")
    @app_commands.describe(
        message="Your follow-up message for the agent",
    )
    async def continue_cmd(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        cog = self.cog
        agent_name = "reflection"
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment. "
                "Set `ALPHAPY_AGENTS_ENABLED=true` on the bot service.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild_id
        if guild_id is not None and not cog._guild_agents_enabled(guild_id):
            await interaction.response.send_message(
                "Agents are disabled in this server. Ask an admin to run `/config agents toggle`.",
                ephemeral=True,
            )
            return

        resolved = await cog._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, discord_user_id = resolved

        if not message.strip():
            await interaction.response.send_message(
                "Please provide a message for the agent.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await continue_agent_session(
                innersync_user_id=innersync_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                user_message=message.strip(),
                channel="discord",
            )
        except NoActiveAgentSessionError:
            await interaction.followup.send(
                "No active session. Start one with `/agent start` first.", ephemeral=True
            )
            return
        except Exception as exc:
            logger.exception("Agent continue failed: %s", exc)
            await interaction.followup.send(
                "Something went wrong continuing the session.", ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=_agent_response_embed(result, active_session=True), ephemeral=True
        )

    @app_commands.command(name="end", description="End your active agent session")
    async def end_cmd(self, interaction: discord.Interaction) -> None:
        cog = self.cog
        agent_name = "reflection"
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment. "
                "Set `ALPHAPY_AGENTS_ENABLED=true` on the bot service.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild_id
        if guild_id is not None and not cog._guild_agents_enabled(guild_id):
            await interaction.response.send_message(
                "Agents are disabled in this server. Ask an admin to run `/config agents toggle`.",
                ephemeral=True,
            )
            return

        resolved = await cog._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, discord_user_id = resolved

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            result = await end_agent_session(
                innersync_user_id=innersync_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                channel="discord",
            )
        except NoActiveAgentSessionError:
            await interaction.followup.send(
                "No active session to end. Use `/agent start` first.", ephemeral=True
            )
            return
        except Exception as exc:
            logger.exception("Agent end failed: %s", exc)
            await interaction.followup.send(
                "Something went wrong ending the session.", ephemeral=True
            )
            return

        await emit_hermit_event(
            event_type="gpt_command",
            user_id=discord_user_id,
            guild_id=guild_id,
            payload={"agent": agent_name, "session_id": result.session_id},
        )

        embed = _agent_response_embed(result)
        embed.set_footer(text=embed.footer.text + " · ended")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="Show your active reflection agent session")
    async def status_cmd(self, interaction: discord.Interaction) -> None:
        agent_name = "reflection"
        if not _agents_globally_enabled():
            await interaction.response.send_message(
                "Agents are not enabled on this deployment. "
                "Set `ALPHAPY_AGENTS_ENABLED=true` on the bot service.",
                ephemeral=True,
            )
            return

        resolved = await self.cog._resolve_user(interaction)
        if resolved is None:
            return
        innersync_id, _ = resolved

        row = await get_active_session(innersync_id, agent_name)
        if not row:
            await interaction.response.send_message(
                "No active session for the reflection agent.", ephemeral=True
            )
            return

        session_id = str(row.get("id", ""))
        messages = await get_session_messages(session_id) if session_id else []
        turn_count = max((int(m.get("turn_index", 0)) for m in messages), default=-1) + 1
        if turn_count <= 0:
            turn_count = 1

        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        origin_channel = metadata.get("origin_channel")
        origin_hint = ""
        if origin_channel in {"discord", "app"}:
            label = "Discord" if origin_channel == "discord" else "Innersync App"
            origin_hint = f"Started on: **{label}**\n"

        embed = discord.Embed(
            title="Active session: reflection",
            description=(
                f"{origin_hint}"
                f"Started: {row.get('started_at', 'unknown')}\n"
                f"Turns: {turn_count}\n\n"
                "Discord: `/agent continue` or `/agent end`\n"
                f"App: {_app_agent_home_url()}"
            ),
            color=_AGENT_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AgentsCog(AlphaCog):
    """User-facing Alphapy agent commands (/agent)."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self.agent_group = AgentGroup(self)
        bot.tree.add_command(self.agent_group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("agent")

    def _guild_agents_enabled(self, guild_id: int | None) -> bool:
        if guild_id is None:
            return True
        return self.settings_helper.get_bool("agents", "enabled", guild_id, fallback=False)

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentsCog(bot))
