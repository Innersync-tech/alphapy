from __future__ import annotations

import pytest

from agents.telemetry import (
    AgentSessionMetrics,
    collect_agent_session_metrics,
    format_agent_session_telemetry_notes,
)


def test_format_agent_session_telemetry_notes_disabled() -> None:
    metrics = AgentSessionMetrics(agents_enabled=False)
    assert format_agent_session_telemetry_notes(metrics) == "agents: disabled"


def test_format_agent_session_telemetry_notes_with_activity() -> None:
    metrics = AgentSessionMetrics(
        agents_enabled=True,
        active_sessions=2,
        started_24h=5,
        completed_24h=4,
        active_origin_discord=1,
        active_origin_app=1,
    )
    text = format_agent_session_telemetry_notes(metrics)
    assert text.startswith("agents:")
    assert "2 active" in text
    assert "5 started/24h" in text
    assert "origin discord:1 app:1" in text


@pytest.mark.asyncio
async def test_collect_agent_session_metrics_local_backend(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, create_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_id = await create_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
        metadata={"origin_channel": "discord", "last_channel": "discord"},
    )

    from agents.memory import _local_sessions

    _local_sessions[session_id]["status"] = "active"

    metrics = await collect_agent_session_metrics()
    assert metrics.agents_enabled is True
    assert metrics.active_sessions == 1
    assert metrics.started_24h >= 1
    assert metrics.active_origin_discord == 1


@pytest.mark.asyncio
async def test_collect_agent_session_metrics_when_disabled(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_ENABLED", False)
    metrics = await collect_agent_session_metrics()
    assert metrics.agents_enabled is False
    assert metrics.active_sessions == 0
