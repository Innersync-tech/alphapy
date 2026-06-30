"""Fatigue / energy self-report helpers for agent prefs."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agents.profile import load_agent_prefs, merge_agent_prefs_fields
from utils.supabase_client import _supabase_get

logger = logging.getLogger("alphapy.agents.fatigue")

# Prompt Discord quick check when report is missing or older than this.
FATIGUE_PROMPT_STALE_HOURS = 24
# Skill marks context as possibly outdated after this.
FATIGUE_CONTEXT_STALE_HOURS = 72

ENERGY_LEVEL_LABELS: dict[str, str] = {
    "1": "Very low / depleted",
    "2": "Low energy",
    "3": "Moderate",
    "4": "Good energy",
    "5": "Well rested / energized",
}

VALID_ENERGY_LEVELS = frozenset(ENERGY_LEVEL_LABELS.keys())


def parse_fatigue_reported_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def fatigue_report_age_hours(prefs: dict[str, str | bool], *, now: datetime | None = None) -> float | None:
    reported = parse_fatigue_reported_at(prefs.get("fatigue_reported_at"))
    if reported is None:
        return None
    current = now or datetime.now(UTC)
    return (current - reported).total_seconds() / 3600.0


def needs_fatigue_prompt(prefs: dict[str, str | bool], *, now: datetime | None = None) -> bool:
    level = prefs.get("energy_level")
    if not isinstance(level, str) or level not in VALID_ENERGY_LEVELS:
        return True
    age = fatigue_report_age_hours(prefs, now=now)
    if age is None:
        return True
    return age > FATIGUE_PROMPT_STALE_HOURS


def format_fatigue_context(prefs: dict[str, str | bool], *, now: datetime | None = None) -> str:
    level = prefs.get("energy_level")
    if not isinstance(level, str) or level not in VALID_ENERGY_LEVELS:
        return (
            "No recent energy self-report. "
            "Ask gently about rest and energy if relevant; user can update in "
            "Innersync App → Settings → Agent memory or via the quick check before /agent."
        )

    label = ENERGY_LEVEL_LABELS[level]
    lines = [f"Self-reported energy level: {level}/5 ({label})."]
    note = prefs.get("fatigue_note")
    if isinstance(note, str) and note.strip():
        lines.append(f"User note: {note.strip()}")

    age = fatigue_report_age_hours(prefs, now=now)
    if age is None:
        lines.append("Report timestamp missing — treat energy level as approximate.")
    elif age > FATIGUE_CONTEXT_STALE_HOURS:
        hours = int(age)
        lines.append(
            f"Report is {hours}h old — energy may have changed; avoid strong assumptions."
        )
    else:
        hours = max(0, int(age))
        lines.append(f"Reported about {hours}h ago.")

    lines.append(
        "Use this only as a self-report hint — not medical advice. "
        "Do not push the user to overexert when energy is low."
    )
    return "\n".join(lines)


async def load_raw_agent_prefs(innersync_user_id: str) -> dict[str, Any]:
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
        logger.warning("Failed to load raw agent_prefs for %s: %s", innersync_user_id, exc)
        return {}
    if not rows:
        return {}
    raw = rows[0].get("agent_prefs")
    return raw if isinstance(raw, dict) else {}


async def save_fatigue_self_report(
    innersync_user_id: str,
    *,
    energy_level: str,
    fatigue_note: str | None = None,
) -> dict[str, str | bool]:
    """Merge fatigue fields into Tier 1 agent_prefs (Discord quick check or API)."""
    level = energy_level.strip()
    if level not in VALID_ENERGY_LEVELS:
        raise ValueError(f"Invalid energy level: {energy_level}")

    patch: dict[str, str] = {
        "energy_level": level,
        "fatigue_reported_at": datetime.now(UTC).isoformat(),
    }
    if fatigue_note and fatigue_note.strip():
        patch["fatigue_note"] = fatigue_note.strip()[:200]

    raw = await load_raw_agent_prefs(innersync_user_id)
    merged = {**raw, **patch}
    return await merge_agent_prefs_fields(innersync_user_id, merged)


async def should_prompt_fatigue_check(innersync_user_id: str) -> bool:
    prefs = await load_agent_prefs(innersync_user_id)
    return needs_fatigue_prompt(prefs)
