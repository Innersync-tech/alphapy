"""Discord UI for quick energy self-report before /agent start.

Uses a persistent View (stable custom_ids + bot.add_view) so button clicks still
ACK after a redeploy. Pending start state is in-process with a short TTL; if the
bot restarted, the user gets a clear ephemeral instead of Discord's timeout.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import discord

from agents.fatigue import ENERGY_LEVEL_LABELS, VALID_ENERGY_LEVELS, save_fatigue_self_report
from agents.runtime import (
    ActiveAgentSessionError,
    AgentSessionQuotaExceededError,
    start_agent_session,
)

logger = logging.getLogger("alphapy.agents.fatigue_ui")

_CUSTOM_ID_PREFIX = "alphapy:fatigue:"
_PENDING_TTL_SEC = 120.0


@dataclass(frozen=True)
class PendingFatigueStart:
    innersync_user_id: str
    discord_user_id: int
    guild_id: int | None
    agent_name: str
    user_message: str | None
    expires_at: float


_pending_starts: dict[int, PendingFatigueStart] = {}


def register_pending_fatigue_start(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    user_message: str | None,
    ttl_sec: float = _PENDING_TTL_SEC,
) -> None:
    """Remember /agent start context until the user picks an energy level or skips."""
    _pending_starts[discord_user_id] = PendingFatigueStart(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        user_message=user_message,
        expires_at=time.monotonic() + ttl_sec,
    )


def pop_pending_fatigue_start(discord_user_id: int) -> PendingFatigueStart | None:
    """Take pending start for this Discord user, or None if missing/expired."""
    pending = _pending_starts.pop(discord_user_id, None)
    if pending is None:
        return None
    if time.monotonic() > pending.expires_at:
        return None
    return pending


def clear_pending_fatigue_starts() -> None:
    """Test helper: wipe in-memory pending map."""
    _pending_starts.clear()


class FatigueQuickCheckView(discord.ui.View):
    """Ephemeral 1–5 energy buttons + skip before starting an agent session.

    Persistent (timeout=None, fixed custom_ids) so clicks are routed after restart.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

        for level in sorted(VALID_ENERGY_LEVELS, key=int):
            short = ENERGY_LEVEL_LABELS[level].split("/")[0].strip()
            self.add_item(
                _EnergyButton(
                    level=level,
                    label=f"{level} · {short}"[:80],
                    row=0 if int(level) <= 3 else 1,
                )
            )
        self.add_item(_SkipEnergyButton())

    async def _complete_with_energy(
        self,
        interaction: discord.Interaction,
        energy_level: str,
    ) -> None:
        # ACK first — Discord requires a response within 3s.
        await interaction.response.defer(ephemeral=True, thinking=True)
        pending = pop_pending_fatigue_start(interaction.user.id)
        if pending is None:
            await interaction.followup.send(
                "This energy check-in expired (or the bot restarted). "
                "Run `/agent start` again.",
                ephemeral=True,
            )
            return

        try:
            await save_fatigue_self_report(
                pending.innersync_user_id,
                energy_level=energy_level,
            )
        except Exception as exc:
            logger.warning("Fatigue self-report save failed: %s", exc)

        await self._run_agent_start(interaction, pending)

    async def _skip_and_start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        pending = pop_pending_fatigue_start(interaction.user.id)
        if pending is None:
            await interaction.followup.send(
                "This energy check-in expired (or the bot restarted). "
                "Run `/agent start` again.",
                ephemeral=True,
            )
            return
        await self._run_agent_start(interaction, pending)

    async def _run_agent_start(
        self,
        interaction: discord.Interaction,
        pending: PendingFatigueStart,
    ) -> None:
        from cogs.agents import _agent_app_link_view, _agent_response_embed
        from gpt.errors import GrokUnavailableError, grok_user_message

        try:
            result = await start_agent_session(
                innersync_user_id=pending.innersync_user_id,
                discord_user_id=pending.discord_user_id,
                guild_id=pending.guild_id,
                agent_name=pending.agent_name,
                user_message=pending.user_message,
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
        except GrokUnavailableError as exc:
            await interaction.followup.send(grok_user_message(exc), ephemeral=True)
            return
        except Exception as exc:
            logger.exception("Agent session failed after fatigue check: %s", exc)
            await interaction.followup.send(
                "Something went wrong running the agent.", ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=_agent_response_embed(result),
            view=_agent_app_link_view(),
            ephemeral=True,
        )


class _EnergyButton(discord.ui.Button):
    def __init__(self, *, level: str, label: str, row: int) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"{_CUSTOM_ID_PREFIX}{level}",
        )
        self._level = level

    async def callback(self, interaction: discord.Interaction) -> None:
        parent = self.view
        if not isinstance(parent, FatigueQuickCheckView):
            await interaction.response.send_message(
                "Something went wrong with this check-in. Run `/agent start` again.",
                ephemeral=True,
            )
            return
        await parent._complete_with_energy(interaction, self._level)


class _SkipEnergyButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Skip",
            style=discord.ButtonStyle.primary,
            row=2,
            custom_id=f"{_CUSTOM_ID_PREFIX}skip",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        parent = self.view
        if not isinstance(parent, FatigueQuickCheckView):
            await interaction.response.send_message(
                "Something went wrong with this check-in. Run `/agent start` again.",
                ephemeral=True,
            )
            return
        await parent._skip_and_start(interaction)
