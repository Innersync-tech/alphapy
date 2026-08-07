"""Agent session orchestration."""
from __future__ import annotations

import asyncio
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
from agents.pattern_loader import load_pattern_context
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
    SESSION_INSIGHT_SNAPSHOT_KEY,
    TIER2_ROOT_KEY,
    append_skill_insights,
    build_blocklist_from_tier0,
    build_session_insight_snapshot,
    distill_session_profile,
    extract_derived_profile,
    normalize_derived_profile,
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
    pattern_context: str | None = None,
    platform_locale: str | None = None,
) -> str:
    parts: list[str] = []
    profile_block = build_agent_profile_block(
        prefs,
        tier3,
        derived_profile=derived_profile,
        platform_locale=platform_locale,
    )
    if profile_block.strip():
        parts.append("[agent_profile]\n" + safe_prompt(profile_block[:1500]))
    if pattern_context and pattern_context.strip():
        parts.append(safe_prompt(pattern_context[:1500]))
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
    platform_locale: str = "en",
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_agent_system_prompt(locale=platform_locale)},
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
    from utils.platform_locale import resolve_locale_for_discord

    platform_locale = await resolve_locale_for_discord(ctx.discord_user_id, prefs)
    skill_blocks = await _build_skill_context(ctx)
    ctx.skill_blocks = skill_blocks
    pattern_context = await load_pattern_context(ctx.innersync_user_id, prefs)
    context_blob = _assemble_prompt(
        skill_blocks,
        prefs=prefs,
        tier3=tier3,
        derived_profile=derived_profile,
        pattern_context=pattern_context,
        platform_locale=platform_locale,
    )
    messages = _build_llm_messages(
        context_blob=context_blob,
        user_request=user_message,
        prior_turns=prior_turns,
        include_context=include_context,
        platform_locale=platform_locale,
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
    # Empty start: open a dialogue — do not dump a full context summary (that caused echo loops).
    prompt = (user_message or "").strip() or (
        "The user started a reflection session without a specific question. "
        "Respond in their language if clear from context, otherwise English. "
        "Open briefly: at most one short greeting and either one open question OR one single "
        "light hook from context — never list or summarize multiple context items. "
        "Do not invent a full reflection essay; invite them to set the focus."
    )

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
        derived_profile=derived_profile,
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
        derived_profile=derived_profile,
        metadata=session_metadata or dict(metadata or {}),
    )

    # Re-inject skill/profile context on continue (same as start). Without this, multi-turn
    # chats collapse into history-only somatic loops and lose inner_voice / pattern guidance.
    summary, skill_blocks, stored_user = await _run_agent_turn(
        ctx=ctx,
        prefs=prefs,
        tier3=tier3,
        derived_profile=derived_profile,
        user_message=user_message,
        prior_turns=prior_turns,
        include_context=True,
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


# Pending end-finalize tasks (tests can drain; production fire-and-forget).
_pending_end_finalize_tasks: set[asyncio.Task[Any]] = set()


def _schedule_end_finalize(coro: Any, *, name: str) -> asyncio.Task[Any]:
    task: asyncio.Task[Any] = asyncio.create_task(coro, name=name)
    _pending_end_finalize_tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _pending_end_finalize_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("end-session background finalize failed: %s", exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


async def drain_end_background_jobs() -> None:
    """Await all pending end-finalize jobs (tests / graceful shutdown)."""
    while _pending_end_finalize_tasks:
        await asyncio.gather(*list(_pending_end_finalize_tasks), return_exceptions=True)


async def end_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    metadata: dict[str, Any] | None = None,
    channel: AgentChannel | None = None,
) -> AgentResult:
    """End session fast: complete + clear active path, heavy work in background.

    Critical path (user-visible): load turns, Tier-3 session bookkeeping, mark completed,
    delete ephemeral messages, return last assistant text.

    Background: skill gather, Tier-2 distill, skill execute, insight snapshot, Core graph.
    """
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
    user_transcript, assistant_transcript = _transcript_from_messages(prior_turns)

    last_assistant = ""
    for row in reversed(prior_turns):
        if row.get("role") == "assistant":
            last_assistant = str(row.get("content", ""))
            break

    prefs, tier3, derived_profile, prior_session_count = await _load_durable_state(
        innersync_user_id,
        agent_name,
    )
    profile_before = extract_derived_profile(
        {TIER2_ROOT_KEY: derived_profile} if derived_profile else {}
    )

    # Fast Tier-3 only — free the active session slot before any LLM work.
    memory_patch = tier3_memory_patch(
        session_id=session_id,
        agent_name=agent_name,
        prior_session_count=prior_session_count,
    )
    quick_summary = (last_assistant or "Session ended.")[:500]
    await complete_session(
        session_id,
        status="completed",
        summary=quick_summary,
        memory_patch=memory_patch,
    )
    await delete_session_messages(session_id)

    # Persist session_count immediately so concurrent starts see correct Tier-3.
    updated_memory = await patch_user_memory(innersync_user_id, agent_name, memory_patch)

    _schedule_end_finalize(
        _finalize_session_end_background(
            innersync_user_id=innersync_user_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            agent_name=agent_name,
            session_id=session_id,
            session_metadata=session_metadata or dict(metadata or {}),
            prefs=prefs,
            tier3=tier3,
            derived_profile=derived_profile,
            profile_before=profile_before,
            memory_patch=dict(memory_patch),
            user_transcript=user_transcript,
            assistant_transcript=assistant_transcript,
        ),
        name=f"agent-end-finalize:{session_id}",
    )

    return _result_from_turn(
        agent_name=agent_name,
        session_id=session_id,
        summary=last_assistant or "Session ended.",
        skill_blocks={},
        prefs=prefs,
        turn_count=turn_count,
        memory_patch=updated_memory,
    )


async def _finalize_session_end_background(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    session_id: str,
    session_metadata: dict[str, Any],
    prefs: dict[str, str | bool],
    tier3: dict[str, Any],
    derived_profile: dict[str, Any],
    profile_before: dict[str, Any],
    memory_patch: dict[str, Any],
    user_transcript: str,
    assistant_transcript: str,
) -> None:
    """LLM distill, skill side-effects, snapshot, graph — after user already got end ACK."""
    ctx = AgentContext(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        session_id=session_id,
        memory=tier3,
        derived_profile=derived_profile,
        metadata=dict(session_metadata),
    )

    skill_blocks = await _build_skill_context(ctx)
    ctx.skill_blocks = skill_blocks
    consent_ids = await _fetch_active_consent_reflection_ids(innersync_user_id)
    tier0_context = skill_blocks.get("journal_sync", "")
    ctx.metadata["user_transcript"] = user_transcript
    ctx.metadata["assistant_transcript"] = assistant_transcript
    ctx.metadata["consent_ids"] = sorted(consent_ids)
    ctx.metadata["prefs"] = prefs

    session_summary = session_summary_from_profile(derived_profile)
    updated_memory: dict[str, Any] = dict(memory_patch)

    if learn_from_shared_enabled(prefs) and consent_ids and tier0_context.strip():
        from utils.platform_locale import resolve_locale_for_discord

        platform_locale = await resolve_locale_for_discord(discord_user_id, prefs)
        merged_profile = await distill_session_profile(
            tier0_context=tier0_context,
            user_message=user_transcript or "Session ended.",
            agent_response=assistant_transcript or "Session ended.",
            source_reflection_ids=consent_ids,
            existing=derived_profile,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            platform_locale=platform_locale,
        )
        if merged_profile:
            memory_patch[TIER2_ROOT_KEY] = merged_profile
            derived_profile = merged_profile
            ctx.derived_profile = derived_profile
            session_summary = session_summary_from_profile(merged_profile)

    updated_memory = await patch_user_memory(innersync_user_id, agent_name, memory_patch)
    ctx.derived_profile = extract_derived_profile(updated_memory)
    await _execute_agent_skills(ctx)

    skill_candidates = ctx.metadata.get("skill_insight_candidates")
    if (
        isinstance(skill_candidates, list)
        and skill_candidates
        and learn_from_shared_enabled(prefs)
        and consent_ids
        and tier0_context.strip()
    ):
        blocklist = build_blocklist_from_tier0(tier0_context)
        merged_after_skills = append_skill_insights(
            ctx.derived_profile,
            skill_candidates,
            source_reflection_ids=consent_ids,
            consent_epoch=ctx.metadata.get("consent_epoch") or "",
            blocklist=blocklist,
        )
        if merged_after_skills != normalize_derived_profile(ctx.derived_profile):
            skill_patch = {TIER2_ROOT_KEY: merged_after_skills}
            updated_memory = await patch_user_memory(
                innersync_user_id,
                agent_name,
                skill_patch,
            )
            derived_profile = extract_derived_profile(updated_memory)
            ctx.derived_profile = derived_profile
            memory_patch[TIER2_ROOT_KEY] = derived_profile
            session_summary = session_summary_from_profile(derived_profile)

    snapshot = build_session_insight_snapshot(profile_before, derived_profile)
    if snapshot:
        memory_patch[SESSION_INSIGHT_SNAPSHOT_KEY] = snapshot

    # Enrich completed session row (status already completed on critical path).
    await complete_session(
        session_id,
        status="completed",
        summary=session_summary or (assistant_transcript[:500] if assistant_transcript else "Session ended."),
        memory_patch=memory_patch,
    )

    themes: list[Any] = []
    if isinstance(derived_profile, dict):
        raw_themes = derived_profile.get("active_themes")
        if isinstance(raw_themes, list):
            themes = list(raw_themes)
    await _push_agent_graph_progress_background(
        discord_user_id=discord_user_id,
        session_id=session_id,
        insight_snapshot=snapshot if isinstance(snapshot, list) else None,
        active_themes=themes,
    )


async def _push_agent_graph_progress_background(
    *,
    discord_user_id: int,
    session_id: str,
    insight_snapshot: list[dict[str, Any]] | None,
    active_themes: list[Any] | None,
) -> None:
    """Best-effort Core graph write after session end; never blocks the end response."""
    try:
        from utils.core_agent_graph import push_agent_chat_progress

        await push_agent_chat_progress(
            discord_user_id=discord_user_id,
            session_id=session_id,
            insight_snapshot=insight_snapshot,
            active_themes=active_themes,
        )
    except Exception:
        logger.warning(
            "agent-graph progress push failed after session end (background) session=%s",
            session_id,
            exc_info=True,
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
    ended = await end_agent_session(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        metadata=metadata,
    )
    # Fast end returns empty skill_blocks; one-shot callers still want start's skill map
    # (e.g. Discord "skills used" footer / tests).
    if result.skill_blocks and not ended.skill_blocks:
        return AgentResult(
            agent_name=ended.agent_name,
            session_id=ended.session_id,
            summary=ended.summary,
            skill_blocks=result.skill_blocks,
            memory_patch=ended.memory_patch,
            display_name=ended.display_name,
            turn_count=ended.turn_count,
        )
    return ended
