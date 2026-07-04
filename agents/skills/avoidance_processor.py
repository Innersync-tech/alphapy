"""Avoidance processor — energy-aware seal-off vs process exercise."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agents.base import AgentContext, BaseAgentSkill
from agents.fatigue import VALID_ENERGY_LEVELS, format_fatigue_context, load_agent_prefs
from agents.profile import learn_from_shared_enabled
from agents.skill_memory import (
    AVOIDANCE_KEYWORDS,
    format_pattern_block,
    get_normalized_profile,
    insight_labels,
    select_insights,
)
from agents.tier2 import _parse_distill_json
from gpt.helpers import ask_gpt

logger = logging.getLogger("alphapy.agents.skills.avoidance_processor")


def _energy_level(prefs: dict[str, str | bool]) -> int | None:
    level = prefs.get("energy_level")
    if not isinstance(level, str) or level not in VALID_ENERGY_LEVELS:
        return None
    try:
        return int(level)
    except ValueError:
        return None


def _queue_insight_candidate(ctx: AgentContext, candidate: dict[str, Any]) -> None:
    existing = ctx.metadata.get("skill_insight_candidates")
    if not isinstance(existing, list):
        existing = []
    existing.append(candidate)
    ctx.metadata["skill_insight_candidates"] = existing
    ctx.metadata["consent_epoch"] = datetime.now(UTC).isoformat()


class AvoidanceProcessorSkill(BaseAgentSkill):
    name = "avoidance_processor"
    priority = 7

    def enabled(self, ctx: AgentContext) -> bool:
        profile = get_normalized_profile(ctx)
        return bool(
            select_insights(
                profile,
                types=frozenset({"habit", "trigger"}),
                keywords=AVOIDANCE_KEYWORDS,
                limit=1,
            )
        )

    async def gather(self, ctx: AgentContext) -> str:
        try:
            prefs = await load_agent_prefs(ctx.innersync_user_id)
        except Exception as exc:
            logger.warning("avoidance_processor prefs load failed: %s", exc)
            prefs = {}

        ctx.metadata["prefs"] = prefs
        profile = get_normalized_profile(ctx)
        insights = select_insights(
            profile,
            types=frozenset({"habit", "trigger"}),
            keywords=AVOIDANCE_KEYWORDS,
            limit=3,
        )
        if not insights:
            insights = select_insights(
                profile,
                types=frozenset({"habit", "trigger"}),
                limit=2,
            )

        energy = _energy_level(prefs)
        lines: list[str] = [format_fatigue_context(prefs)]
        labels = insight_labels(insights)
        if labels:
            lines.append("Avoidance / suppression patterns (generalized):")
            for label in labels:
                lines.append(f"- {label}")

        if energy is not None and energy <= 2:
            lines.append(
                "Soft entry: offer a gentle seal-off vs process choice. "
                "Permission to pause — no pressure to go deep today."
            )
        else:
            lines.append(
                "Higher energy window: guide a structured seal-off vs process reflection. "
                "One step at a time; still no advice dump."
            )

        lines.append(
            "When the user completes the exercise, mark progress in your reply "
            "(they may end the session with /agent end to store a distilled pattern)."
        )
        ctx.metadata["avoidance_processor_active"] = True
        return format_pattern_block("Avoidance processor", lines)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["avoidance_processor_ran"] = True
        if not ctx.metadata.get("avoidance_processor_active"):
            return None

        prefs = ctx.metadata.get("prefs")
        if not isinstance(prefs, dict):
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

        system = (
            "Extract ONE generalized avoidance-processing insight from a coaching session. "
            "Return ONLY valid JSON: "
            '{"insights":[{"type":"habit|trigger|theme","label":"abstract pattern under 120 chars",'
            '"confidence":0.65-1.0}]}\n'
            "NO quotes; NO names; NO dates; abstract pattern only."
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
            logger.warning("avoidance_processor distill failed: %s", exc)
            return None

        parsed = _parse_distill_json(raw or "")
        if not parsed:
            return None
        insights = parsed.get("insights")
        if not isinstance(insights, list) or not insights:
            return None
        first = insights[0]
        if isinstance(first, dict):
            _queue_insight_candidate(ctx, first)
        return None
