"""Fetch Tier-2 derived insights for agent context."""

from __future__ import annotations

import logging
from typing import Any

from agents.memory import get_user_memory
from agents.profile import learn_from_patterns_enabled
from agents.tier2 import extract_derived_profile

logger = logging.getLogger("alphapy.agents.pattern_loader")

_REFLECTION_AGENT = "reflection"
_PATTERN_CONTEXT_MAX = 1500
_MAX_INSIGHTS = 8


async def _fetch_tier2_insights(innersync_user_id: str) -> list[dict[str, Any]]:
    """Load validated insights from agent_memory derived_profile."""
    try:
        memory = await get_user_memory(innersync_user_id, _REFLECTION_AGENT)
    except Exception as exc:
        logger.debug("Tier-2 memory fetch failed: %s", exc)
        return []

    if not memory:
        return []

    derived = extract_derived_profile(memory)
    insights = derived.get("insights") or []
    if not isinstance(insights, list):
        return []

    out: list[dict[str, Any]] = []
    for item in insights:
        if isinstance(item, dict) and str(item.get("label") or "").strip():
            out.append(item)
        if len(out) >= _MAX_INSIGHTS:
            break
    return out


async def load_pattern_context(
    innersync_user_id: str,
    prefs: dict[str, Any],
) -> str | None:
    """
    Load Tier-2-safe insight labels when user opted into learning from patterns.
    Falls back to learn_from_shared for backward compatibility.
    """
    if not learn_from_patterns_enabled(prefs):
        return None

    insights = await _fetch_tier2_insights(innersync_user_id)
    if not insights:
        return None

    lines = ["[learned_patterns]"]
    for ins in insights:
        label = str(ins.get("label") or "").strip()[:120]
        if not label:
            continue
        itype = ins.get("type")
        if isinstance(itype, str) and itype.strip():
            lines.append(f"- {label} ({itype.strip()})")
        else:
            lines.append(f"- {label}")

    block = "\n".join(lines)
    return block[:_PATTERN_CONTEXT_MAX] if block.strip() else None
