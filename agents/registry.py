"""Agent and skill registry."""
from __future__ import annotations

import logging
from typing import Iterable

from agents.base import AgentSkill, BaseAgent
from agents.skills.journal_sync import JournalSyncSkill
from agents.skills.trade_insight import TradeInsightSkill

logger = logging.getLogger("alphapy.agents")

_AGENT_DEFINITIONS: dict[str, dict[str, object]] = {
    "reflection": {
        "description": "Daily journal reflection and pattern awareness.",
        "skills": ("journal_sync",),
    },
    "trade": {
        "description": "Lightweight trade psychology insight from recent demo trades.",
        "skills": ("trade_insight",),
    },
    "full": {
        "description": "Combined reflection + trade insight loop.",
        "skills": ("journal_sync", "trade_insight"),
    },
}

_SKILL_INSTANCES: dict[str, AgentSkill] = {
    "journal_sync": JournalSyncSkill(),
    "trade_insight": TradeInsightSkill(),
}


def list_agents() -> list[str]:
    return list(_AGENT_DEFINITIONS.keys())


def resolve_agent(agent_name: str) -> BaseAgent | None:
    """Return an agent shell with skills wired for the given name."""
    definition = _AGENT_DEFINITIONS.get(agent_name)
    if definition is None:
        return None
    skill_names = definition.get("skills", ())
    skills = resolve_skills_for_agent(skill_names)  # type: ignore[arg-type]
    agent = BaseAgent(skills=skills)
    agent.name = agent_name
    agent.description = str(definition.get("description", ""))
    agent.default_skills = tuple(skill_names)  # type: ignore[assignment]
    return agent


def resolve_skills_for_agent(skill_names: Iterable[str]) -> list[AgentSkill]:
    resolved: list[AgentSkill] = []
    for name in skill_names:
        skill = _SKILL_INSTANCES.get(name)
        if skill is None:
            logger.warning("Unknown agent skill: %s", name)
            continue
        resolved.append(skill)
    return sorted(resolved, key=lambda s: s.priority)
