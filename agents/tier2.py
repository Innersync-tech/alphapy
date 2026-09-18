"""Tier 2 derived profile: distill, validate, merge, and consent-linked purge."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from gpt.helpers import ask_gpt

logger = logging.getLogger("alphapy.agents.tier2")

TIER2_ROOT_KEY = "derived_profile"
DERIVED_PROFILE_VERSION = 1
INSIGHT_TYPES = frozenset({"theme", "emotion", "goal", "habit", "trigger"})
MIN_CONFIDENCE = 0.6
MAX_INSIGHTS = 20
MAX_LABEL_LEN = 120
MAX_THEMES = 12
MAX_OPEN_LOOPS = 8

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ]{4,}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_derived_profile() -> dict[str, Any]:
    return {
        "version": DERIVED_PROFILE_VERSION,
        "insights": [],
        "active_themes": [],
        "open_loops": [],
    }


def extract_derived_profile(memory: dict[str, Any]) -> dict[str, Any]:
    """Return normalized Tier 2 blob from durable memory."""
    raw = memory.get(TIER2_ROOT_KEY) if memory else None
    if not isinstance(raw, dict):
        return empty_derived_profile()
    return normalize_derived_profile(raw)


def normalize_derived_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Sanitize stored derived_profile."""
    out = empty_derived_profile()
    insights_raw = raw.get("insights")
    if isinstance(insights_raw, list):
        for item in insights_raw:
            if not isinstance(item, dict):
                continue
            validated = _validate_insight(item, blocklist=set())
            if validated:
                out["insights"].append(validated)
    out["insights"] = out["insights"][:MAX_INSIGHTS]

    for key, cap in (("active_themes", MAX_THEMES), ("open_loops", MAX_OPEN_LOOPS)):
        values = raw.get(key)
        if isinstance(values, list):
            cleaned = [
                str(v).strip()[:MAX_LABEL_LEN]
                for v in values
                if v is not None and str(v).strip()
            ]
            out[key] = cleaned[:cap]

    if not out["active_themes"]:
        out["active_themes"] = _themes_from_insights(out["insights"])
    return out


