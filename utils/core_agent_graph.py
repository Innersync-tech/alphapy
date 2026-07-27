"""Push Memory Vault progress nodes to Core (agent_chat source).

Labels only — never session transcripts or journal plaintext.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.logger import logger

_TIMEOUT = 12.0
# Lowercase labels before match; one Latin-1 letter range avoids CodeQL py/overly-large-range
# (à-ÿ and À-Ÿ overlapped in the same class).
_TOKEN_RE = re.compile(r"[a-z0-9à-ÿ]+")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _core_url() -> str:
    return (getattr(config, "CORE_API_URL", None) or "").rstrip("/")


def _service_key() -> str:
    return (getattr(config, "ALPHAPY_SERVICE_KEY", None) or "").strip()


def memory_graph_push_enabled() -> bool:
    """Default on when Core URL + service key exist; override with ALPHAPY_MEMORY_GRAPH_PUSH."""
    raw = os.environ.get("ALPHAPY_MEMORY_GRAPH_PUSH")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return bool(_core_url() and _service_key())


def _theme_key(label: str) -> str:
    parts = _TOKEN_RE.findall((label or "").strip().lower())
    key = "-".join(parts)
    return key[:64].strip("-")


def build_agent_chat_progress_payload(
    *,
    discord_user_id: int,
    session_id: str,
    insight_snapshot: list[dict[str, Any]] | None,
    active_themes: list[Any] | None = None,
    day: str | None = None,
) -> dict[str, Any] | None:
    """
    Build Core platform write body from Tier-2 labels only.

    Returns None when there is nothing meaningful to write.
    Guarantees no message/transcript fields in the payload.
    """
    themes: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        label = str(raw or "").strip()
        if len(label) < 4:
            return
        key = _theme_key(label)
        if not key or key in seen:
            return
        seen.add(key)
        themes.append(label[:120])

    for item in insight_snapshot or []:
        if isinstance(item, dict):
            _add(item.get("label"))
        else:
            _add(item)
    for theme in active_themes or []:
        _add(theme)

    themes = themes[:5]
    if not themes:
        return None

    day_s = (day or datetime.now(UTC).date().isoformat()).strip()
    if not _DAY_RE.match(day_s):
        day_s = datetime.now(UTC).date().isoformat()

    theme_keys = [_theme_key(t) for t in themes if _theme_key(t)][:5]
    label = f"progress {day_s}: {', '.join(themes[:3])}"[:500]
    return {
        "user_id": str(int(discord_user_id)),
        "writer": "alphapy",
        "nodes": [
            {
                "node_type": "pattern",
                "label": label,
                "usage_count": max(1, len(themes)),
                "metadata": {
                    "kind": "user_progress",
                    "source": "agent_chat",
                    "day": day_s,
                    "themes": themes,
                    "theme_keys": theme_keys,
                    "theme_source": "tier2",
                    "content_ref": f"agent_session:{session_id}",
                },
            }
        ],
    }


async def push_agent_chat_progress(
    *,
    discord_user_id: int,
    session_id: str,
    insight_snapshot: list[dict[str, Any]] | None,
    active_themes: list[Any] | None = None,
) -> bool:
    """POST metadata-only progress node to Core. Fail-open; never raises."""
    try:
        if not memory_graph_push_enabled():
            return False
        base = _core_url()
        key = _service_key()
        if not base or not key:
            return False
        body = build_agent_chat_progress_payload(
            discord_user_id=discord_user_id,
            session_id=session_id,
            insight_snapshot=insight_snapshot,
            active_themes=active_themes,
        )
        if not body:
            return False
        meta = body["nodes"][0].get("metadata") or {}
        for banned in ("user_transcript", "assistant_transcript", "messages", "plaintext"):
            if banned in meta:
                logger.warning("agent-graph push aborted: banned metadata key %s", banned)
                return False
        url = f"{base}/integrations/platform/agent-graph/write"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                url,
                json=body,
                headers={"X-API-Key": key, "Accept": "application/json"},
            )
        if res.status_code >= 400:
            logger.warning(
                "agent-graph push failed status=%s theme_keys=%d",
                res.status_code,
                len(body["nodes"][0]["metadata"].get("theme_keys") or []),
            )
            return False
        logger.info(
            "agent-graph push ok writer=alphapy theme_keys=%d",
            len(body["nodes"][0]["metadata"].get("theme_keys") or []),
        )
        return True
    except Exception as exc:
        logger.warning("agent-graph push error: %s", exc)
        return False
