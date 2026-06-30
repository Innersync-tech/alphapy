"""Tier 1 (user prefs) and Tier 3 (operational) agent profile assembly."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from utils.supabase_client import _supabase_get

logger = logging.getLogger("alphapy.agents.profile")

TIER1_FIELDS = frozenset({"display_name", "persona", "default_focus", "language_pref"})
TIER1_BOOL_FIELDS = frozenset({"learn_from_shared"})
TIER3_FIELDS = frozenset({"session_count", "last_session_at", "last_session_id", "last_agent"})

PERSONA_DESCRIPTIONS: dict[str, str] = {
    "calm": "warm, gentle, and grounding",
    "direct": "clear, concise, and honest",
    "playful": "light, encouraging, and a bit witty",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_agent_prefs(raw: Any) -> dict[str, str | bool]:
    """Return sanitized Tier 1 prefs from JSON."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str | bool] = {}
    for key in TIER1_FIELDS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out[key] = text[:500] if key == "default_focus" else text[:120]
    persona = out.get("persona", "")
    if isinstance(persona, str) and persona.lower() and persona.lower() not in PERSONA_DESCRIPTIONS:
        out["persona"] = "calm"
    if "learn_from_shared" in raw:
        out["learn_from_shared"] = bool(raw.get("learn_from_shared"))
    return out


def learn_from_shared_enabled(prefs: dict[str, str | bool]) -> bool:
    """Whether Tier 2 distill is allowed for this user."""
    value = prefs.get("learn_from_shared")
    if value is None:
        return False
    return bool(value)


async def load_agent_prefs(innersync_user_id: str) -> dict[str, str | bool]:
    """Load Tier 1 prefs from App settings (service role)."""
    try:
        rows = await _supabase_get(
            "app_user_settings",
            {
                "select": "agent_prefs",
                "user_id": f"eq.{innersync_user_id}",
                "limit": 1,
            },
        )
    except Exception as exc:
        logger.warning("Failed to load agent_prefs for %s: %s", innersync_user_id, exc)
        return {}
    if not rows:
        return {}
    return normalize_agent_prefs(rows[0].get("agent_prefs"))


def extract_tier3_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Keep only operational metadata from durable memory."""
    if not memory:
        return {}
    return {k: v for k, v in memory.items() if k in TIER3_FIELDS}


def build_agent_profile_block(
    prefs: dict[str, str | bool],
    tier3: dict[str, Any],
    *,
    derived_profile: dict[str, Any] | None = None,
) -> str:
    """Format [agent_profile] context for the LLM (no journal text)."""
    lines: list[str] = []

    display_name = prefs.get("display_name")
    if isinstance(display_name, str) and display_name:
        lines.append(f"Preferred agent name: {display_name}")

    persona = prefs.get("persona", "")
    if isinstance(persona, str) and persona.lower() in PERSONA_DESCRIPTIONS:
        lines.append(f"Tone: {persona.lower()} ({PERSONA_DESCRIPTIONS[persona.lower()]})")

    default_focus = prefs.get("default_focus")
    if isinstance(default_focus, str) and default_focus:
        lines.append(f"Default reflection focus: {default_focus}")

    language_pref = prefs.get("language_pref")
    if isinstance(language_pref, str) and language_pref:
        lines.append(f"Preferred language: {language_pref}")

    if derived_profile:
        insights = derived_profile.get("insights") or []
        if insights:
            lines.append("Remembered patterns (generalized, not journal quotes):")
            for ins in insights[:8]:
                if not isinstance(ins, dict):
                    continue
                label = ins.get("label")
                itype = ins.get("type")
                if label:
                    lines.append(f"- {label}" + (f" ({itype})" if itype else ""))
        themes = derived_profile.get("active_themes") or []
        if themes:
            lines.append("Active themes: " + ", ".join(str(t) for t in themes[:6]))
        loops = derived_profile.get("open_loops") or []
        if loops:
            lines.append("Open loops: " + "; ".join(str(loop) for loop in loops[:3]))

    session_count = tier3.get("session_count")
    if session_count is not None:
        try:
            count = int(session_count)
            if count > 0:
                lines.append(f"Prior agent sessions with this user: {count}")
        except (TypeError, ValueError):
            pass

    last_session_at = tier3.get("last_session_at")
    if last_session_at:
        lines.append(f"Last session at: {last_session_at}")

    return "\n".join(lines)


def tier3_memory_patch(
    *,
    session_id: str,
    agent_name: str,
    prior_session_count: int,
) -> dict[str, Any]:
    """Patch payload allowed in agent_memory (Tier 3 only)."""
    return {
        "last_session_id": session_id,
        "last_agent": agent_name,
        "session_count": prior_session_count + 1,
        "last_session_at": _now_iso(),
    }
