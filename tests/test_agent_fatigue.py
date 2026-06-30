from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.fatigue import (
    ENERGY_LEVEL_LABELS,
    format_fatigue_context,
    needs_fatigue_prompt,
    parse_fatigue_reported_at,
)


def test_parse_fatigue_reported_at_accepts_zulu() -> None:
    parsed = parse_fatigue_reported_at("2026-06-30T12:00:00Z")
    assert parsed is not None
    assert parsed.year == 2026


def test_needs_fatigue_prompt_when_missing_level() -> None:
    assert needs_fatigue_prompt({}) is True


def test_needs_fatigue_prompt_when_stale() -> None:
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    assert needs_fatigue_prompt({"energy_level": "3", "fatigue_reported_at": old}) is True


def test_needs_fatigue_prompt_when_fresh() -> None:
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert needs_fatigue_prompt({"energy_level": "4", "fatigue_reported_at": recent}) is False


def test_format_fatigue_context_includes_level_and_note() -> None:
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    body = format_fatigue_context(
        {
            "energy_level": "2",
            "fatigue_note": "short night",
            "fatigue_reported_at": recent,
        }
    )
    assert "2/5" in body
    assert ENERGY_LEVEL_LABELS["2"] in body
    assert "short night" in body


@pytest.mark.asyncio
async def test_fatigue_check_skill_gather(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.fatigue_check import FatigueCheckSkill

    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    async def _fake_load(_user_id: str):
        return {
            "energy_level": "5",
            "fatigue_reported_at": recent,
        }

    monkeypatch.setattr("agents.skills.fatigue_check.load_agent_prefs", _fake_load)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
    )
    body = await FatigueCheckSkill().gather(ctx)
    assert "5/5" in body
