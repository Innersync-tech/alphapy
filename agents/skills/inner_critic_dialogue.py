"""Inner critic dialogue skill — mirror patterns from Tier 2 + inner voice."""
from __future__ import annotations

import logging

from agents.base import AgentContext, BaseAgentSkill
from agents.profile import load_agent_prefs
from agents.skill_memory import (
    INNER_CONFLICT_KEYWORDS,
    format_pattern_block,
    get_normalized_profile,
    insight_labels,
    select_insights,
)

logger = logging.getLogger("alphapy.agents.skills.inner_critic_dialogue")

DIALOGUE_RULES = (
    "Dialogue mode: lead a safe back-and-forth with the user's inner critic. "
    "Mirror what you hear — do not dump advice. Offer one micro prompt to apply. "
    "Keep turns short; invite /agent continue for the next exchange."
)


class InnerCriticDialogueSkill(BaseAgentSkill):
    name = "inner_critic_dialogue"
    priority = 6

    def enabled(self, ctx: AgentContext) -> bool:
        return True

    async def gather(self, ctx: AgentContext) -> str:
        profile = get_normalized_profile(ctx)
        insights = select_insights(
            profile,
            types=frozenset({"theme", "emotion"}),
            keywords=INNER_CONFLICT_KEYWORDS,
            limit=2,
        )
        if not insights:
            insights = select_insights(
                profile,
                types=frozenset({"theme", "emotion"}),
                limit=2,
            )

        lines: list[str] = [DIALOGUE_RULES]
        labels = insight_labels(insights)
        if labels:
            lines.append("Remembered inner-conflict patterns (generalized):")
            for label in labels:
                lines.append(f"- {label}")

        try:
            prefs = await load_agent_prefs(ctx.innersync_user_id)
        except Exception as exc:
            logger.warning("inner_critic_dialogue prefs load failed: %s", exc)
            prefs = {}

        inner_voice = prefs.get("inner_voice")
        if isinstance(inner_voice, str) and inner_voice.strip():
            lines.append(f"User inner voice note: {inner_voice.strip()}")

        if len(lines) <= 1 and not (
            isinstance(prefs.get("inner_voice"), str) and prefs.get("inner_voice", "").strip()
        ):
            return ""

        return format_pattern_block("Inner critic dialogue", lines)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["inner_critic_dialogue_ran"] = True
        return None
