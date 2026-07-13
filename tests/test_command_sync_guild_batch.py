"""Tests for post-connect guild-only command batch sync."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.command_sync import GuildSyncBatchResult, SyncResult, sync_guild_only_commands_for_all_guilds


def _make_guild(guild_id: int, name: str) -> MagicMock:
    guild = MagicMock()
    guild.id = guild_id
    guild.name = name
    return guild


@pytest.mark.asyncio
async def test_sync_guild_batch_no_guild_only_commands() -> None:
    bot = MagicMock()
    bot.guilds = [_make_guild(1, "Alpha")]

    with patch("utils.command_sync.detect_guild_only_commands", return_value=False):
        result = await sync_guild_only_commands_for_all_guilds(bot, sync_type="first_ready")

    assert result == GuildSyncBatchResult(0, 0, 1)


@pytest.mark.asyncio
async def test_sync_guild_batch_empty_guilds() -> None:
    bot = MagicMock()
    bot.guilds = []

    with patch("utils.command_sync.detect_guild_only_commands", return_value=True):
        result = await sync_guild_only_commands_for_all_guilds(bot, sync_type="first_ready")

    assert result == GuildSyncBatchResult(0, 0, 0)


@pytest.mark.asyncio
async def test_sync_guild_batch_syncs_all_guilds() -> None:
    bot = MagicMock()
    guild_a = _make_guild(1, "Alpha")
    guild_b = _make_guild(2, "Beta")
    bot.guilds = [guild_a, guild_b]

    async def fake_safe_sync(_bot, guild=None, force=False):
        return SyncResult(success=True, command_count=3, sync_type="guild")

    with (
        patch("utils.command_sync.detect_guild_only_commands", return_value=True),
        patch("utils.command_sync.safe_sync", side_effect=fake_safe_sync) as safe_sync_mock,
        patch("utils.command_sync.log_operational_event"),
    ):
        result = await sync_guild_only_commands_for_all_guilds(bot, sync_type="first_ready")

    assert result == GuildSyncBatchResult(2, 0, 2)
    assert safe_sync_mock.await_count == 2


@pytest.mark.asyncio
async def test_sync_guild_batch_counts_failures() -> None:
    bot = MagicMock()
    guild_a = _make_guild(1, "Alpha")
    guild_b = _make_guild(2, "Beta")
    bot.guilds = [guild_a, guild_b]

    async def fake_safe_sync(_bot, guild=None, force=False):
        if guild.id == 1:
            return SyncResult(success=True, command_count=2, sync_type="guild")
        return SyncResult(success=False, command_count=0, error="cooldown", cooldown_remaining=60.0, sync_type="guild")

    with (
        patch("utils.command_sync.detect_guild_only_commands", return_value=True),
        patch("utils.command_sync.safe_sync", side_effect=fake_safe_sync),
        patch("utils.command_sync.log_operational_event"),
    ):
        result = await sync_guild_only_commands_for_all_guilds(bot, sync_type="reconnect")

    assert result == GuildSyncBatchResult(1, 1, 2)
