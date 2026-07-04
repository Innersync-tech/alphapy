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
    assert any(s.name == "inner_voice" for s in agent.skills)
    assert any(s.name == "inner_critic_dialogue" for s in agent.skills)
    assert any(s.name == "avoidance_processor" for s in agent.skills)
    assert any(s.name == "fatigue_check" for s in agent.skills)
    assert any(s.name == "chain_breaker_micro" for s in agent.skills)
    assert any(s.name == "journal_sync" for s in agent.skills)


@pytest.mark.asyncio
async def test_memory_patch_local_backend(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, get_user_memory, patch_user_memory

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert await get_user_memory(user_id, "reflection") == {}

    updated = await patch_user_memory(user_id, "reflection", {"session_count": 1, "last_agent": "reflection"})
    assert updated["session_count"] == 1
    assert (await get_user_memory(user_id, "reflection"))["session_count"] == 1


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
    from agents.memory import clear_local_store, get_user_memory
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

    stored = await get_user_memory("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "reflection")
    assert "last_summary_preview" not in stored
    assert stored.get("session_count") == 1
    assert "last_session_at" in stored


@pytest.mark.asyncio
async def test_run_agent_session_includes_agent_profile_prefs(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store
    from agents.runtime import run_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    async def _fake_load_reflections(discord_id, limit=5):
        return ""

    async def _fake_load_prefs(user_id):
        return {"display_name": "Nova", "persona": "calm", "default_focus": "boundaries"}

    captured_messages: list[list[dict]] = []

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        captured_messages.append(messages)
        return "Hello Nova."

    monkeypatch.setattr("agents.skills.journal_sync.load_agent_reflection_context", _fake_load_reflections)
    monkeypatch.setattr("agents.runtime.load_agent_prefs", _fake_load_prefs)
    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)

    await run_agent_session(
        innersync_user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )

    user_content = captured_messages[0][1]["content"]
    assert "[agent_profile]" in user_content
    assert "Nova" in user_content
    assert "boundaries" in user_content


@pytest.mark.asyncio
async def test_run_agent_session_clears_stale_memory_without_consent(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, get_user_memory, patch_user_memory
    from agents.runtime import run_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    await patch_user_memory(
        user_id,
        "reflection",
        {
            "last_summary_preview": "Mantra: FUCK from June 18",
            "session_count": 3,
        },
    )

    async def _fake_load_reflections(discord_id, limit=5):
        return ""

    captured_messages: list[list[dict]] = []

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        captured_messages.append(messages)
        return "No shared reflections yet."

    monkeypatch.setattr("agents.skills.journal_sync.load_agent_reflection_context", _fake_load_reflections)
    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)

    await run_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )

    user_content = captured_messages[0][1]["content"]
    assert "FUCK" not in user_content
    assert "[memory]" not in user_content
    assert "Mantra" not in user_content

    stored = await get_user_memory(user_id, "reflection")
    assert "last_summary_preview" not in stored
    assert stored.get("session_count") == 4
    assert "last_session_at" in stored


@pytest.mark.asyncio
async def test_multi_turn_session_start_continue_end(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, get_active_session, get_session_messages, get_user_memory
    from agents.runtime import (
        ActiveAgentSessionError,
        continue_agent_session,
        end_agent_session,
        start_agent_session,
    )

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    responses = iter(["First reply.", "Second reply."])

    async def _fake_load_reflections(discord_id, limit=5):
        return ""

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        return next(responses)

    monkeypatch.setattr("agents.skills.journal_sync.load_agent_reflection_context", _fake_load_reflections)
    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)

    first = await start_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
        user_message="Hello",
    )
    assert first.summary == "First reply."
    assert first.turn_count == 1
    assert await get_active_session(user_id, "reflection") is not None

    stored_turns = await get_session_messages(first.session_id)
    user_rows = [row for row in stored_turns if row.get("role") == "user"]
    assert user_rows
    assert user_rows[0]["content"] == "Hello"
    assert "UNTRUSTED" not in user_rows[0]["content"]
    assert "[journal_sync]" not in user_rows[0]["content"]

    with pytest.raises(ActiveAgentSessionError):
        await start_agent_session(
            innersync_user_id=user_id,
            discord_user_id=42,
            guild_id=1,
            agent_name="reflection",
        )

    second = await continue_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
        user_message="Follow up",
    )
    assert second.summary == "Second reply."
    assert second.turn_count == 2

    ended = await end_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )
    assert ended.turn_count == 2
    assert await get_active_session(user_id, "reflection") is None

    stored = await get_user_memory(user_id, "reflection")
    assert stored.get("session_count") == 1


@pytest.mark.asyncio
async def test_end_agent_session_stores_insight_snapshot(monkeypatch) -> None:
    import config
    from agents.memory import (
        clear_local_store,
        get_session_by_id,
        get_user_memory,
        patch_user_memory,
    )
    from agents.runtime import end_agent_session, start_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    await patch_user_memory(
        user_id,
        "reflection",
        {
            "derived_profile": {
                "version": 1,
                "insights": [
                    {
                        "id": "seed",
                        "type": "theme",
                        "label": "gentle morning routine",
                        "confidence": 0.6,
                        "source_reflection_ids": ["ref-1"],
                        "consent_epoch": "2026-06-01T00:00:00Z",
                        "last_reinforced_at": "2026-06-01T00:00:00Z",
                    }
                ],
                "active_themes": [],
                "open_loops": [],
            }
        },
    )

    async def _fake_load_reflections(discord_id, limit=5):
        return "Recent reflections from the user:\nReflection 1:\n  Reflection: Stayed calm"

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        return "Session ended."

    async def _fake_distill(*args, **kwargs):
        from agents.tier2 import normalize_derived_profile

        return normalize_derived_profile(
            {
                "insights": [
                    {
                        "id": "seed",
                        "type": "theme",
                        "label": "gentle morning routine",
                        "confidence": 0.85,
                        "source_reflection_ids": ["ref-1"],
                        "consent_epoch": "2026-06-02T00:00:00Z",
                        "last_reinforced_at": "2026-06-02T00:00:00Z",
                    },
                    {
                        "id": "new-habit",
                        "type": "habit",
                        "label": "single breath before replying",
                        "confidence": 0.75,
                        "source_reflection_ids": ["ref-1"],
                        "consent_epoch": "2026-06-02T00:00:00Z",
                        "last_reinforced_at": "2026-06-02T00:00:00Z",
                    },
                ]
            }
        )

    async def _fake_consent_ids(_user_id: str):
        return frozenset({"ref-1"})

    async def _fake_load_prefs(_user_id: str):
        return {"learn_from_shared": True}

    monkeypatch.setattr("agents.skills.journal_sync.load_agent_reflection_context", _fake_load_reflections)
    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)
    monkeypatch.setattr("agents.runtime.distill_session_profile", _fake_distill)
    monkeypatch.setattr("agents.runtime._fetch_active_consent_reflection_ids", _fake_consent_ids)
    monkeypatch.setattr("agents.runtime.load_agent_prefs", _fake_load_prefs)

    started = await start_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
        user_message="Hello",
    )

    ended = await end_agent_session(
        innersync_user_id=user_id,
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )
    assert ended.session_id == started.session_id

    session_row = await get_session_by_id(started.session_id)
    assert session_row is not None
    patch = session_row.get("memory_patch") or {}
    snapshot = patch.get("session_insight_snapshot")
    assert isinstance(snapshot, list)
    assert len(snapshot) >= 1
    assert any(item.get("id") == "new-habit" for item in snapshot)

    stored = await get_user_memory(user_id, "reflection")
    assert stored.get("session_count") == 1
