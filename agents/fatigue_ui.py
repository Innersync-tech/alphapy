"""Discord UI for quick energy self-report before /agent start."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from agents.fatigue import ENERGY_LEVEL_LABELS, VALID_ENERGY_LEVELS, save_fatigue_self_report
from agents.runtime import (
    ActiveAgentSessionError,
    AgentSessionQuotaExceededError,
    start_agent_session,
)

if TYPE_CHECKING:
    from cogs.agents import AgentsCog

logger = logging.getLogger("alphapy.agents.fatigue_ui")


class FatigueQuickCheckView(discord.ui.View):
    """Ephemeral 1–5 energy buttons + skip before starting an agent session."""

    def __init__(
        self,
        cog: AgentsCog,
        *,
        innersync_user_id: str,
        discord_user_id: int,
        guild_id: int | None,
        agent_name: str,
        user_message: str | None,
    ) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.innersync_user_id = innersync_user_id
        self.discord_user_id = discord_user_id
        self.guild_id = guild_id
        self.agent_name = agent_name
        self.user_message = user_message
        self._started = False

        for level in sorted(VALID_ENERGY_LEVELS, key=int):
            short = ENERGY_LEVEL_LABELS[level].split("/")[0].strip()
            self.add_item(
                _EnergyButton(
                    level=level,
                    label=f"{level} · {short}"[:80],
                    parent=self,
                    row=0 if int(level) <= 3 else 1,
                )
            )
        self.add_item(_SkipEnergyButton(parent=self))

    async def _complete_with_energy(
        self,
        interaction: discord.Interaction,
        energy_level: str,
    ) -> None:
        if self._started:
            await interaction.response.send_message("Session already starting.", ephemeral=True)
            return
        self._started = True
        self.stop()

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await save_fatigue_self_report(
                self.innersync_user_id,
                energy_level=energy_level,
            )
        except Exception as exc:
            logger.warning("Fatigue self-report save failed: %s", exc)

        await self._run_agent_start(interaction)

    async def _skip_and_start(self, interaction: discord.Interaction) -> None:
        if self._started:
            await interaction.response.send_message("Session already starting.", ephemeral=True)
            return
        self._started = True
        self.stop()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._run_agent_start(interaction)

    async def _run_agent_start(self, interaction: discord.Interaction) -> None:
        from cogs.agents import _agent_response_embed

        try:
            result = await start_agent_session(
                innersync_user_id=self.innersync_user_id,
                discord_user_id=self.discord_user_id,
                guild_id=self.guild_id,
                agent_name=self.agent_name,
                user_message=self.user_message,
                channel="discord",
            )
        except ActiveAgentSessionError:
            await interaction.followup.send(
                "You already have an active session. Use `/agent continue` to add a turn "
                "or `/agent end` to finish.",
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
            logger.exception("Agent session failed after fatigue check: %s", exc)
            await interaction.followup.send(
                "Something went wrong running the agent.", ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=_agent_response_embed(result), ephemeral=True
        )


class _EnergyButton(discord.ui.Button):
    def __init__(
        self,
        *,
        level: str,
        label: str,
        parent: FatigueQuickCheckView,
        row: int,
    ) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self._level = level
        self._parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._parent._complete_with_energy(interaction, self._level)


class _SkipEnergyButton(discord.ui.Button):
    def __init__(self, *, parent: FatigueQuickCheckView) -> None:
        super().__init__(label="Skip", style=discord.ButtonStyle.primary, row=2)
        self._parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._parent._skip_and_start(interaction)
