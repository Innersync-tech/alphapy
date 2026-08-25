from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.engagement import (
    EngagementCog,
    _get_food_channel_ids,
    _invalidate_engagement_cache,
    _is_enabled,
    get_engagement_cache_stats,
)
from utils.automod_rules import RuleProcessor
from utils.premium_guard import (
    _get_cached_guild,
    _set_guild_cache,
    guild_has_premium,
    invalidate_premium_cache,
)


class _FakeSettings:
    def __init__(self) -> None:
        self.calls = 0
        self.listeners = []
        self.values = {
            ("engagement", "enabled", 123): True,
            ("engagement", "challenges_enabled", 123): True,
            ("engagement", "weekly_food_channel_ids", 123): "10, 20, nope, 30",
        }

    def get(self, scope: str, key: str, guild_id: int = 0, fallback=None):
        self.calls += 1
        return self.values.get((scope, key, guild_id), fallback)

    def add_global_listener(self, listener) -> None:
        self.listeners.append(listener)

    def add_reload_listener(self, listener) -> None:
        self.listeners.append(listener)


@pytest.mark.asyncio
async def test_engagement_feature_flag_and_food_channel_cache() -> None:
    settings = _FakeSettings()
    bot = SimpleNamespace(settings=settings)

    # First call reads master + feature; second call should hit TTL cache.
    assert await _is_enabled(bot, 123, "challenges") is True
    assert await _is_enabled(bot, 123, "challenges") is True
    assert settings.calls == 2

    ids = await _get_food_channel_ids(bot, 123)
    assert ids == {10, 20, 30}
    ids_cached = await _get_food_channel_ids(bot, 123)
    assert ids_cached == {10, 20, 30}
    assert settings.calls == 3

    # Simulate settings update event and ensure cache invalidates.
    _invalidate_engagement_cache("engagement", "challenges_enabled", 123)
    _invalidate_engagement_cache("engagement", "weekly_food_channel_ids", 123)
    assert await _is_enabled(bot, 123, "challenges") is True
    assert await _get_food_channel_ids(bot, 123) == {10, 20, 30}
    assert settings.calls == 6

    stats = get_engagement_cache_stats()
    assert stats["engagement_feature_flag_cache_hits"] >= 1
    assert stats["engagement_food_channels_cache_hits"] >= 1


def test_engagement_cog_registers_global_listener() -> None:
    class _Tree:
        def add_command(self, _cmd) -> None:
            return None

        def remove_command(self, _name: str) -> None:
            return None

    settings = _FakeSettings()
    bot = SimpleNamespace(tree=_Tree(), settings=settings)
    EngagementCog(bot)  # should register listener without errors
    assert len(settings.listeners) == 1


@pytest.mark.asyncio
async def test_guild_has_premium_uses_ttl_cache(monkeypatch) -> None:
    # Prime and validate private guild cache helpers.
    _set_guild_cache(555, True)
    assert _get_cached_guild(555) is True
    invalidate_premium_cache(1, 555)
    assert _get_cached_guild(555) is None

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"?column?": 1})

    @asynccontextmanager
    async def _fake_acquire_safe(_pool):
        yield mock_conn

    monkeypatch.setattr("utils.premium_guard.acquire_safe", _fake_acquire_safe)
    monkeypatch.setattr("utils.premium_guard._ensure_pool", AsyncMock(return_value=object()))

    assert await guild_has_premium(777) is True
    assert await guild_has_premium(777) is True
    # DB query runs once; second call hits guild cache.
    assert mock_conn.fetchrow.await_count == 1


@pytest.mark.asyncio
async def test_automod_list_rules_cache_and_create_rule_invalidation(monkeypatch) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "id": 1,
            "guild_id": 9,
            "rule_type": "spam",
            "name": "r1",
            "enabled": True,
            "config": {},
            "is_premium": False,
            "created_at": None,
            "action_type": "warn",
        }
    ]
    conn.fetchval.side_effect = [321, 654]  # action_id, rule_id

    @asynccontextmanager
    async def _fake_acquire_safe(_pool):
        yield conn

    monkeypatch.setattr("utils.automod_rules.acquire_safe", _fake_acquire_safe)
    bot = SimpleNamespace(settings=SimpleNamespace(_pool=object()))
    processor = RuleProcessor(bot=bot)

    first = await processor.list_rules(9)
    second = await processor.list_rules(9)
    assert first[0]["id"] == 1
    assert second[0]["id"] == 1
    assert conn.fetch.await_count == 1

    processor._cache_updated[9] = 123.0
    processor._list_cache_updated[9] = 123.0
    await processor.create_rule(
        guild_id=9,
        rule_type="spam",
        name="new",
        config={},
        action_type="warn",
        action_config={},
        created_by=1,
        is_premium=False,
    )
    assert 9 not in processor._cache_updated
    assert 9 not in processor._list_cache_updated


def test_collect_cache_metrics_and_premium_metrics(monkeypatch) -> None:
    import api_metrics as metrics_module

    fake_rule_processor = SimpleNamespace(
        get_cache_stats=lambda: {
            "automod_rules_cache_size": 1,
            "automod_rules_list_cache_size": 2,
            "automod_rules_cache_hits": 3,
            "automod_rules_cache_misses": 4,
        }
    )
    fake_bot = SimpleNamespace(get_cog=lambda _name: SimpleNamespace(rule_processor=fake_rule_processor))
    monkeypatch.setattr("gpt.helpers.bot_instance", fake_bot, raising=False)
    monkeypatch.setattr(
        "cogs.engagement.get_engagement_cache_stats",
        lambda: {
            "engagement_feature_flag_cache_size": 5,
            "engagement_food_channels_cache_size": 6,
            "engagement_feature_flag_cache_hits": 7,
            "engagement_feature_flag_cache_misses": 8,
            "engagement_food_channels_cache_hits": 9,
            "engagement_food_channels_cache_misses": 10,
        },
    )
    monkeypatch.setattr(
        "utils.premium_guard.get_premium_guard_stats",
        lambda: {
            "premium_checks_total": 11,
            "premium_checks_core_api": 12,
            "premium_checks_local": 13,
            "premium_cache_hits": 14,
            "premium_transfers_count": 15,
            "premium_cache_size": 16,
            "premium_guild_cache_size": 17,
            "premium_guild_cache_hits": 18,
            "premium_guild_cache_misses": 19,
        },
    )

    cache_metrics = metrics_module._collect_cache_metrics()
    assert cache_metrics.automod_rules_cache_hits == 3
    assert cache_metrics.engagement_food_channels_cache_misses == 10

    premium_metrics = metrics_module._collect_premium_metrics()
    assert premium_metrics is not None
    assert premium_metrics.premium_guild_cache_hits == 18
