"""Agent session orchestration."""
from __future__ import annotations

import logging
from typing import Any

from agents.base import AgentContext, AgentResult
from agents.memory import (
    complete_session,
    create_session,
    get_user_memory,
    patch_user_memory,
    strip_sensitive_memory_keys,
)
from agents.policy import (
    build_agent_system_prompt,
    build_agent_user_message,
)
from agents.profile import (
    build_agent_profile_block,
    extract_tier3_memory,
    load_agent_prefs,
    tier3_memory_patch,
)
from agents.registry import resolve_agent
from gpt.helpers import ask_gpt
from utils.sanitizer import safe_prompt

logger = logging.getLogger("alphapy.agents.runtime")


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
    prefs: dict[str, str],
    tier3: dict[str, Any],
) -> str:
    parts: list[str] = []
    profile_block = build_agent_profile_block(prefs, tier3)
    if profile_block.strip():
        parts.append("[agent_profile]\n" + safe_prompt(profile_block[:1500]))
    for name, body in sorted(skill_blocks.items()):
        parts.append(f"[{name}]\n{safe_prompt(body[:2500])}")
    return "\n\n".join(parts)


async def run_agent_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    user_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentResult:
    """Run a full agent loop: load memory → gather skills → LLM → persist."""
    agent = resolve_agent(agent_name)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    raw_memory = await get_user_memory(innersync_user_id, agent_name)
    tier3 = extract_tier3_memory(strip_sensitive_memory_keys(raw_memory))
    prefs = await load_agent_prefs(innersync_user_id)
    prior_session_count = int(tier3.get("session_count", 0))

    session_id = await create_session(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        metadata=metadata,
    )

    ctx = AgentContext(
        innersync_user_id=innersync_user_id,
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        agent_name=agent_name,
        session_id=session_id,
        memory=tier3,
        metadata=metadata or {},
    )

    skill_blocks = await _build_skill_context(ctx)
    ctx.skill_blocks = skill_blocks

    context_blob = _assemble_prompt(skill_blocks, prefs=prefs, tier3=tier3)
    prompt = user_message or "Give a short reflection based on the context."
    messages = [
        {"role": "system", "content": build_agent_system_prompt()},
        {
            "role": "user",
            "content": build_agent_user_message(
                context_blob=context_blob,
                user_request=safe_prompt(prompt[:2000]),
            ),
        },
    ]

    summary = await ask_gpt(
        messages,
        user_id=discord_user_id,
        guild_id=guild_id,
        include_reflections=False,
    )
    if not summary:
        summary = "I could not generate a response right now. Please try again shortly."

    memory_patch = tier3_memory_patch(
        session_id=session_id,
        agent_name=agent_name,
        prior_session_count=prior_session_count,
    )
    updated_memory = await patch_user_memory(innersync_user_id, agent_name, memory_patch)

    for skill in agent.skills:
        if not skill.enabled(ctx):
            continue
        try:
            await skill.execute(ctx)
        except Exception as exc:
            logger.warning("Skill %s execute failed: %s", skill.name, exc)

    await complete_session(
        session_id,
        status="completed",
        summary=summary,
        memory_patch=memory_patch,
    )

    return AgentResult(
        agent_name=agent_name,
        session_id=session_id,
        summary=summary,
        skill_blocks=skill_blocks,
        memory_patch=updated_memory,
        display_name=prefs.get("display_name"),
    )
