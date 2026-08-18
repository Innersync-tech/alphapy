"""Guild log-channel helper and user-action logging hooks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.guild_logs import (
    format_user_log_line,
    guild_id_from_interaction,
    home_guild_id,
    send_guild_log,
    send_home_guild_log,
)


def test_guild_id_from_interaction_int() -> None:
    interaction = MagicMock()
    interaction.guild_id = 42
    assert guild_id_from_interaction(interaction) == 42


def test_guild_id_from_interaction_ignores_mock_ids() -> None:
    interaction = MagicMock()
    assert guild_id_from_interaction(interaction) is None


def test_guild_id_from_interaction_uses_guild_object() -> None:
    interaction = MagicMock()
    interaction.guild_id = None
    interaction.guild.id = 77
    assert guild_id_from_interaction(interaction) == 77


def test_format_user_log_line_prefers_mention() -> None:
    user = MagicMock()
    user.id = 9
    user.mention = "<@9>"
    assert format_user_log_line(user) == "**User:** <@9> (`9`)"


@pytest.mark.asyncio
async def test_send_guild_log_noops_without_guild() -> None:
    bot = MagicMock()
    await send_guild_log(bot, None, "Title", "Desc")
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_send_guild_log_noops_without_channel() -> None:
    bot = MagicMock()
    bot.settings.get.return_value = 0
    await send_guild_log(bot, 1, "Title", "Desc")
    bot.get_channel.assert_not_called()


def test_home_guild_id_reads_main_guild(monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    monkeypatch.setattr(config, "MAIN_GUILD_ID", 1_439_381_029_721_735_271)
    assert home_guild_id() == 1_439_381_029_721_735_271


def test_home_guild_id_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    monkeypatch.setattr(config, "MAIN_GUILD_ID", 0)
    assert home_guild_id() is None


@pytest.mark.asyncio
async def test_send_home_guild_log_uses_main_guild_not_caller_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    monkeypatch.setattr(config, "MAIN_GUILD_ID", 99)
    bot = MagicMock()
    with patch("utils.guild_logs.send_guild_log", new=AsyncMock()) as log:
        await send_home_guild_log(bot, "Innersync link started", "desc", source="identity")
    log.assert_awaited_once()
    assert log.await_args.args[1] == 99


@pytest.mark.asyncio
async def test_send_home_guild_log_noops_without_main_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    monkeypatch.setattr(config, "MAIN_GUILD_ID", 0)
    bot = MagicMock()
    with patch("utils.guild_logs.send_guild_log", new=AsyncMock()) as log:
        await send_home_guild_log(bot, "Title", "Desc")
    log.assert_awaited_once()
    assert log.await_args.args[1] is None


@pytest.mark.asyncio
async def test_send_guild_log_posts_embed() -> None:
    bot = MagicMock()
    bot.settings.get.return_value = 555
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel

    with patch("utils.guild_logs.should_log_to_discord", return_value=True):
        await send_guild_log(bot, 12, "GDPR agreement accepted", "user accepted", source="gdpr")

    bot.get_channel.assert_called_once_with(555)
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "GDPR agreement accepted"
    assert "user accepted" in embed.description


@pytest.mark.asyncio
async def test_gdpr_button_logs_acceptance() -> None:
    from utils.gdpr_helpers import GDPRButton

    bot = MagicMock()
    button = GDPRButton(bot)
    button.settings = None
    interaction = MagicMock()
    interaction.guild_id = 88
    interaction.guild = MagicMock()
    interaction.user.id = 3
    interaction.user.mention = "<@3>"
    interaction.response.send_message = AsyncMock()

    with (
        patch("utils.gdpr_helpers.store_gdpr_acceptance", new=AsyncMock()) as store,
        patch("utils.gdpr_helpers.send_guild_log", new=AsyncMock()) as log,
    ):
        await button.callback(interaction)

    store.assert_awaited_once_with(3, 88, bot)
    interaction.response.send_message.assert_awaited_once()
    log.assert_awaited_once()
    assert log.await_args.args[2] == "GDPR agreement accepted"
    assert log.await_args.kwargs["source"] == "gdpr"


@pytest.mark.asyncio
async def test_delete_my_data_logs_after_purge() -> None:
    from cogs.delete_my_data import ConfirmDeleteView, DeleteMyDataCog

    cog = MagicMock(spec=DeleteMyDataCog)
    cog.db = MagicMock()
    view = ConfirmDeleteView(user_id=5, cog=cog)
    interaction = MagicMock()
    interaction.user.id = 5
    interaction.user.mention = "<@5>"
    interaction.guild_id = 22
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    with (
        patch("cogs.delete_my_data.is_pool_healthy", return_value=True),
        patch("cogs.delete_my_data._purge_user_data", new=AsyncMock()) as purge,
        patch("cogs.delete_my_data.send_home_guild_log", new=AsyncMock()) as log,
    ):
        await view.confirm.callback(interaction)

    purge.assert_awaited_once()
    log.assert_awaited_once()
    assert log.await_args.args[1] == "User data deleted"
    assert log.await_args.kwargs["source"] == "gdpr"


@pytest.mark.asyncio
async def test_link_slash_logs_session_started() -> None:
    import cogs.innersync_identity as cog

    interaction = MagicMock()
    interaction.user.id = 44
    interaction.user.mention = "<@44>"
    interaction.guild_id = 100
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "_link_rate_ok", return_value=True),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
        patch.object(
            cog,
            "request_discord_link_session",
            new=AsyncMock(return_value={"link_url": "https://app.example/l"}),
        ),
        patch.object(cog, "send_home_guild_log", new=AsyncMock()) as log,
    ):
        await cog.link_slash.callback(interaction)

    log.assert_awaited_once()
    assert log.await_args.args[1] == "Innersync link started"


@pytest.mark.asyncio
async def test_unlink_slash_logs_only_when_deleted() -> None:
    import cogs.innersync_identity as cog

    interaction = MagicMock()
    interaction.user.id = 46
    interaction.user.mention = "<@46>"
    interaction.guild_id = 100
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "request_discord_unlink", new=AsyncMock(return_value=True)),
        patch.object(cog, "send_home_guild_log", new=AsyncMock()) as log,
    ):
        await cog.unlink_slash.callback(interaction)

    log.assert_awaited_once()
    assert log.await_args.args[1] == "Innersync unlinked"
