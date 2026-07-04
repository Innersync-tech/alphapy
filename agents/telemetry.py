"""Agent session metrics for Alphapy telemetry ingest (Phase 4.0 observability)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from agents.memory import _SESSIONS_TABLE, _use_supabase
from utils.supabase_client import SupabaseConfigurationError, _headers, _require_config

logger = logging.getLogger("alphapy.agents.telemetry")


@dataclass(frozen=True, slots=True)
class AgentSessionMetrics:
    """Aggregate agent session counts for telemetry (no user content)."""

    agents_enabled: bool
    active_sessions: int = 0
    started_24h: int = 0
    completed_24h: int = 0
    active_origin_discord: int = 0
    active_origin_app: int = 0

    def has_activity(self) -> bool:
        return any(
            (
                self.active_sessions,
                self.started_24h,
                self.completed_24h,
            )
        )


def _agents_globally_enabled() -> bool:
    return bool(getattr(config, "ALPHAPY_AGENTS_ENABLED", False))


def _cutoff_iso(hours: int = 24) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def _supabase_count(table: str, params: dict[str, str]) -> int:
    _require_config()
    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{table}"
    query = {"select": "id", **params}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            url,
            headers=_headers(prefer=["count=exact"]),
            params=query,
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.debug("Agent metrics count failed: %s", exc.response.text[:200])
        return 0

    content_range = response.headers.get("content-range", "")
    if "/" not in content_range:
        return 0
    total = content_range.rsplit("/", 1)[-1]
    if total == "*":
        return 0
    try:
        return int(total)
    except ValueError:
        return 0


def _origin_channel(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    origin = metadata.get("origin_channel")
    if origin in {"discord", "app"}:
        return str(origin)
    return None


async def _metrics_from_local_store(cutoff: str) -> AgentSessionMetrics:
    from agents.memory import _local_sessions

    active = started_24h = completed_24h = 0
    origin_discord = origin_app = 0

    for row in _local_sessions.values():
        started_at = str(row.get("started_at", ""))
        status = row.get("status")
        if started_at >= cutoff:
            started_24h += 1
        if status == "completed":
            completed_at = str(row.get("completed_at") or started_at)
            if completed_at >= cutoff:
                completed_24h += 1
        if status != "active":
            continue
        active += 1
        origin = _origin_channel(row.get("metadata"))
        if origin == "discord":
            origin_discord += 1
        elif origin == "app":
            origin_app += 1

    return AgentSessionMetrics(
        agents_enabled=True,
        active_sessions=active,
        started_24h=started_24h,
        completed_24h=completed_24h,
        active_origin_discord=origin_discord,
        active_origin_app=origin_app,
    )


async def _metrics_from_supabase(cutoff: str) -> AgentSessionMetrics:
    active_sessions = await _supabase_count(
        _SESSIONS_TABLE,
        {"status": "eq.active"},
    )
    started_24h = await _supabase_count(
        _SESSIONS_TABLE,
        {"started_at": f"gte.{cutoff}"},
    )
    completed_24h = await _supabase_count(
        _SESSIONS_TABLE,
        {
            "status": "eq.completed",
            "completed_at": f"gte.{cutoff}",
        },
    )

    origin_discord = origin_app = 0
    try:
        from utils.supabase_client import _supabase_get

        active_rows = await _supabase_get(
            _SESSIONS_TABLE,
            {
                "select": "metadata",
                "status": "eq.active",
                "limit": 500,
            },
        )
        for row in active_rows:
            origin = _origin_channel(row.get("metadata"))
            if origin == "discord":
                origin_discord += 1
            elif origin == "app":
                origin_app += 1
    except Exception as exc:
        logger.debug("Agent metrics origin breakdown skipped: %s", exc)

    return AgentSessionMetrics(
        agents_enabled=True,
        active_sessions=active_sessions,
        started_24h=started_24h,
        completed_24h=completed_24h,
        active_origin_discord=origin_discord,
        active_origin_app=origin_app,
    )


async def collect_agent_session_metrics() -> AgentSessionMetrics:
    """Load aggregate agent session counts for telemetry snapshots."""
    enabled = _agents_globally_enabled()
    if not enabled:
        return AgentSessionMetrics(agents_enabled=False)

    cutoff = _cutoff_iso()

    if not _use_supabase():
        return await _metrics_from_local_store(cutoff)

    try:
        return await _metrics_from_supabase(cutoff)
    except SupabaseConfigurationError:
        return AgentSessionMetrics(agents_enabled=True)
    except Exception as exc:
        logger.debug("Agent session metrics collection failed: %s", exc)
        return AgentSessionMetrics(agents_enabled=True)


def format_agent_session_telemetry_notes(metrics: AgentSessionMetrics) -> str:
    """Human-readable agent metrics line for telemetry.subsystem_snapshots.notes."""
    if not metrics.agents_enabled:
        return "agents: disabled"

    parts = [
        f"{metrics.active_sessions} active",
        f"{metrics.started_24h} started/24h",
        f"{metrics.completed_24h} completed/24h",
    ]
    if metrics.active_sessions:
        parts.append(
            f"origin discord:{metrics.active_origin_discord} app:{metrics.active_origin_app}"
        )
    return "agents: " + " · ".join(parts)
