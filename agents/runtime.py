"""Agent session orchestration."""
from __future__ import annotations

import logging
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.channels import AgentChannel, merge_channel_metadata
from agents.memory import (
    append_session_messages,
    complete_session,
    create_session,
    delete_session_messages,
    get_active_session,
    get_session_messages,
    get_user_memory,
    patch_session_metadata,
    patch_user_memory,
    strip_sensitive_memory_keys,
    touch_session,
)
from agents.policy import (
    build_agent_system_prompt,
    build_agent_user_message,
    public_user_message,
)
from agents.profile import (
    build_agent_profile_block,
    extract_tier3_memory,
    learn_from_shared_enabled,
    load_agent_prefs,
    tier3_memory_patch,
)
from agents.registry import resolve_agent
from agents.tier2 import (
    TIER2_ROOT_KEY,
    distill_session_profile,
    extract_derived_profile,
    session_summary_from_profile,
)
from gpt.context_loader import _fetch_active_consent_reflection_ids
from gpt.helpers import ask_gpt
from utils.sanitizer import safe_prompt

logger = logging.getLogger("alphapy.agents.runtime")


class ActiveAgentSessionError(ValueError):
    """Raised when starting a session while one is already active."""


class NoActiveAgentSessionError(ValueError):
    """Raised when continuing or ending without an active session."""


class AgentSessionQuotaExceededError(ValueError):
    """Raised when the user has reached their daily /agent start limit."""

    def __init__(self, count: int, limit: int) -> None:
        self.count = count
        self.limit = limit
        super().__init__(f"Daily agent session limit reached ({count}/{limit})")


async def _build_skill_context(ctx: AgentContext) -> dict[str, str]:
    agent = resolve_agent(ctx.agent_name)
    if agent is None:
        return {}

    blocks: dict[str, str] = {}
    for skill in agent.skills:
        if not skill.enabled(ctx):
            continue
        try:
            body = await skill.gather(ctx)
            if body.strip():
                blocks[skill.name] = body.strip()
        except Exception as exc:
            logger.warning("Skill %s gather failed: %s", skill.name, exc)
            blocks[skill.name] = f"(unavailable: {type(exc).__name__})"
    return blocks


def _assemble_prompt(
    skill_blocks: dict[str, str],
    *,
    prefs: dict[str, str | bool],
    tier3: dict[str, Any],
    derived_profile: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    profile_block = build_agent_profile_block(
        prefs,
        tier3,
        derived_profile=derived_profile,
    )
    if profile_block.strip():
        parts.append("[agent_profile]\n" + safe_prompt(profile_block[:1500]))
    for name, body in sorted(skill_blocks.items()):
        parts.append(f"[{name}]\n{safe_prompt(body[:2500])}")
    return "\n\n".join(parts)


async def _load_durable_state(
    innersync_user_id: str,
    agent_name: str,
) -> tuple[dict[str, str | bool], dict[str, Any], dict[str, Any], int]:
    raw_memory = await get_user_memory(innersync_user_id, agent_name)
    cleaned_memory = strip_sensitive_memory_keys(raw_memory)
    tier3 = extract_tier3_memory(cleaned_memory)
    derived_profile = extract_derived_profile(cleaned_memory)
    prefs = await load_agent_prefs(innersync_user_id)
    prior_session_count = int(tier3.get("session_count", 0))
    return prefs, tier3, derived_profile, prior_session_count


def _build_llm_messages(
    *,
    context_blob: str,
    user_request: str,
    prior_turns: list[dict[str, Any]],
    include_context: bool,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_agent_system_prompt()},
    ]
    for turn in prior_turns:
        role = str(turn.get("role", ""))
        content = str(turn.get("content", ""))
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    if include_context:
        user_content = build_agent_user_message(
            context_blob=context_blob,
            user_request=safe_prompt(user_request[:2000]),
        )
    else:
        user_content = safe_prompt(user_request[:2000])

    messages.append({"role": "user", "content": user_content})
    return messages


