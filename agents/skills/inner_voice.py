"""Inner voice skill — short user-authored coaching context from agent prefs."""
from __future__ import annotations

import logging

from agents.base import AgentContext, BaseAgentSkill
from agents.profile import load_agent_prefs

logger = logging.getLogger("alphapy.agents.skills.inner_voice")

NO_INNER_VOICE_MESSAGE = (
    "No inner voice description set "
    "(optional — add a short note in Innersync App → Settings → Agent memory)."
)


class InnerVoiceSkill(BaseAgentSkill):
    name = "inner_voice"
    priority = 5

    async def gather(self, ctx: AgentContext) -> str:
        try:
            prefs = await load_agent_prefs(ctx.innersync_user_id)
        except Exception as exc:
            logger.warning("inner_voice prefs load failed: %s", exc)
            return NO_INNER_VOICE_MESSAGE

        inner_voice = prefs.get("inner_voice")
        if not isinstance(inner_voice, str) or not inner_voice.strip():
            return NO_INNER_VOICE_MESSAGE

        return (
            "User-described inner voice / self-talk patterns (use gently, do not amplify shame):\n"
            f"{inner_voice.strip()}"
        )

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["inner_voice_ran"] = True
        return None
