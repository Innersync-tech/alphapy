"""Base types for Alphapy multi-user agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class AgentContext:
    """Per-invocation context for a single user agent run."""

    innersync_user_id: str
    discord_user_id: int
    guild_id: int | None
    agent_name: str
    session_id: str | None = None
    memory: dict[str, Any] = field(default_factory=dict)
    skill_blocks: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    """Outcome of an agent session."""

    agent_name: str
    session_id: str
    summary: str
    skill_blocks: dict[str, str]
    memory_patch: dict[str, Any] = field(default_factory=dict)
    display_name: str | None = None
    turn_count: int = 1


@runtime_checkable
class AgentSkill(Protocol):
    """Skill that gathers context and optionally produces user-facing output."""

    name: str
    priority: int

    def enabled(self, ctx: AgentContext) -> bool:
        """Whether this skill should run for the given context."""
        ...

    async def gather(self, ctx: AgentContext) -> str:
        """Collect context text for prompt assembly (no LLM call)."""
        ...

    async def execute(self, ctx: AgentContext) -> str | None:
        """Optional post-LLM side effect (persist insight, sync journal, etc.)."""
        ...


class BaseAgentSkill:
    """Optional base with defaults."""

    name: str = "base"
    priority: int = 50

    def enabled(self, ctx: AgentContext) -> bool:
        return True

    async def gather(self, ctx: AgentContext) -> str:
        return ""

    async def execute(self, ctx: AgentContext) -> str | None:
        return None


class BaseAgent:
    """Minimal agent shell: runs registered skills and synthesizes a response."""

    name: str = "base"
    description: str = ""
    default_skills: tuple[str, ...] = ()

    def __init__(self, skills: list[AgentSkill] | None = None) -> None:
        self._skills = skills or []

    @property
    def skills(self) -> list[AgentSkill]:
        return self._skills
