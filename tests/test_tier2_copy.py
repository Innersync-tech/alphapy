"""Tests for Tier-2 copy polish and user_messages rollout."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from utils.user_messages import ERR_DB, ERR_GENERIC, ERR_GUILD_ONLY


def test_user_message_constants():
    assert ERR_GUILD_ONLY.startswith("❌")
    assert "server" in ERR_GUILD_ONLY.lower()
    assert ERR_DB.startswith("⛔")
    assert "Database" in ERR_DB
    assert ERR_GENERIC.startswith("❌")


@pytest.mark.asyncio
async def test_growth_keep_private_copy():
    from cogs.growth import GrowthShareView

    channel = MagicMock(spec=discord.TextChannel)
    view = GrowthShareView("goal", "obstacle", "feeling", "reply", channel)
    interaction = AsyncMock()
    private_btn = next(child for child in view.children if getattr(child, "label", None) == "Keep private")

    await private_btn.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    content = interaction.response.edit_message.await_args.kwargs["content"]
    assert "nothing is posted to the growth channel" in content


@pytest.mark.asyncio
async def test_verification_panel_post_guild_only():
    from cogs.verification import VerificationCog

    cog = VerificationCog(MagicMock())
    interaction = AsyncMock()
    interaction.guild = None
    interaction.response.send_message = AsyncMock()

    with patch("utils.validators.validate_admin", return_value=(True, None)):
        await cog.verification_panel_post(interaction, channel=None)

    interaction.response.send_message.assert_awaited_once_with(ERR_GUILD_ONLY, ephemeral=True)


@pytest.mark.asyncio
async def test_verification_start_button_guild_only():
    from cogs.verification import VerificationPanelView

    cog = MagicMock()
    view = VerificationPanelView(cog)
    interaction = AsyncMock()
    interaction.guild = None
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    button = MagicMock()

    await view.start_verification(interaction, button)

    interaction.followup.send.assert_awaited_once_with(ERR_GUILD_ONLY, ephemeral=True)


@pytest.mark.asyncio
async def test_contentgen_uses_generic_error_on_failure():
    from cogs.contentgen import ContentGen

    cog = ContentGen(MagicMock())
    interaction = AsyncMock()
    interaction.guild = MagicMock(id=1)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    style = MagicMock(value="punchy")

    with patch("cogs.contentgen.ask_gpt", side_effect=RuntimeError("boom")):
        await cog.create_caption(interaction, topic="discipline", style=style)

    interaction.followup.send.assert_awaited_once_with(ERR_GENERIC, ephemeral=True)


@pytest.mark.asyncio
async def test_learn_topic_defer_failure_uses_generic_error():
    from cogs.learn import LearnTopic

    cog = LearnTopic(MagicMock())
    interaction = AsyncMock()
    interaction.guild = MagicMock(id=1)
    interaction.user.id = 42
    interaction.response.defer = AsyncMock(side_effect=RuntimeError("defer failed"))
    interaction.response.send_message = AsyncMock()

    with patch("cogs.learn.log_gpt_error"):
        await cog.learn_topic(interaction, topic="RSI")

    interaction.response.send_message.assert_awaited_once_with(ERR_GENERIC, ephemeral=True)


@pytest.mark.asyncio
async def test_reminder_list_guild_only():
    from cogs.reminders import ReminderCog

    bot = MagicMock()
    cog = ReminderCog(bot)
    interaction = AsyncMock()
    interaction.guild = None
    interaction.response.send_message = AsyncMock()

    await cog.reminder_list(interaction)

    interaction.response.send_message.assert_awaited_once_with(ERR_GUILD_ONLY, ephemeral=True)


@pytest.mark.asyncio
async def test_reminder_edit_db_unavailable():
    from cogs.reminders import ReminderCog

    bot = MagicMock()
    cog = ReminderCog(bot)
    interaction = AsyncMock()
    interaction.guild = MagicMock(id=1)
    interaction.response.send_message = AsyncMock()
    cog._is_enabled = MagicMock(return_value=True)
    cog._ensure_connection = AsyncMock(return_value=False)

    await cog.reminder_edit(interaction, reminder_id=1)

    interaction.response.send_message.assert_awaited_once_with(ERR_DB, ephemeral=True)


@pytest.mark.asyncio
async def test_leadership_challenge_select_generic_error():
    from cogs.leadership import ChallengeSelect

    select = ChallengeSelect(MagicMock())
    interaction = AsyncMock()
    interaction.user.id = 1
    interaction.guild = MagicMock(id=2)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    select.values = ["burnout"]

    with patch("cogs.leadership.ask_gpt", side_effect=RuntimeError("fail")):
        await select.callback(interaction)

    interaction.followup.send.assert_awaited_once_with(ERR_GENERIC, ephemeral=True)
