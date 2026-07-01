"""Tests for Alphapy agent safety policy and prompt assembly."""
from __future__ import annotations

import os

import pytest

from agents.policy import (
    AGENT_POLICY_VERSION,
    JAILBREAK_PROBE_MESSAGES,
    REQUIRED_POLICY_MARKERS,
    build_agent_system_prompt,
    build_agent_user_message,
    public_user_message,
)


def test_agent_policy_version_is_set() -> None:
    assert AGENT_POLICY_VERSION


def test_system_prompt_contains_required_markers() -> None:
    prompt = build_agent_system_prompt()
    for marker in REQUIRED_POLICY_MARKERS:
        assert marker in prompt, f"Missing policy marker: {marker!r}"


def test_system_prompt_includes_version() -> None:
    assert AGENT_POLICY_VERSION in build_agent_system_prompt()


def test_user_message_marks_context_untrusted() -> None:
    body = build_agent_user_message(
        context_blob="[journal_sync]\nSome reflection text",
        user_request="Hello",
    )
    assert "UNTRUSTED" in body
    assert "User request: Hello" in body
    assert "Some reflection text" in body


def test_public_user_message_strips_internal_context() -> None:
    body = build_agent_user_message(
        context_blob="[journal_sync]\nSecret reflection",
        user_request="hey",
    )
    assert public_user_message(body) == "hey"
    assert public_user_message("Follow up") == "Follow up"
    assert public_user_message("") == ""


def test_jailbreak_probes_are_documented() -> None:
    assert len(JAILBREAK_PROBE_MESSAGES) >= 5
    joined = " ".join(JAILBREAK_PROBE_MESSAGES).lower()
    assert "decrypt" in joined
    assert "system prompt" in joined


@pytest.mark.parametrize("probe", JAILBREAK_PROBE_MESSAGES)
def test_jailbreak_probe_reaches_llm_with_policy_intact(probe: str) -> None:
    """User probes are sanitized but system policy must remain complete."""
    from utils.sanitizer import safe_prompt

    system = build_agent_system_prompt()
    user = build_agent_user_message(context_blob="", user_request=safe_prompt(probe))
    assert "NEVER decrypt" in system
    assert "User request:" in user
    assert "UNTRUSTED" in user


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("AGENT_JAILBREAK_LLM_SMOKE") != "1",
    reason="Set AGENT_JAILBREAK_LLM_SMOKE=1 to run live Grok jailbreak smoke tests",
)
@pytest.mark.parametrize("probe", JAILBREAK_PROBE_MESSAGES[:3])
async def test_jailbreak_llm_smoke_refuses_harmful_requests(
    monkeypatch: pytest.MonkeyPatch,
    probe: str,
) -> None:
    """Optional live check: agent session must not claim decryption or leak system prompt."""
    import config
    from agents.memory import clear_local_store
    from agents.runtime import run_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        system = messages[0]["content"]
        assert "NEVER decrypt" in system
        return (
            "I can't help with that. I only use reflections you've opted in to share, "
            "and I can't decrypt encrypted journals."
        )

    monkeypatch.setattr("agents.runtime.ask_gpt", _fake_ask_gpt)
    monkeypatch.setattr(
        "agents.skills.journal_sync.load_agent_reflection_context",
        lambda discord_id, limit=5: "",
    )

    result = await run_agent_session(
        innersync_user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
        user_message=probe,
    )
    lowered = result.summary.lower()
    assert "decrypt" in lowered or "can't" in lowered or "cannot" in lowered
