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
_MESSAGES_TABLE = "agent_session_messages"

# In-memory fallback for tests and when Supabase is not configured.
_local_sessions: dict[str, dict[str, Any]] = {}
_local_memory: dict[str, dict[str, Any]] = {}
_local_session_messages: dict[str, list[dict[str, Any]]] = {}


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


async def purge_agent_user_data(
    innersync_user_id: str | None = None,
    *,
    discord_user_id: int | None = None,
) -> None:
    """GDPR erasure: remove all agent sessions, messages, and durable memory for a user."""
    if not innersync_user_id and discord_user_id is None:
        raise ValueError("purge_agent_user_data requires innersync_user_id and/or discord_user_id")

    if not _use_supabase():
        _purge_agent_user_data_local(innersync_user_id, discord_user_id=discord_user_id)
        return

    user_ids = await _collect_innersync_user_ids_for_purge(innersync_user_id, discord_user_id)
    await _delete_agent_sessions(innersync_user_id, discord_user_id=discord_user_id)
    for uid in user_ids:
        await clear_all_user_memory(uid)

    logger.info(
        "GDPR agent purge complete: innersync_user_id=%s discord_user_id=%s",
        innersync_user_id,
        discord_user_id,
    )


def _purge_agent_user_data_local(
    innersync_user_id: str | None,
    *,
    discord_user_id: int | None,
) -> None:
    discord_text = str(discord_user_id) if discord_user_id is not None else None
    target_innersync = {innersync_user_id.lower()} if innersync_user_id else set()

    for session_id, row in list(_local_sessions.items()):
        row_innersync = str(row.get("innersync_user_id", "")).lower()
        row_discord = str(row.get("discord_user_id", ""))
        if (
            (innersync_user_id and row_innersync == innersync_user_id.lower())
            or (discord_text and row_discord == discord_text)
        ):
            target_innersync.add(row_innersync)
            _local_sessions.pop(session_id, None)
            _local_session_messages.pop(session_id, None)

    for uid in target_innersync:
        prefix = f"{uid}:"
        for key in list(_local_memory.keys()):
            if key.startswith(prefix):
                _local_memory.pop(key, None)


async def _collect_innersync_user_ids_for_purge(
    innersync_user_id: str | None,
    discord_user_id: int | None,
) -> set[str]:
    ids: set[str] = set()
    if innersync_user_id:
        ids.add(innersync_user_id)

    if discord_user_id is None:
        return ids

    rows = await _supabase_get(
        _SESSIONS_TABLE,
        {
            "select": "innersync_user_id",
            "discord_user_id": f"eq.{discord_user_id}",
        },
    )
    for row in rows:
        uid = row.get("innersync_user_id")
        if uid:
            ids.add(str(uid))
    return ids


async def _delete_agent_sessions(
    innersync_user_id: str | None,
    *,
    discord_user_id: int | None,
) -> None:
    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_SESSIONS_TABLE}"
    if innersync_user_id:
        params = {"innersync_user_id": f"eq.{innersync_user_id}"}
    elif discord_user_id is not None:
        params = {"discord_user_id": f"eq.{discord_user_id}"}
    else:
        return

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


async def get_session_by_id(session_id: str) -> dict[str, Any] | None:
    """Load a session row by primary key."""
    if not _use_supabase():
        return _local_sessions.get(session_id)

    rows = await _supabase_get(
        _SESSIONS_TABLE,
        {
            "select": "id,innersync_user_id,discord_user_id,guild_id,agent_name,status,metadata,started_at",
            "id": f"eq.{session_id}",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def patch_session_metadata(session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge patch into session metadata and persist."""
    row = await get_session_by_id(session_id)
    if not row:
        raise ValueError(f"Session not found: {session_id}")

    current = row.get("metadata") or {}
    if not isinstance(current, dict):
        current = {}
    merged = {**current, **patch}

    if not _use_supabase():
        stored = _local_sessions.get(session_id)
        if stored:
            stored["metadata"] = merged
        return merged

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_SESSIONS_TABLE}"
    params = {"id": f"eq.{session_id}"}
    payload = {"metadata": merged, "updated_at": _now_iso()}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.patch(
            url,
            json=payload,
            headers=_headers(prefer=["return=minimal"], method="PATCH"),
            params=params,
        )
        response.raise_for_status()
    return merged


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


async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Load ordered turn messages for an active session."""
    if not _use_supabase():
        rows = list(_local_session_messages.get(session_id, []))
        rows.sort(key=lambda r: (int(r.get("turn_index", 0)), r.get("role", "")))
        return rows

    rows = await _supabase_get(
        _MESSAGES_TABLE,
        {
            "select": "turn_index,role,content,created_at",
            "session_id": f"eq.{session_id}",
            "order": "turn_index.asc,role.asc",
        },
    )
    return rows


async def append_session_messages(
    session_id: str,
    *,
    turn_index: int,
    user_content: str,
    assistant_content: str,
) -> None:
    """Persist one user/assistant pair for a session turn."""
    now = _now_iso()
    user_row = {
        "session_id": session_id,
        "turn_index": turn_index,
        "role": "user",
        "content": user_content[:8000],
        "created_at": now,
    }
    assistant_row = {
        "session_id": session_id,
        "turn_index": turn_index,
        "role": "assistant",
        "content": assistant_content[:8000],
        "created_at": now,
    }

    if not _use_supabase():
        bucket = _local_session_messages.setdefault(session_id, [])
        bucket.append(user_row)
        bucket.append(assistant_row)
        return

    await _supabase_post(_MESSAGES_TABLE, [user_row, assistant_row], upsert=False)


async def delete_session_messages(session_id: str) -> None:
    """Remove ephemeral messages when a session ends."""
    if not _use_supabase():
        _local_session_messages.pop(session_id, None)
        return

    url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1/{_MESSAGES_TABLE}"
    params = {"session_id": f"eq.{session_id}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.delete(
            url,
            headers=_headers(prefer=["return=minimal"]),
            params=params,
        )
        response.raise_for_status()


async def touch_session(session_id: str) -> None:
    """Bump updated_at on an active session."""
    payload = {"updated_at": _now_iso()}
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


def clear_local_store() -> None:
    """Test helper."""
    _local_sessions.clear()
    _local_memory.clear()
    _local_session_messages.clear()
