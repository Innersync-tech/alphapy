from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_inner_voice_skill_returns_user_text(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.inner_voice import InnerVoiceSkill

    async def _fake_load_prefs(_user_id: str):
        return {"inner_voice": "  I get harsh with myself when tired.  "}

    monkeypatch.setattr("agents.skills.inner_voice.load_agent_prefs", _fake_load_prefs)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
    )
    body = await InnerVoiceSkill().gather(ctx)
    assert "harsh with myself when tired" in body
    assert "User-described inner voice" in body


@pytest.mark.asyncio
async def test_inner_voice_skill_empty_prefs_message(monkeypatch) -> None:
    from agents.base import AgentContext
    from agents.skills.inner_voice import InnerVoiceSkill, NO_INNER_VOICE_MESSAGE

    async def _fake_load_prefs(_user_id: str):
        return {}

    monkeypatch.setattr("agents.skills.inner_voice.load_agent_prefs", _fake_load_prefs)

    ctx = AgentContext(
        innersync_user_id="user-1",
        discord_user_id=1,
        guild_id=2,
        agent_name="reflection",
    )
    assert await InnerVoiceSkill().gather(ctx) == NO_INNER_VOICE_MESSAGE


def test_normalize_agent_prefs_inner_voice_truncates() -> None:
    from agents.profile import normalize_agent_prefs

    prefs = normalize_agent_prefs({"inner_voice": "x" * 500})
    assert len(prefs["inner_voice"]) == 400
