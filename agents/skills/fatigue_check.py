"""Fatigue check skill — self-reported energy from agent prefs."""
from __future__ import annotations

import logging

from agents.base import AgentContext, BaseAgentSkill
from agents.fatigue import format_fatigue_context
from agents.profile import load_agent_prefs

logger = logging.getLogger("alphapy.agents.skills.fatigue_check")


class FatigueCheckSkill(BaseAgentSkill):
    name = "fatigue_check"
    priority = 8

    async def gather(self, ctx: AgentContext) -> str:
        try:
            prefs = await load_agent_prefs(ctx.innersync_user_id)
        except Exception as exc:
            logger.warning("fatigue_check prefs load failed: %s", exc)
            return format_fatigue_context({})

        return format_fatigue_context(prefs)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["fatigue_check_ran"] = True
        return None
