import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from utils.automod_rules import RuleProcessor, RuleType


class DummyMessage:
    def __init__(self, content: str, mentions: int = 0):
        self.content = content
        self.mentions = [object() for _ in range(mentions)]


def test_evaluators_caps_and_mentions():
    async def run() -> None:
        processor = RuleProcessor(bot=None)

        caps_rule = {
            "id": 1,
            "rule_type": RuleType.SPAM.value,
            "config": {
                "spam_type": "caps",
                "min_length": 10,
                "max_caps_ratio": 0.7,
            },
        }
        caps_msg = DummyMessage("THIS IS ALMOST ALL CAPS")
        caps_result = await processor.evaluate_rule(caps_rule, cast(discord.Message, caps_msg), {})
        assert caps_result.triggered is True

        mentions_rule = {
            "id": 2,
            "rule_type": RuleType.CONTENT.value,
            "config": {
                "content_type": "mentions",
                "max_mentions": 2,
            },
        }
        mentions_msg = DummyMessage("hello", mentions=4)
        mentions_result = await processor.evaluate_rule(mentions_rule, cast(discord.Message, mentions_msg), {})
        assert mentions_result.triggered is True

    asyncio.run(run())


def test_rule_crud_with_mocked_db(monkeypatch):
    async def run() -> None:
        conn = AsyncMock()

        # create_rule: first fetchval -> action_id, second fetchval -> rule_id
        conn.fetchval.side_effect = [101, 202]

        # list_rules
        conn.fetch.return_value = [
            {
                "id": 202,
                "guild_id": 1,
                "rule_type": "spam",
                "name": "Rule A",
                "enabled": True,
                "config": {"spam_type": "frequency", "max_messages": 5, "time_window": 60},
                "is_premium": False,
                "created_at": None,
                "action_type": "warn",
            }
        ]

        # update/delete lookups
        conn.fetchrow.side_effect = [
            {"id": 202, "action_id": 101},  # update_rule lookup
            {"action_id": 101},               # delete_rule lookup
        ]

        pool = object()

        @asynccontextmanager
        async def fake_acquire_safe(_pool):
            assert _pool is pool
            yield conn

        monkeypatch.setattr("utils.automod_rules.acquire_safe", fake_acquire_safe)

        # get_bot_db_pool(bot) reads bot.settings._pool, not bot.db_pool
        bot = SimpleNamespace(settings=SimpleNamespace(_pool=pool))
        processor = RuleProcessor(bot=cast(commands.Bot, bot))

        # Ensure cache invalidation paths are covered
        processor._rules_cache[1] = [{"id": 999}]
        processor._cache_updated[1] = 123.0

        created_id = await processor.create_rule(
            guild_id=1,
            rule_type="spam",
            name="Rule A",
            config={"spam_type": "frequency", "max_messages": 5, "time_window": 60},
            action_type="warn",
            action_config={},
            created_by=42,
            is_premium=False,
        )
        assert created_id == 202
        assert 1 not in processor._rules_cache

        rows = await processor.list_rules(1)
        assert len(rows) == 1
        assert rows[0]["id"] == 202

        updated = await processor.update_rule(
            guild_id=1,
            rule_id=202,
            name="Rule A Updated",
            enabled=False,
            action_type="delete",
            config={"spam_type": "frequency", "max_messages": 4, "time_window": 30},
            action_config={"reason": "updated"},
        )
        assert updated is True

        deleted = await processor.delete_rule(guild_id=1, rule_id=202)
        assert deleted is True

        # At least one execute call happened for update and delete operations
        assert conn.execute.await_count >= 4

    asyncio.run(run())


def test_content_rule_accepts_dashboard_word_string():
    async def run() -> None:
        processor = RuleProcessor(bot=None)
        rule = {
            "id": 3,
            "rule_type": RuleType.CONTENT.value,
            "config": {"content_type": "bad_words", "words": "try"},
        }
        hit = await processor.evaluate_rule(rule, cast(discord.Message, DummyMessage("please try this")), {})
        miss = await processor.evaluate_rule(rule, cast(discord.Message, DummyMessage("hello")), {})
        assert hit.triggered is True
        assert miss.triggered is False

    asyncio.run(run())


def test_dashboard_legacy_rule_types():
    async def run() -> None:
        processor = RuleProcessor(bot=None)

        mentions_rule = {"id": 4, "rule_type": "mentions", "config": {"max_mentions": 2}}
        mentions_msg = DummyMessage("hello", mentions=4)
        mentions_result = await processor.evaluate_rule(
            mentions_rule, cast(discord.Message, mentions_msg), {}
        )
        assert mentions_result.triggered is True

        caps_rule = {"id": 5, "rule_type": "caps", "config": {}}
        caps_result = await processor.evaluate_rule(
            caps_rule, cast(discord.Message, DummyMessage("THIS IS ALMOST ALL CAPS")), {}
        )
        assert caps_result.triggered is True

    asyncio.run(run())
