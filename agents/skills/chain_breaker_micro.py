"""Chain breaker micro skill — generational pattern break + one daily habit."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agents.base import AgentContext, BaseAgentSkill
from agents.profile import learn_from_shared_enabled, load_agent_prefs
from agents.skill_memory import (
    AVOIDANCE_KEYWORDS,
    format_pattern_block,
    get_normalized_profile,
    insight_labels,
    select_insights,
)

logger = logging.getLogger("alphapy.agents.skills.chain_breaker_micro")


def _queue_insight_candidate(ctx: AgentContext, candidate: dict[str, Any]) -> None:
    existing = ctx.metadata.get("skill_insight_candidates")
    if not isinstance(existing, list):
        existing = []
    existing.append(candidate)
    ctx.metadata["skill_insight_candidates"] = existing
    ctx.metadata["consent_epoch"] = datetime.now(UTC).isoformat()


class ChainBreakerMicroSkill(BaseAgentSkill):
    name = "chain_breaker_micro"
    priority = 9

    def enabled(self, ctx: AgentContext) -> bool:
        profile = get_normalized_profile(ctx)
        if select_insights(
            profile,
            types=frozenset({"habit", "trigger", "theme"}),
            keywords=AVOIDANCE_KEYWORDS,
            limit=1,
        ):
            return True
        journal_block = (ctx.skill_blocks or {}).get("journal_sync", "")
        return bool(journal_block.strip()) and "No reflections explicitly shared" not in journal_block

    async def gather(self, ctx: AgentContext) -> str:
        profile = get_normalized_profile(ctx)
        insights = select_insights(
            profile,
            types=frozenset({"habit", "trigger", "theme"}),
            keywords=AVOIDANCE_KEYWORDS,
            limit=2,
        )
        if not insights:
            insights = select_insights(profile, types=frozenset({"habit", "trigger"}), limit=2)

        lines: list[str] = [
            "Chain-break framing: raw and honest — no hype, no toxic positivity.",
            "Confront avoidance/suppression with doing better than the previous generation.",
            "Propose exactly ONE concrete micro-habit for today (small enough to do once).",
        ]
        labels = insight_labels(insights)
        if labels:
            lines.append("Patterns to break (generalized):")
            for label in labels:
                lines.append(f"- {label}")

        journal_hint = (ctx.skill_blocks or {}).get("journal_sync", "")
        if journal_hint.strip() and "No reflections explicitly shared" not in journal_hint:
            lines.append(
                "Use shared reflection context only as hints — do not quote journal text."
            )

        ctx.metadata["chain_breaker_active"] = True
        return format_pattern_block("Chain breaker micro", lines)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["chain_breaker_micro_ran"] = True
        if not ctx.metadata.get("chain_breaker_active"):
            return None

        try:
            prefs = await load_agent_prefs(ctx.innersync_user_id)
        except Exception:
            prefs = {}

        consent_raw = ctx.metadata.get("consent_ids") or []
        consent_ids = frozenset(str(x) for x in consent_raw if x)
        tier0 = (ctx.skill_blocks or {}).get("journal_sync", "")
        if not learn_from_shared_enabled(prefs) or not consent_ids or not tier0.strip():
            return None

        user_transcript = str(ctx.metadata.get("user_transcript") or "")
        assistant_transcript = str(ctx.metadata.get("assistant_transcript") or "")
        if not user_transcript.strip() and not assistant_transcript.strip():
            return None

        from agents.tier2 import _parse_distill_json
        from gpt.helpers import ask_gpt

        system = (
            "Extract ONE generalized micro-habit insight from a chain-breaking coaching session. "
            "Return ONLY valid JSON: "
            '{"insights":[{"type":"habit","label":"abstract daily micro-habit under 120 chars",'
            '"confidence":0.65-1.0}]}\n'
            "NO quotes; NO hype; raw and concrete; abstract pattern only."
        )
        user = (
            f"User transcript:\n{user_transcript[:1200]}\n\n"
            f"Agent transcript:\n{assistant_transcript[:1200]}"
        )
        try:
            raw = await ask_gpt(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                user_id=ctx.discord_user_id,
                guild_id=ctx.guild_id,
                include_reflections=False,
            )
        except Exception as exc:
            logger.warning("chain_breaker_micro distill failed: %s", exc)
            return None

        parsed = _parse_distill_json(raw or "")
        if not parsed:
            return None
        insights = parsed.get("insights")
        if not isinstance(insights, list) or not insights:
            return None
        first = insights[0]
        if isinstance(first, dict):
            first = dict(first)
            first["type"] = "habit"
            _queue_insight_candidate(ctx, first)
        return None
