import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from utils.automod_logging import AutoModLogger


def _make_bot(log_channel_id: int = 999):
    log_channel = MagicMock(spec=discord.TextChannel)
    log_channel.send = AsyncMock()

    user = MagicMock()
    user.mention = "<@42>"

    source_channel = MagicMock(spec=discord.TextChannel)
    source_channel.mention = "<#100>"

    settings = MagicMock(spec=["get"])
    settings.get.return_value = log_channel_id

    bot = MagicMock()
    bot.settings = settings

    def get_channel(channel_id: int):
        if channel_id == log_channel_id:
            return log_channel
        if channel_id == 100:
            return source_channel
        return None

    bot.get_channel = MagicMock(side_effect=get_channel)
    bot.get_user = MagicMock(return_value=user)
    return bot, log_channel


def test_log_violation_forwards_rule_name_to_discord_logger():
    async def run() -> None:
        bot, _ = _make_bot()
        logger = AutoModLogger(bot=bot)
        logger._log_to_discord_channel = AsyncMock()  # type: ignore[method-assign]

        await logger.log_violation(
            guild_id=1,
            user_id=42,
            message_id=10,
            channel_id=100,
            rule_id=4,
            action_type="warn",
            message_content="bad message",
            rule_name="pompen regel",
        )

        logger._log_to_discord_channel.assert_awaited_once_with(
            1, 42, "warn", 4, "bad message", 100, "pompen regel"
        )

    asyncio.run(run())


def test_discord_log_embed_shows_rule_name_and_db_id_footer():
    async def run() -> None:
        bot, log_channel = _make_bot()
        logger = AutoModLogger(bot=bot)

        await logger._log_to_discord_channel(
            guild_id=1,
            user_id=42,
            action_type="delete",
            rule_id=4,
            message_content="spam here",
            channel_id=100,
            rule_name="pompen regel",
        )

        log_channel.send.assert_awaited_once()
        embed = log_channel.send.await_args.args[0]
        assert isinstance(embed, discord.Embed)
        fields = {field.name: field.value for field in embed.fields}
        assert fields["Rule"] == "pompen regel"
        assert embed.footer.text == "Guild ID: 1 · db #4"

    asyncio.run(run())


def test_discord_log_embed_falls_back_to_rule_id_without_name():
    async def run() -> None:
        bot, log_channel = _make_bot()
        logger = AutoModLogger(bot=bot)

        await logger._log_to_discord_channel(
            guild_id=1,
            user_id=42,
            action_type="warn",
            rule_id=7,
            message_content=None,
            channel_id=100,
            rule_name=None,
        )

        embed = log_channel.send.await_args.args[0]
        fields = {field.name: field.value for field in embed.fields}
        assert fields["Rule"] == "Rule #7"
        assert embed.footer.text == "Guild ID: 1 · db #7"

    asyncio.run(run())
