"""Tests for /health slash command early defer (diff coverage)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.status import health_cmd


@pytest.mark.asyncio
async def test_health_cmd_defers_before_building_embed() -> None:
    interaction = MagicMock()
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    embed = discord.Embed(title="ok")

    with patch("cogs.status._build_health_embed", new=AsyncMock(return_value=embed)):
        await health_cmd.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with(embed=embed, ephemeral=True)


@pytest.mark.asyncio
async def test_health_cmd_followup_on_build_failure() -> None:
    interaction = MagicMock()
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    with patch("cogs.status._build_health_embed", new=AsyncMock(side_effect=RuntimeError("db down"))):
        await health_cmd.callback(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert "Could not load health status" in interaction.followup.send.await_args.args[0]