def _transcript_from_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    for row in messages:
        role = str(row.get("role", ""))
        content = str(row.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            user_parts.append(public_user_message(content))
        elif role == "assistant":
            assistant_parts.append(content)
    return "\n---\n".join(user_parts)[:2500], "\n---\n".join(assistant_parts)[:2500]


async def _run_agent_turn(
    *,
    ctx: AgentContext,
    prefs: dict[str, str | bool],
    tier3: dict[str, Any],
    derived_profile: dict[str, Any],
    user_message: str,
    prior_turns: list[dict[str, Any]],
    include_context: bool,
) -> tuple[str, dict[str, str], str]:
    skill_blocks = await _build_skill_context(ctx)
    ctx.skill_blocks = skill_blocks
    context_blob = _assemble_prompt(
        skill_blocks,
        prefs=prefs,
        tier3=tier3,
        derived_profile=derived_profile,
    )
    messages = _build_llm_messages(
        context_blob=context_blob,
        user_request=user_message,
        prior_turns=prior_turns,
        include_context=include_context,
    )
    summary = await ask_gpt(
        messages,
        user_id=ctx.discord_user_id,
        guild_id=ctx.guild_id,
        include_reflections=False,
    )
    if not summary:
        summary = "I could not generate a response right now. Please try again shortly."

    stored_user = safe_prompt(user_message[:2000])
    return summary, skill_blocks, stored_user


async def _execute_agent_skills(ctx: AgentContext) -> None:
    agent = resolve_agent(ctx.agent_name)
    if agent is None:
        return
    for skill in agent.skills:
        if not skill.enabled(ctx):
            continue
        try:
            await skill.execute(ctx)
        except Exception as exc:
            logger.warning("Skill %s execute failed: %s", skill.name, exc)


def _result_from_turn(
    *,
    agent_name: str,
    session_id: str,
    summary: str,
    skill_blocks: dict[str, str],
    prefs: dict[str, str | bool],
    turn_count: int,
    memory_patch: dict[str, Any] | None = None,
) -> AgentResult:
    display_name = prefs.get("display_name")
    return AgentResult(
        agent_name=agent_name,
        session_id=session_id,
        summary=summary,
        skill_blocks=skill_blocks,
        memory_patch=memory_patch or {},
        display_name=display_name if isinstance(display_name, str) else None,
        turn_count=turn_count,
    )


async def _apply_channel_metadata(
    session_id: str,
    *,
    channel: AgentChannel | None,
    existing_metadata: dict[str, Any] | None = None,
    is_start: bool = False,
    guild_id: int | None = None,
) -> dict[str, Any]:
    if channel is None:
        return dict(existing_metadata or {})
    merged = merge_channel_metadata(
        existing_metadata or {},
        channel=channel,
        is_start=is_start,
        guild_id=guild_id,
    )
    await patch_session_metadata(session_id, merged)
    return merged


async def start_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    user_message: str | None = None,
    metadata: dict[str, Any] | None = None,
    channel: AgentChannel | None = None,
) -> AgentResult:
    """Start a multi-turn session (first turn). Session stays active until /agent end."""
    agent = resolve_agent(agent_name)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    if await get_active_session(innersync_user_id, agent_name):
        raise ActiveAgentSessionError(
            f"Active session already exists for agent {agent_name!r}. "
            "Use /agent continue or /agent end first."
        )

    from utils.premium_guard import check_and_increment_agent_session_quota

    allowed, _count, limit = await check_and_increment_agent_session_quota(
        discord_user_id,
        guild_id,
    )
    if not allowed and limit is not None:
        raise AgentSessionQuotaExceededError(_count, limit)

    prefs, tier3, derived_profile, _prior_session_count = await _load_durable_state(
        innersync_user_id,
        agent_name,
    )
    prompt = user_message or "Give a short reflection based on the context."

    session_metadata = dict(metadata or {})
    if channel is not None:
        session_metadata = merge_channel_metadata(
            session_metadata,
            channel=channel,
            is_start=True,
            guild_id=guild_id,
        )

    session_id = await create_session(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        metadata=session_metadata,
    )

    ctx = AgentContext(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        session_id=session_id,
        memory=tier3,
        metadata=session_metadata,
    )

    summary, skill_blocks, stored_user = await _run_agent_turn(
        ctx=ctx,
        prefs=prefs,
        tier3=tier3,
        derived_profile=derived_profile,
        user_message=prompt,
        prior_turns=[],
        include_context=True,
    )

    await append_session_messages(
        session_id,
        turn_index=0,
        user_content=stored_user,
        assistant_content=summary,
    )
    await touch_session(session_id)

    return _result_from_turn(
        agent_name=agent_name,
        session_id=session_id,
        summary=summary,
        skill_blocks=skill_blocks,
        prefs=prefs,
        turn_count=1,
    )


