"""Fetch self-improving pattern nodes for agent context."""

from __future__ import annotations

import logging
from typing import Any

from agents.profile import learn_from_shared_enabled
from utils.supabase_client import (
    SupabaseConfigurationError,
    _require_config,
    _supabase_get,
)

logger = logging.getLogger("alphapy.agents.pattern_loader")

_GRAPH_NODES_TABLE = "agent_graph_nodes"
_PATTERN_CONTEXT_MAX = 1500


async def _fetch_pattern_nodes(innersync_user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        _require_config()
    except SupabaseConfigurationError:
        return []

    # Over-fetch then drop ops tallies so user progress still fills the context window.
    fetch_limit = max(limit * 3, 15)
    path = (
        f"/rest/v1/{_GRAPH_NODES_TABLE}"
        f"?innersync_user_id=eq.{innersync_user_id}"
        f"&node_type=eq.pattern"
        f"&order=usage_count.desc,updated_at.desc"
        f"&limit={fetch_limit}"
        f"&select=label,body_md,memory_tree_path,usage_count,metadata"
    )
    try:
        rows = await _supabase_get(path)
    except Exception as exc:
        logger.debug("Pattern node fetch failed: %s", exc)
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if not _is_ops_telemetry_node(row)][:limit]


def _is_ops_telemetry_node(node: dict[str, Any]) -> bool:
    """Skip Hermit gpt_command / agents/ tallies — only user progress belongs in prompts."""
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    kind = str(meta.get("kind") or "").strip().lower()
    source = str(meta.get("source") or "").strip().lower()
    if kind == "user_progress" or source == "growthcheckin":
        return False
    path = str(node.get("memory_tree_path") or "").lstrip("/").lower()
    if path.startswith("agents/"):
        return True
    label = str(node.get("label") or "").strip().lower()
    if label.endswith(" dominance") or "gpt_command" in label:
        return True
    if meta.get("event_type") and kind != "user_progress":
        return True
    body = str(node.get("body_md") or "").lower()
    return "ops event snapshot" in body or "dominant event:" in body


async def load_pattern_context(
    innersync_user_id: str,
    prefs: dict[str, Any],
) -> str | None:
    """
    Load Tier-2-safe pattern summaries when user opted into learning from patterns.
    Falls back to learn_from_shared for backward compatibility.
    """
    learn = prefs.get("learn_from_patterns")
    if learn is None:
        learn = learn_from_shared_enabled(prefs)
    if not learn:
        return None

    nodes = await _fetch_pattern_nodes(innersync_user_id)
    if not nodes:
        return None

    lines = ["[learned_patterns]"]
    for node in nodes:
        label = str(node.get("label", "pattern"))[:120]
        body = str(node.get("body_md") or "").strip()[:400]
        if body:
            lines.append(f"- {label}: {body}")
        else:
            lines.append(f"- {label}")
    block = "\n".join(lines)
    return block[:_PATTERN_CONTEXT_MAX] if block.strip() else None
