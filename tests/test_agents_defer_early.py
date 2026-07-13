"""Tests for early defer in /agent slash commands (diff coverage)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentResult
from cogs.agents import AgentsCog


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.settings = MagicMock()
    bot.settings.get = MagicMock(return_value=True)
    bot.settings._pool = MagicMock()
    bot.tree = MagicMock()
    return bot


def _make_interaction(*, is_done: bool = True) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 12345
    interaction.guild_id = 999
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=is_done)
    return interaction


@pytest.fixture
def agents_cog() -> AgentsCog:
    return AgentsCog(_make_bot())


@pytest.mark.asyncio
async def test_send_ephemeral_uses_followup_when_deferred(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction(is_done=True)
    await agents_cog._send_ephemeral(interaction, "hello")
    interaction.followup.send.assert_awaited_once_with("hello", ephemeral=True)


@pytest.mark.asyncio
async def test_send_ephemeral_uses_response_when_not_deferred(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction(is_done=False)
    await agents_cog._send_ephemeral(interaction, "hello")
    interaction.response.send_message.assert_awaited_once_with("hello", ephemeral=True)


@pytest.mark.asyncio
async def test_resolve_user_pool_none_uses_followup(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    with patch("cogs.agents.get_bot_db_pool", return_value=None):
        result = await agents_cog._resolve_user(interaction)
    assert result is None
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_user_not_linked_uses_followup(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    with (
        patch("cogs.agents.get_bot_db_pool", return_value=MagicMock()),
        patch("cogs.agents.get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
    ):
        result = await agents_cog._resolve_user(interaction)
    assert result is None
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_start_defers_before_session_start(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Hello.",
        skill_blocks={},
    )
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(agents_cog, "_guild_agents_enabled", return_value=True),
        patch.object(
            agents_cog,
            "_resolve_user",
            new=AsyncMock(return_value=(innersync_id, interaction.user.id)),
        ),
        patch("cogs.agents.resolve_agent", return_value=MagicMock()),
        patch("cogs.agents.should_prompt_fatigue_check", new=AsyncMock(return_value=False)),
        patch("cogs.agents.start_agent_session", new=AsyncMock(return_value=result)),
    ):
        await agents_cog.agent_group.start_cmd.callback(agents_cog.agent_group, interaction, message=None)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_agent_start_fatigue_prompt_uses_followup(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(agents_cog, "_guild_agents_enabled", return_value=True),
        patch.object(
            agents_cog,
            "_resolve_user",
            new=AsyncMock(return_value=(innersync_id, interaction.user.id)),
        ),
        patch("cogs.agents.resolve_agent", return_value=MagicMock()),
        patch("cogs.agents.should_prompt_fatigue_check", new=AsyncMock(return_value=True)),
    ):
        await agents_cog.agent_group.start_cmd.callback(agents_cog.agent_group, interaction, message="focus")

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs.get("view") is not None


@pytest.mark.asyncio
async def test_agent_continue_defers_before_resolve(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Continue.",
        skill_blocks={},
    )
    resolve_mock = AsyncMock(return_value=(innersync_id, interaction.user.id))
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(agents_cog, "_guild_agents_enabled", return_value=True),
        patch.object(agents_cog, "_resolve_user", new=resolve_mock),
        patch("cogs.agents.continue_agent_session", new=AsyncMock(return_value=result)),
    ):
        await agents_cog.agent_group.continue_cmd.callback(
            agents_cog.agent_group, interaction, message="next step"
        )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    resolve_mock.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_agent_end_defers_before_resolve(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    result = AgentResult(
        agent_name="reflection",
        session_id="a64fdd42-1234-5678-9abc-def012345678",
        summary="Ended.",
        skill_blocks={},
    )
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(agents_cog, "_guild_agents_enabled", return_value=True),
        patch.object(
            agents_cog,
            "_resolve_user",
            new=AsyncMock(return_value=(innersync_id, interaction.user.id)),
        ),
        patch("cogs.agents.end_agent_session", new=AsyncMock(return_value=result)),
        patch("cogs.agents.emit_hermit_event", new=AsyncMock()),
    ):
        await agents_cog.agent_group.end_cmd.callback(agents_cog.agent_group, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)


@pytest.mark.asyncio
async def test_agent_status_defers_and_reports_no_session(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(
            agents_cog,
            "_resolve_user",
            new=AsyncMock(return_value=(innersync_id, interaction.user.id)),
        ),
        patch("cogs.agents.get_active_session", new=AsyncMock(return_value=None)),
    ):
        await agents_cog.agent_group.status_cmd.callback(agents_cog.agent_group, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_status_defers_and_sends_active_session(agents_cog: AgentsCog) -> None:
    interaction = _make_interaction()
    innersync_id = "550e8400-e29b-41d4-a716-446655440000"
    with (
        patch("cogs.agents._agents_globally_enabled", return_value=True),
        patch.object(
            agents_cog,
            "_resolve_user",
            new=AsyncMock(return_value=(innersync_id, interaction.user.id)),
        ),
        patch(
            "cogs.agents.get_active_session",
            new=AsyncMock(
                return_value={
                    "id": "sess-1",
                    "agent_name": "reflection",
                    "started_at": "2026-07-13T12:00:00Z",
                    "metadata": {"origin_channel": "discord"},
                }
            ),
        ),
        patch("cogs.agents.get_session_messages", new=AsyncMock(return_value=[{"turn_index": 0}])),
    ):
        await agents_cog.agent_group.status_cmd.callback(agents_cog.agent_group, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs.get("embed") is not None
