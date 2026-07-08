"""Tests for agents global enable gate."""

from unittest.mock import patch

from cogs.agents import _agents_globally_enabled


def test_agents_globally_enabled_when_config_true():
    with patch("cogs.agents.config") as mock_config:
        mock_config.ALPHAPY_AGENTS_ENABLED = True
        assert _agents_globally_enabled() is True


def test_agents_globally_enabled_when_config_false():
    with patch("cogs.agents.config") as mock_config:
        mock_config.ALPHAPY_AGENTS_ENABLED = False
        assert _agents_globally_enabled() is False


def test_agents_globally_enabled_missing_attr_defaults_false():
    with patch("cogs.agents.config", spec=[]):
        assert _agents_globally_enabled() is False