async def continue_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    user_message: str,
    metadata: dict[str, Any] | None = None,
    channel: AgentChannel | None = None,
) -> AgentResult:
    """Append a turn to the active session."""
    agent = resolve_agent(agent_name)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    active = await get_active_session(innersync_user_id, agent_name)
    if not active:
        raise NoActiveAgentSessionError(
            f"No active session for agent {agent_name!r}. Use /agent start first."
        )

    session_id = str(active["id"])
    active_metadata = active.get("metadata") or {}
    if not isinstance(active_metadata, dict):
        active_metadata = {}
    session_metadata = await _apply_channel_metadata(
        session_id,
        channel=channel,
        existing_metadata=active_metadata,
        is_start=False,
    )

    prior_turns = await get_session_messages(session_id)
    turn_index = max((int(row.get("turn_index", 0)) for row in prior_turns), default=-1) + 1

    prefs, tier3, derived_profile, _prior_session_count = await _load_durable_state(
        innersync_user_id,
        agent_name,
    )

    ctx = AgentContext(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        session_id=session_id,
        memory=tier3,
        metadata=session_metadata or dict(metadata or {}),
    )

    summary, skill_blocks, stored_user = await _run_agent_turn(
        ctx=ctx,
        prefs=prefs,
        tier3=tier3,
        derived_profile=derived_profile,
        user_message=user_message,
        prior_turns=prior_turns,
        include_context=False,
    )

    await append_session_messages(
        session_id,
        turn_index=turn_index,
        user_content=stored_user,
        assistant_content=summary,
    )
    await touch_session(session_id)

    return _result_from_turn(
        agent_name=agent_name,
        session_id=session_id,
        summary=summary,
        skill_blocks=skill_blocks,
        prefs=prefs,
        turn_count=turn_index + 1,
    )


async def end_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    metadata: dict[str, Any] | None = None,
    channel: AgentChannel | None = None,
) -> AgentResult:
    """Finalize an active session: distill Tier 2, patch Tier 3, delete ephemeral messages."""
    agent = resolve_agent(agent_name)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    active = await get_active_session(innersync_user_id, agent_name)
    if not active:
        raise NoActiveAgentSessionError(
            f"No active session for agent {agent_name!r}. Use /agent start first."
        )

    session_id = str(active["id"])
    active_metadata = active.get("metadata") or {}
    if not isinstance(active_metadata, dict):
        active_metadata = {}
    session_metadata = await _apply_channel_metadata(
        session_id,
        channel=channel,
        existing_metadata=active_metadata,
        is_start=False,
    )

    prior_turns = await get_session_messages(session_id)
    turn_count = max((int(row.get("turn_index", 0)) for row in prior_turns), default=-1) + 1

    prefs, tier3, derived_profile, prior_session_count = await _load_durable_state(
        innersync_user_id,
        agent_name,
    )

    ctx = AgentContext(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        session_id=session_id,
        memory=tier3,
        metadata=session_metadata or dict(metadata or {}),
    )

    skill_blocks = await _build_skill_context(ctx)
    ctx.skill_blocks = skill_blocks

    memory_patch = tier3_memory_patch(
        session_id=session_id,
        agent_name=agent_name,
        prior_session_count=prior_session_count,
    )

    session_summary = session_summary_from_profile(derived_profile)
    consent_ids = await _fetch_active_consent_reflection_ids(innersync_user_id)
    tier0_context = skill_blocks.get("journal_sync", "")
    user_transcript, assistant_transcript = _transcript_from_messages(prior_turns)

    if learn_from_shared_enabled(prefs) and consent_ids and tier0_context.strip():
        merged_profile = await distill_session_profile(
            tier0_context=tier0_context,
            user_message=user_transcript or "Session ended.",
            agent_response=assistant_transcript or "Session ended.",
            source_reflection_ids=consent_ids,
            existing=derived_profile,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
        )
        if merged_profile:
            memory_patch[TIER2_ROOT_KEY] = merged_profile
            derived_profile = merged_profile
            session_summary = session_summary_from_profile(merged_profile)

    updated_memory = await patch_user_memory(innersync_user_id, agent_name, memory_patch)
    await _execute_agent_skills(ctx)

    await complete_session(
        session_id,
        status="completed",
        summary=session_summary,
        memory_patch=memory_patch,
    )
    await delete_session_messages(session_id)

    last_assistant = ""
    for row in reversed(prior_turns):
        if row.get("role") == "assistant":
            last_assistant = str(row.get("content", ""))
            break

    return _result_from_turn(
        agent_name=agent_name,
        session_id=session_id,
        summary=last_assistant or "Session ended.",
        skill_blocks=skill_blocks,
        prefs=prefs,
        turn_count=turn_count,
        memory_patch=updated_memory,
    )


async def run_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    user_message: str | None = None,
    metadata: dict[str, Any] | None = None,
    finalize: bool = True,
) -> AgentResult:
    """Run an agent session. When finalize=True (default), start and end in one call."""
    result = await start_agent_session(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        user_message=user_message,
        metadata=metadata,
    )
    if not finalize:
        return result
    return await end_agent_session(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        metadata=metadata,
    )