def _validate_insight(
    item: dict[str, Any],
    *,
    blocklist: set[str],
    source_reflection_ids: frozenset[str] | None = None,
    consent_epoch: str | None = None,
) -> dict[str, Any] | None:
    insight_type = str(item.get("type", "")).lower().strip()
    if insight_type not in INSIGHT_TYPES:
        return None

    label = str(item.get("label", "")).strip()[:MAX_LABEL_LEN]
    if len(label) < 8:
        return None

    try:
        confidence = float(item.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if confidence < MIN_CONFIDENCE:
        return None

    label_lower = label.lower()
    if any(token in label_lower for token in blocklist):
        return None
    if '"' in label or "“" in label or "”" in label:
        return None

    ids_raw = item.get("source_reflection_ids")
    ids: list[str] = []
    if isinstance(ids_raw, list):
        ids = [str(x) for x in ids_raw if x][:20]
    if source_reflection_ids is not None:
        ids = [rid for rid in ids if rid in source_reflection_ids]
        if not ids:
            ids = sorted(source_reflection_ids)[:20]

    insight_id = str(item.get("id") or uuid.uuid4())
    return {
        "id": insight_id,
        "type": insight_type,
        "label": label,
        "confidence": round(min(confidence, 1.0), 2),
        "source_reflection_ids": ids,
        "consent_epoch": str(item.get("consent_epoch") or consent_epoch or _now_iso()),
        "last_reinforced_at": str(item.get("last_reinforced_at") or _now_iso()),
        "expires_at": item.get("expires_at"),
    }


def build_blocklist_from_tier0(tier0_text: str) -> set[str]:
    """Tokens from ephemeral journal context — must not appear in distilled labels."""
    if not tier0_text:
        return set()
    tokens = {m.group(0).lower() for m in _WORD_RE.finditer(tier0_text)}
    return {t for t in tokens if len(t) >= 4}


def _themes_from_insights(insights: list[dict[str, Any]]) -> list[str]:
    themes: list[str] = []
    for ins in insights:
        if ins.get("type") == "theme":
            label = str(ins.get("label", "")).strip()
            if label and label not in themes:
                themes.append(label[:MAX_LABEL_LEN])
    return themes[:MAX_THEMES]


def _label_key(label: str) -> str:
    return re.sub(r"\s+", " ", label.lower().strip())


def merge_derived_profiles(
    existing: dict[str, Any],
    candidate: dict[str, Any],
    *,
    source_reflection_ids: frozenset[str],
    consent_epoch: str,
    blocklist: set[str],
) -> dict[str, Any]:
    """Merge validated candidate insights into existing derived profile."""
    base = normalize_derived_profile(existing)
    merged_insights: list[dict[str, Any]] = list(base["insights"])
    by_key = {_label_key(str(i.get("label", ""))): i for i in merged_insights}

    for raw in candidate.get("insights") or []:
        if not isinstance(raw, dict):
            continue
        validated = _validate_insight(
            raw,
            blocklist=blocklist,
            source_reflection_ids=source_reflection_ids,
            consent_epoch=consent_epoch,
        )
        if not validated:
            continue
        key = _label_key(validated["label"])
        if key in by_key:
            prior = by_key[key]
            prior["confidence"] = round(min(1.0, float(prior["confidence"]) + 0.08), 2)
            prior["last_reinforced_at"] = _now_iso()
            prior_ids = set(prior.get("source_reflection_ids") or [])
            prior_ids.update(validated.get("source_reflection_ids") or [])
            prior["source_reflection_ids"] = sorted(prior_ids)[:20]
        else:
            merged_insights.append(validated)
            by_key[key] = validated

    merged_insights.sort(key=lambda i: float(i.get("confidence", 0)), reverse=True)
    merged_insights = merged_insights[:MAX_INSIGHTS]

    active_themes = candidate.get("active_themes")
    if isinstance(active_themes, list) and active_themes:
        theme_list = [
            str(t).strip()[:MAX_LABEL_LEN]
            for t in active_themes
            if t and str(t).strip()
        ]
    else:
        theme_list = _themes_from_insights(merged_insights)

    open_loops_raw = candidate.get("open_loops")
    open_loops: list[str] = list(base.get("open_loops") or [])
    if isinstance(open_loops_raw, list):
        for loop in open_loops_raw:
            text = str(loop).strip()[:MAX_LABEL_LEN]
            if text and text not in open_loops:
                open_loops.append(text)
    open_loops = open_loops[:MAX_OPEN_LOOPS]

    return {
        "version": DERIVED_PROFILE_VERSION,
        "insights": merged_insights,
        "active_themes": theme_list[:MAX_THEMES],
        "open_loops": open_loops,
    }


def purge_insights_for_reflection(
    derived: dict[str, Any],
    reflection_id: str,
) -> dict[str, Any]:
    """Remove insights linked to a revoked reflection and recompute themes."""
    base = normalize_derived_profile(derived)
    rid = str(reflection_id)
    kept = [
        ins
        for ins in base["insights"]
        if rid not in (ins.get("source_reflection_ids") or [])
    ]
    base["insights"] = kept
    base["active_themes"] = _themes_from_insights(kept)
    return base


def delete_insight_by_id(derived: dict[str, Any], insight_id: str) -> dict[str, Any]:
    base = normalize_derived_profile(derived)
    iid = str(insight_id)
    base["insights"] = [ins for ins in base["insights"] if str(ins.get("id")) != iid]
    base["active_themes"] = _themes_from_insights(base["insights"])
    return base


def session_summary_from_profile(derived: dict[str, Any]) -> str:
    """Tier-2-conform session summary for agent_sessions (no raw LLM text)."""
    profile = normalize_derived_profile(derived)
    labels = [str(i.get("label", "")) for i in profile["insights"][:5] if i.get("label")]
    if not labels:
        return "Session completed (no derived insights stored)."
    return "Derived patterns: " + "; ".join(labels)[:4000]


SESSION_INSIGHT_SNAPSHOT_KEY = "session_insight_snapshot"


def append_skill_insights(
    existing: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    source_reflection_ids: frozenset[str],
    consent_epoch: str,
    blocklist: set[str] | None = None,
) -> dict[str, Any]:
    """Merge skill-produced insight candidates into derived_profile."""
    if not candidates:
        return normalize_derived_profile(existing)
    return merge_derived_profiles(
        existing,
        {"insights": candidates},
        source_reflection_ids=source_reflection_ids,
        consent_epoch=consent_epoch,
        blocklist=blocklist or set(),
    )


def build_session_insight_snapshot(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    max_items: int = 5,
) -> list[dict[str, str]]:
    """Insights added or reinforced between session start profile and final profile."""
    before_norm = normalize_derived_profile(before)
    after_norm = normalize_derived_profile(after)
    before_by_key = {
        _label_key(str(i.get("label", ""))): i for i in before_norm.get("insights") or []
    }
    snapshots: list[dict[str, str]] = []
    for insight in after_norm.get("insights") or []:
        if not isinstance(insight, dict):
            continue
        label = str(insight.get("label", "")).strip()
        if len(label) < 8:
            continue
        key = _label_key(label)
        prior = before_by_key.get(key)
        is_new = prior is None
        reinforced = False
        if prior is not None:
            try:
                reinforced = float(insight.get("confidence", 0)) > float(prior.get("confidence", 0))
            except (TypeError, ValueError):
                reinforced = False
        if not is_new and not reinforced:
            continue
        snapshots.append(
            {
                "id": str(insight.get("id", "")),
                "type": str(insight.get("type", "theme")),
                "label": label[:MAX_LABEL_LEN],
            }
        )
        if len(snapshots) >= max_items:
            break
    return snapshots


CATALOG_INSIGHT_CAP = 20

# Abstract keep-apart bias. Last in the distill system prompt so it wins over locale.
CATALOG_KEEP_APART_RULES = (
    "Catalog reuse: if this text is the same lived mechanism as an existing catalog label, "
    "return that EXACT stored label and type so it can be reinforced. "
    "If an existing catalog label is the same mechanism, output that exact stored string. "
    "Do not translate stored labels. "
    "If the friction is different, invent a new label even when they share a word. "
    "If uncertain, keep them apart — do not merge to tidy the list. "
    "Example: waiting before acting (impulse control) is distinct from "
    "resting to recover energy (recovery)."
)

# Cue vs reaction vs story. Schema lists trigger but the model skips it unless defined.
# Reuse still keeps a stored type; this only applies when inventing a new insight.
INSIGHT_TYPE_RULES = (
    "Types — pick exactly one per insight. "
    "trigger: the cue that sets a pattern off (a situation, silence, a look, a time of day) "
    "— not the reaction. "
    "habit: what you then do or avoid (reaching for the phone, shutting down). "
    "theme: a recurring inner story or meaning. "
    "emotion: a feeling that keeps showing up. "
    "goal: what you are moving toward or away from. "
    "When both a cue and a reaction are present, emit two insights (trigger + habit), "
    "not one lumped habit. "
    "When inventing a new insight, type cues as trigger even if a related habit already "
    "exists in the catalog."
)


def distill_locale_output_instruction(locale: str | None = None) -> str:
    """Distill-only locale. New labels follow Profile locale; stored catalog strings stay.

    Do not use locale_output_instruction here — that one tells Discord replies AND
    labels to switch language.
    """
    from utils.platform_locale import normalize_platform_locale

    loc = normalize_platform_locale(locale)
    language = "Belgian Dutch (nl-BE)" if loc == "nl-BE" else "English"
    return (
        f"Platform locale: {loc}. Invent NEW pattern labels in {language} "
        "unless the user clearly writes in another language. "
        "If an existing catalog label is the same mechanism, output that exact stored string. "
        "Do not translate stored labels."
    )


def distill_catalog_system_rules(locale: str | None = None) -> str:
    """Type + locale-for-new + keep-apart. Keep-apart last. Used by /agent end distill."""
    return (
        f"{INSIGHT_TYPE_RULES} "
        f"{distill_locale_output_instruction(locale)} "
        f"{CATALOG_KEEP_APART_RULES}"
    )


def format_catalog_for_distill(existing: dict[str, Any], *, cap: int = CATALOG_INSIGHT_CAP) -> str:
    """Compact label+type list for distill prompts (empty when catalog is empty)."""
    profile = normalize_derived_profile(existing)
    lines: list[str] = []
    for ins in profile.get("insights") or []:
        if not isinstance(ins, dict):
            continue
        label = str(ins.get("label") or "").strip()[:MAX_LABEL_LEN]
        if len(label) < 8:
            continue
        insight_type = str(ins.get("type") or "theme").strip().lower() or "theme"
        lines.append(f"- {label} ({insight_type})")
        if len(lines) >= cap:
            break
    return "\n".join(lines)


def catalog_user_block(catalog_lines: str) -> str:
    body = (catalog_lines or "").strip()
    if not body:
        return ""
    return (
        "Existing catalog (reuse the exact stored label when the mechanism matches):\n"
        + body
    )


def with_catalog_user_message(base_user: str, catalog_lines: str) -> str:
    block = catalog_user_block(catalog_lines)
    if not block:
        return base_user
    return f"{block}\n\n{base_user}"


def _parse_distill_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


async def distill_session_profile(
    *,
    tier0_context: str,
    user_message: str,
    agent_response: str,
    source_reflection_ids: frozenset[str],
    existing: dict[str, Any],
    discord_user_id: int,
    guild_id: int | None,
    platform_locale: str = "en",
) -> dict[str, Any] | None:
    """
    Run LLM distill step; return merged derived_profile or None (fail closed).

    Shared-journal distill still passes consent IDs + tier0. Session write-back
    may pass an empty ID set and empty journal when the session transcript is
    the only source.
    """
    if not (
        (tier0_context or "").strip()
        or (user_message or "").strip()
        or (agent_response or "").strip()
    ):
        return None

    blocklist = build_blocklist_from_tier0(tier0_context or "")
    consent_epoch = _now_iso()

    catalog_lines = format_catalog_for_distill(existing)
    system = (
        "You extract generalized reflection patterns for a private coaching agent. "
        "Return ONLY valid JSON, no markdown. Schema:\n"
        '{"insights":[{"type":"theme|emotion|goal|habit|trigger",'
        '"label":"generalized pattern under 120 chars","confidence":0.0-1.0}],'
        '"active_themes":["short theme"],'
        '"open_loops":["optional gentle follow-up without quotes"]}\n'
        "Rules: NO quotes from journals; NO dates; NO mantras; NO names; "
        "labels must be abstract patterns only; omit insights below 0.6 confidence. "
        f"{distill_catalog_system_rules(platform_locale)}"
    )
    linked_ids = ", ".join(sorted(source_reflection_ids)[:10])
    linked_line = (
        f"Linked reflection IDs (metadata only): {linked_ids}"
        if linked_ids
        else "Linked reflection IDs (metadata only): none (session transcript only)"
    )
    journal_block = (tier0_context or "").strip()[:2000] or "(none)"
    user = with_catalog_user_message(
        (
            f"Ephemeral journal context (do not quote):\n{journal_block}\n\n"
            f"User request: {user_message[:500]}\n\n"
            f"Agent reply (do not quote): {agent_response[:1500]}\n\n"
            f"{linked_line}"
        ),
        catalog_lines,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        raw = await ask_gpt(
            messages,
            user_id=discord_user_id,
            guild_id=guild_id,
            include_reflections=False,
        )
    except Exception as exc:
        logger.warning("Tier 2 distill LLM failed: %s", exc)
        return None

    parsed = _parse_distill_json(raw or "")
    if not parsed:
        logger.info("Tier 2 distill returned non-JSON; skipping write")
        return None

    # Validate at least one insight or theme before merge
    candidate_insights = parsed.get("insights") if isinstance(parsed.get("insights"), list) else []
    has_valid = any(
        _validate_insight(i, blocklist=blocklist, source_reflection_ids=source_reflection_ids)
        for i in candidate_insights
        if isinstance(i, dict)
    )
    has_themes = bool(parsed.get("active_themes"))
    if not has_valid and not has_themes:
        logger.info("Tier 2 distill produced no valid insights; skipping write")
        return None

    merged = merge_derived_profiles(
        existing,
        parsed,
        source_reflection_ids=source_reflection_ids,
        consent_epoch=consent_epoch,
        blocklist=blocklist,
    )
    if not merged.get("insights") and not merged.get("active_themes"):
        return None
    return merged
