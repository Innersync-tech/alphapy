from __future__ import annotations

from cogs.agents import _app_agent_home_url


def test_app_agent_home_url_default() -> None:
    assert _app_agent_home_url() == "https://app.innersync.tech/dashboard/agent"


def test_app_agent_home_url_from_config(monkeypatch) -> None:
    monkeypatch.setattr("cogs.agents.config.INNERSYNC_APP_URL", "https://app.innersync.tech/", raising=False)
    assert _app_agent_home_url() == "https://app.innersync.tech/dashboard/agent"


def test_app_agent_home_url_normalizes_typo(monkeypatch) -> None:
    monkeypatch.setattr("cogs.agents.config.INNERSYNC_APP_URL", "https:/app.innersync.tech", raising=False)
    assert _app_agent_home_url() == "https://app.innersync.tech/dashboard/agent"
