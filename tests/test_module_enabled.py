"""Tests for the shared `{scope}.enabled` module contract."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from utils.settings_helpers import is_module_enabled


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


@pytest.mark.asyncio
async def test_engagement_master_disables_features(monkeypatch):
    from cogs import engagement as eng

    eng._feature_flag_cache.clear()
    bot = SimpleNamespace(settings=MagicMock())
    bot.settings.get.return_value = True  # feature flag on

    monkeypatch.setattr(eng, "is_module_enabled", lambda *a, **k: False)
    assert await eng._is_enabled(bot, 1, "challenges") is False


@pytest.mark.asyncio
async def test_engagement_feature_and_master(monkeypatch):
    from cogs import engagement as eng

    eng._feature_flag_cache.clear()
    bot = SimpleNamespace(settings=MagicMock())
    bot.settings.get.return_value = False  # feature off

    monkeypatch.setattr(eng, "is_module_enabled", lambda *a, **k: True)
    assert await eng._is_enabled(bot, 1, "challenges") is False

    eng._feature_flag_cache.clear()
    bot.settings.get.return_value = True
    assert await eng._is_enabled(bot, 1, "challenges") is True
