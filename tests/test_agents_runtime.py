from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_agent_unknown() -> None:
    from agents.registry import resolve_agent

    assert resolve_agent("nonexistent") is None


@pytest.mark.asyncio
async def test_resolve_agent_reflection_has_journal_skill() -> None:
    from agents.registry import resolve_agent

    agent = resolve_agent("reflection")
    assert agent is not None
    assert agent.name == "reflection"
    assert any(s.name == "journal_sync" for s in agent.skills)


@pytest.mark.asyncio
async def test_memory_patch_local_backend(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, get_user_memory, patch_user_memory

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert await get_user_memory(user_id, "reflection") == {}

    updated = await patch_user_memory(user_id, "reflection", {"count": 1})
    assert updated["count"] == 1
    assert (await get_user_memory(user_id, "reflection"))["count"] == 1


@pytest.mark.asyncio
async def test_create_and_complete_session_local(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, complete_session, create_session, get_active_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session_id = await create_session(
        innersync_user_id=user_id,
        discord_user_id=123456789,
        guild_id=99,
        agent_name="reflection",
    )
    active = await get_active_session(user_id, "reflection")
    assert active is not None
    assert active["id"] == session_id

    await complete_session(session_id, summary="Done", memory_patch={"ok": True})
    assert await get_active_session(user_id, "reflection") is None


@pytest.mark.asyncio
async def test_run_agent_session_local_memory(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store
    from agents.runtime import run_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    async def _fake_load_reflections(discord_id, limit=5):
        return "Recent reflections from the user:\n\nReflection 1 (2026-06-01):\n  Reflection: Stayed calm"

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        return "You are building a steady reflection habit. Keep going."

    monkeypatch.setattr("agents.skills.journal_sync.load_agent_reflection_context", _fake_load_reflections)
    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)

    result = await run_agent_session(
        innersync_user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )

    assert result.agent_name == "reflection"
    assert "steady reflection" in result.summary
    assert "journal_sync" in result.skill_blocks
