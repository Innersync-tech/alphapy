"""Tests for the shared `{scope}.enabled` module contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.settings_helpers import is_module_enabled, is_module_enabled_async
from utils.settings_service import SettingDefinition, SettingsService


class TestIsModuleEnabled:
    def test_default_true_when_unset(self):
        settings = MagicMock()
        settings.get.side_effect = lambda scope, key, guild_id=0, fallback=None: fallback
        bot = SimpleNamespace(settings=settings)
        assert is_module_enabled(bot, 1, "growth") is True
        assert is_module_enabled(bot, 1, "agents") is False

    def test_explicit_false(self):
        helper = MagicMock()
        helper.get_bool.return_value = False
        bot = SimpleNamespace(settings_helper=helper)
        assert is_module_enabled(bot, 42, "growth") is False
        helper.get_bool.assert_called_with("growth", "enabled", 42, fallback=True)

    def test_agents_default_false(self):
        helper = MagicMock()
        helper.get_bool.return_value = False
        bot = SimpleNamespace(settings_helper=helper)
        assert is_module_enabled(bot, 1, "agents") is False
        helper.get_bool.assert_called_with("agents", "enabled", 1, fallback=False)

    def test_settings_service_direct(self):
        settings = MagicMock()
        settings.get.return_value = False
        assert is_module_enabled(settings, 7, "reminders") is False


async def _async_false(*_a, **_k):
    return False


async def _async_true(*_a, **_k):
    return True


@pytest.mark.asyncio
async def test_engagement_master_disables_features(monkeypatch):
    from cogs import engagement as eng

    eng._feature_flag_cache.clear()
    bot = SimpleNamespace(settings=MagicMock())
    bot.settings.get.return_value = True  # feature flag on

    monkeypatch.setattr(eng, "is_module_enabled_async", _async_false)
    assert await eng._is_enabled(bot, 1, "challenges") is False


@pytest.mark.asyncio
async def test_engagement_feature_and_master(monkeypatch):
    from cogs import engagement as eng

    eng._feature_flag_cache.clear()
    bot = SimpleNamespace(settings=MagicMock())
    bot.settings.get.return_value = False  # feature off

    monkeypatch.setattr(eng, "is_module_enabled_async", _async_true)
    assert await eng._is_enabled(bot, 1, "challenges") is False

    eng._feature_flag_cache.clear()
    bot.settings.get.return_value = True
    assert await eng._is_enabled(bot, 1, "challenges") is True


@pytest.mark.asyncio
async def test_is_module_enabled_async_calls_ensure_fresh():
    settings = MagicMock()
    settings.get.side_effect = lambda scope, key, guild_id=0, fallback=None: False
    settings.ensure_fresh = AsyncMock()
    bot = SimpleNamespace(settings=settings)

    assert await is_module_enabled_async(bot, 99, "growth") is False
    settings.ensure_fresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_module_enabled_async_fail_closed_after_retry():
    settings = MagicMock()
    settings.get.return_value = True  # stale memory would say ON
    settings.ensure_fresh = AsyncMock(side_effect=RuntimeError("db down"))
    bot = SimpleNamespace(settings=settings)

    assert await is_module_enabled_async(bot, 99, "growth") is False
    assert settings.ensure_fresh.await_count == 2


@pytest.mark.asyncio
async def test_reload_guild_applies_dashboard_write():
    """Simulate Dashboard writing growth.enabled=false into bot_settings."""
    service = SettingsService(dsn=None)
    service.register(
        SettingDefinition(
            scope="growth",
            key="enabled",
            description="Growth module",
            value_type="bool",
            default=True,
        )
    )
    await service.setup()
    assert service.get("growth", "enabled", 42) is True

    class _FakeConn:
        async def fetch(self, *_args, **_kwargs):
            return [{"scope": "growth", "key": "enabled", "value": False}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _FakePool:
        def acquire(self):
            return _FakeConn()

    service._pool = _FakePool()
    loaded = await service.reload_guild(42)
    assert loaded == 1
    assert service.get("growth", "enabled", 42) is False


@pytest.mark.asyncio
async def test_reload_guild_skips_bad_rows_keeps_good_ones():
    service = SettingsService(dsn=None)
    service.register(
        SettingDefinition(
            scope="growth",
            key="enabled",
            description="Growth module",
            value_type="bool",
            default=True,
        )
    )
    service.register(
        SettingDefinition(
            scope="growth",
            key="log_channel_id",
            description="Channel",
            value_type="channel",
            default=0,
        )
    )
    await service.setup()

    class _FakeConn:
        async def fetch(self, *_args, **_kwargs):
            return [
                {"scope": "growth", "key": "enabled", "value": False},
                {"scope": "growth", "key": "log_channel_id", "value": "not-an-int"},
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _FakePool:
        def acquire(self):
            return _FakeConn()

    service._pool = _FakePool()
    loaded = await service.reload_guild(7)
    assert loaded == 1
    assert service.get("growth", "enabled", 7) is False
    # Bad channel row skipped → default, not a half-cleared crash
    assert service.get("growth", "log_channel_id", 7) == 0
