"""Persistent agent session and memory store (Supabase with in-memory fallback)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.supabase_client import (
    SupabaseConfigurationError,
    _headers,
    _require_config,
    _supabase_get,
    _supabase_post,
)

logger = logging.getLogger("alphapy.agents.memory")

_MEMORY_TABLE = "agent_memory"
_SESSIONS_TABLE = "agent_sessions"

# In-memory fallback for tests and when Supabase is not configured.
_local_sessions: dict[str, dict[str, Any]] = {}
_local_memory: dict[str, dict[str, Any]] = {}


def _memory_key(innersync_user_id: str, agent_name: str) -> str:
    return f"{innersync_user_id.lower()}:{agent_name}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _use_supabase() -> bool:
    backend = getattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "supabase")
    if backend == "memory":
        return False
    try:
        _require_config()
        return True
    except SupabaseConfigurationError:
        return False


async def get_user_memory(innersync_user_id: str, agent_name: str) -> dict[str, Any]:
    """Load durable memory blob for user+agent."""
    if not _use_supabase():
        return dict(_local_memory.get(_memory_key(innersync_user_id, agent_name), {}))

    rows = await _supabase_get(
        _MEMORY_TABLE,
        {
            "select": "memory",
            "innersync_user_id": f"eq.{innersync_user_id}",
            "agent_name": f"eq.{agent_name}",
            "limit": 1,
        },
    )
    if not rows:
        return {}
    memory = rows[0].get("memory")
    return dict(memory) if isinstance(memory, dict) else {}


async def patch_user_memory(
    innersync_user_id: str,
    agent_name: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge patch into durable memory and return the updated blob."""
    current = await get_user_memory(innersync_user_id, agent_name)
    current.update(patch)

    if not _use_supabase():
        _local_memory[_memory_key(innersync_user_id, agent_name)] = current
        return current

    await _supabase_post(
        _MEMORY_TABLE,
        {
            "innersync_user_id": innersync_user_id,
            "agent_name": agent_name,
            "memory": current,
            "updated_at": _now_iso(),
        },
        upsert=True,
    )
    return current


async def create_session(
    *,
    innersync_user_id: str,
    discord_user_id: int,
    guild_id: int | None,
    agent_name: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    session_id = str(uuid.uuid4())
    row = {
        "id": session_id,
        "innersync_user_id": innersync_user_id,
        "discord_user_id": str(discord_user_id),
        "guild_id": str(guild_id) if guild_id is not None else None,
        "agent_name": agent_name,
        "status": "active",
        "metadata": metadata or {},
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    if not _use_supabase():
        _local_sessions[session_id] = row
        return session_id

    await _supabase_post(_SESSIONS_TABLE, row, upsert=False)
    return session_id


async def complete_session(
    session_id: str,
    *,
    status: str = "completed",
    summary: str | None = None,
    memory_patch: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "updated_at": _now_iso(),
        "completed_at": _now_iso(),
    }
    if summary is not None:
        payload["summary"] = summary[:4000]
    if memory_patch is not None:
        payload["memory_patch"] = memory_patch

    if not _use_supabase():
        row = _local_sessions.get(session_id)
        if row:
            row.update(payload)
        return

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_SESSIONS_TABLE}"
    params = {"id": f"eq.{session_id}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.patch(
            url,
            json=payload,
            headers=_headers(prefer=["return=minimal"], method="PATCH"),
            params=params,
        )
        response.raise_for_status()


async def get_active_session(
    innersync_user_id: str,
    agent_name: str,
) -> dict[str, Any] | None:
    if not _use_supabase():
        for row in _local_sessions.values():
            if (
                row.get("innersync_user_id") == innersync_user_id
                and row.get("agent_name") == agent_name
                and row.get("status") == "active"
            ):
                return row
        return None

    rows = await _supabase_get(
        _SESSIONS_TABLE,
        {
            "select": "id,agent_name,status,started_at,metadata",
            "innersync_user_id": f"eq.{innersync_user_id}",
            "agent_name": f"eq.{agent_name}",
            "status": "eq.active",
            "order": "started_at.desc",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


def clear_local_store() -> None:
    """Test helper."""
    _local_sessions.clear()
    _local_memory.clear()
