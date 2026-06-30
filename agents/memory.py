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

from agents.profile import TIER3_FIELDS
from agents.tier2 import TIER2_ROOT_KEY, extract_derived_profile, purge_insights_for_reflection
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


# Keys that may contain journal/reflection plaintext — never persist or inject.
_SENSITIVE_MEMORY_KEYS = frozenset(
    {
        "last_summary_preview",
        "journal_notes",
        "last_reflection_preview",
    }
)

_DURABLE_MEMORY_KEYS = TIER3_FIELDS | {TIER2_ROOT_KEY}


def _memory_key(innersync_user_id: str, agent_name: str) -> str:
    return f"{innersync_user_id.lower()}:{agent_name}"


def strip_sensitive_memory_keys(memory: dict[str, Any]) -> dict[str, Any]:
    """Return memory without keys that can carry opted-out journal content."""
    if not memory:
        return {}
    cleaned = {k: v for k, v in memory.items() if k not in _SENSITIVE_MEMORY_KEYS}
    return {k: v for k, v in cleaned.items() if k in _DURABLE_MEMORY_KEYS}


async def clear_all_user_memory(innersync_user_id: str) -> None:
    """Delete all agent_memory rows for a user (all agents)."""
    if not _use_supabase():
        prefix = f"{innersync_user_id.lower()}:"
        for key in list(_local_memory.keys()):
            if key.startswith(prefix):
                _local_memory.pop(key, None)
        return

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_MEMORY_TABLE}"
    params = {"innersync_user_id": f"eq.{innersync_user_id}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(
            url,
            headers=_headers(prefer=["return=minimal"]),
            params=params,
        )
        response.raise_for_status()


async def clear_derived_memory(innersync_user_id: str, agent_name: str) -> dict[str, Any]:
    """Remove Tier 2 derived profile; keep Tier 3 operational metadata."""
    current = strip_sensitive_memory_keys(await get_user_memory(innersync_user_id, agent_name))
    current.pop(TIER2_ROOT_KEY, None)
    return await _write_user_memory(innersync_user_id, agent_name, current)


async def purge_tier2_for_reflection(
    innersync_user_id: str,
    agent_name: str,
    reflection_id: str,
) -> dict[str, Any]:
    """Drop insights linked to a revoked reflection."""
    current = strip_sensitive_memory_keys(await get_user_memory(innersync_user_id, agent_name))
    derived = extract_derived_profile(current)
    updated = purge_insights_for_reflection(derived, reflection_id)
    if updated.get("insights"):
        current[TIER2_ROOT_KEY] = updated
    else:
        current.pop(TIER2_ROOT_KEY, None)
    return await _write_user_memory(innersync_user_id, agent_name, current)


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


async def clear_user_memory(innersync_user_id: str, agent_name: str) -> None:
    """Delete durable memory for user+agent (e.g. after revoking all shares)."""
    if not _use_supabase():
        _local_memory.pop(_memory_key(innersync_user_id, agent_name), None)
        return

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_MEMORY_TABLE}"
    params = {
        "innersync_user_id": f"eq.{innersync_user_id}",
        "agent_name": f"eq.{agent_name}",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(
            url,
            headers=_headers(prefer=["return=minimal"]),
            params=params,
        )
        response.raise_for_status()


async def _write_user_memory(
    innersync_user_id: str,
    agent_name: str,
    memory: dict[str, Any],
) -> dict[str, Any]:
    if not _use_supabase():
        _local_memory[_memory_key(innersync_user_id, agent_name)] = memory
        return memory

    await _supabase_post(
        _MEMORY_TABLE,
        {
            "innersync_user_id": innersync_user_id,
            "agent_name": agent_name,
            "memory": memory,
            "updated_at": _now_iso(),
        },
        upsert=True,
    )
    return memory


async def patch_user_memory(
    innersync_user_id: str,
    agent_name: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge patch into durable memory and return the updated blob."""
    current = strip_sensitive_memory_keys(await get_user_memory(innersync_user_id, agent_name))
    safe_patch = strip_sensitive_memory_keys(patch)
    for key, value in safe_patch.items():
        if key in TIER3_FIELDS:
            current[key] = value
        elif key == TIER2_ROOT_KEY and isinstance(value, dict):
            current[TIER2_ROOT_KEY] = value
    return await _write_user_memory(innersync_user_id, agent_name, current)


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
