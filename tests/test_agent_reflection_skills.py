from __future__ import annotations

import pytest


def _sample_profile(*labels: tuple[str, str]) -> dict:
    insights = []
    for idx, (itype, label) in enumerate(labels):
        insights.append(
            {
                "id": f"id-{idx}",
                "type": itype,
                "label": label,
                "confidence": 0.8,
                "source_reflection_ids": ["ref-1"],
                "consent_epoch": "2026-06-01T00:00:00Z",
                "last_reinforced_at": "2026-06-01T00:00:00Z",
            }
        )
    return {"version": 1, "insights": insights, "active_themes": [], "open_loops": []}


@pytest.mark.asyncio
async def test_inner_critic_dialogue_gather_with_insights_and_voice(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.inner_critic_dialogue import InnerCriticDialogueSkill

    async def _fake_load_prefs(_user_id: str):
        return {"inner_voice": "I get harsh when tired."}

    monkeypatch.setattr("agents.skills.inner_critic_dialogue.load_agent_prefs", _fake_load_prefs)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("theme", "harsh inner critic when stressed")),
    )
    body = await InnerCriticDialogueSkill().gather(ctx)
    assert "Inner critic dialogue" in body
    assert "harsh inner critic" in body
    assert "I get harsh when tired" in body


@pytest.mark.asyncio
async def test_inner_critic_dialogue_gather_empty_without_signals() -> None:
    from agents.base import AgentContext
    from agents.skills.inner_critic_dialogue import InnerCriticDialogueSkill

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile={"version": 1, "insights": [], "active_themes": [], "open_loops": []},
    )
    assert await InnerCriticDialogueSkill().gather(ctx) == ""


def test_avoidance_processor_enabled_requires_avoidance_insight() -> None:
    from agents.base import AgentContext
    from agents.skills.avoidance_processor import AvoidanceProcessorSkill

    skill = AvoidanceProcessorSkill()
    enabled_ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("habit", "avoid difficult conversations")),
    )
    disabled_ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("goal", "finish weekly review")),
    )
    assert skill.enabled(enabled_ctx) is True
    assert skill.enabled(disabled_ctx) is False


@pytest.mark.asyncio
async def test_avoidance_processor_low_energy_branch(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.avoidance_processor import AvoidanceProcessorSkill

    async def _fake_load_prefs(_user_id: str):
        return {"energy_level": "1"}

    monkeypatch.setattr("agents.skills.avoidance_processor.load_agent_prefs", _fake_load_prefs)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("trigger", "avoidance when overwhelmed")),
    )
    body = await AvoidanceProcessorSkill().gather(ctx)
    assert "Soft entry" in body
    assert "avoidance when overwhelmed" in body


@pytest.mark.asyncio
async def test_avoidance_processor_high_energy_branch(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.avoidance_processor import AvoidanceProcessorSkill

    async def _fake_load_prefs(_user_id: str):
        return {"energy_level": "4"}

    monkeypatch.setattr("agents.skills.avoidance_processor.load_agent_prefs", _fake_load_prefs)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("habit", "delay hard tasks until late")),
    )
    body = await AvoidanceProcessorSkill().gather(ctx)
    assert "Higher energy window" in body


def test_chain_breaker_enabled_with_journal_block() -> None:
    from agents.base import AgentContext
    from agents.skills.chain_breaker_micro import ChainBreakerMicroSkill

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        skill_blocks={"journal_sync": "Recent reflections from the user:\nReflection 1"},
    )
    assert ChainBreakerMicroSkill().enabled(ctx) is True


@pytest.mark.asyncio
async def test_chain_breaker_gather_includes_micro_habit_rule() -> None:
    from agents.base import AgentContext
    from agents.skills.chain_breaker_micro import ChainBreakerMicroSkill

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
        derived_profile=_sample_profile(("habit", "suppress feelings until burnout")),
    )
    body = await ChainBreakerMicroSkill().gather(ctx)
    assert "ONE concrete micro-habit" in body
    assert "suppress feelings" in body
