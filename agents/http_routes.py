"""HTTP API routes for cross-platform agent sessions (Phase 4.0)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import config
from agents.base import AgentResult
from agents.memory import get_active_session, get_session_by_id, get_session_messages
from agents.registry import list_agents, resolve_agent
from agents.runtime import (
    ActiveAgentSessionError,
    AgentSessionQuotaExceededError,
    NoActiveAgentSessionError,
    continue_agent_session,
    end_agent_session,
    start_agent_session,
)
from utils.hermit_events import emit_hermit_event


class AgentSessionStartRequest(BaseModel):
    agent: str = "reflection"
    message: str | None = None


class AgentSessionTurnRequest(BaseModel):
    message: str = Field(..., min_length=1)


class AgentMessageResponse(BaseModel):
    turn_index: int
    role: Literal["user", "assistant"]
    content: str


class AgentSessionResponse(BaseModel):
    session_id: str
    agent_name: str
    status: Literal["active", "completed"]
    turn_count: int
    display_name: str | None = None
    assistant_message: str
    origin_channel: str | None = None
    last_channel: str | None = None


class AgentActiveSessionResponse(AgentSessionResponse):
    messages: list[AgentMessageResponse] = Field(default_factory=list)


def _agents_globally_enabled() -> bool:
    return bool(getattr(config, "ALPHAPY_AGENTS_ENABLED", False))


def _require_agents_enabled() -> None:
    if not _agents_globally_enabled():
        raise HTTPException(
            status_code=503,
            detail="Agents are not enabled on this deployment.",
        )


def _metadata_channels(metadata: Any) -> tuple[str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None
    origin = metadata.get("origin_channel")
    last = metadata.get("last_channel")
    return (
        str(origin) if origin is not None else None,
        str(last) if last is not None else None,
    )


def _format_messages(rows: list[dict[str, Any]]) -> list[AgentMessageResponse]:
    formatted: list[AgentMessageResponse] = []
    for row in rows:
        role = row.get("role")
        if role not in {"user", "assistant"}:
            continue
        formatted.append(
            AgentMessageResponse(
                turn_index=int(row.get("turn_index", 0)),
                role=role,
                content=str(row.get("content", "")),
            )
        )
    return formatted


def _turn_count_from_messages(messages: list[AgentMessageResponse]) -> int:
    if not messages:
        return 0
    return len({message.turn_index for message in messages})


def _session_response(
    result: AgentResult,
    *,
    status: Literal["active", "completed"],
    metadata: dict[str, Any] | None = None,
    messages: list[AgentMessageResponse] | None = None,
    turn_count: int | None = None,
) -> AgentActiveSessionResponse:
    origin_channel, last_channel = _metadata_channels(metadata or {})
    effective_turn_count = turn_count if turn_count is not None else result.turn_count
    return AgentActiveSessionResponse(
        session_id=result.session_id,
        agent_name=result.agent_name,
        status=status,
        turn_count=effective_turn_count,
        display_name=result.display_name,
        assistant_message=result.summary,
        origin_channel=origin_channel,
        last_channel=last_channel,
        messages=messages or [],
    )


async def _load_owned_active_session(
    session_id: str,
    innersync_user_id: str,
) -> dict[str, Any]:
    session = await get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if str(session.get("innersync_user_id")) != innersync_user_id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    if session.get("status") != "active":
        raise HTTPException(status_code=409, detail="Session is not active.")
    return session


def _parse_guild_id(session: dict[str, Any]) -> int | None:
    raw = session.get("guild_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def include_agent_routes(
    api_router: APIRouter,
    *,
    get_authenticated_user_id: Callable[..., Awaitable[str]],
    require_discord_link: Callable[[str], Awaitable[int]],
) -> None:
    """Register /api/agents/* routes on the main API router."""

    @api_router.get("/agents/sessions/active", response_model=AgentActiveSessionResponse)
    async def get_active_agent_session(
        agent: str = Query("reflection", description="Agent name"),
        auth_user_id: str = Depends(get_authenticated_user_id),
    ) -> AgentActiveSessionResponse:
        _require_agents_enabled()
        await require_discord_link(auth_user_id)

        if resolve_agent(agent) is None:
            raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")

        active = await get_active_session(auth_user_id, agent)
        if not active:
            raise HTTPException(status_code=404, detail="No active session.")

        session_id = str(active["id"])
        messages = _format_messages(await get_session_messages(session_id))
        metadata = active.get("metadata") if isinstance(active.get("metadata"), dict) else {}

        last_assistant = ""
        for row in reversed(messages):
            if row.role == "assistant":
                last_assistant = row.content
                break

        turn_count = _turn_count_from_messages(messages)
        result = AgentResult(
            agent_name=agent,
            session_id=session_id,
            summary=last_assistant,
            skill_blocks={},
            turn_count=turn_count,
        )
        return _session_response(
            result,
            status="active",
            metadata=metadata,
            messages=messages,
            turn_count=turn_count,
        )

    @api_router.post("/agents/sessions", response_model=AgentSessionResponse, status_code=201)
    async def start_agent_session_http(
        body: AgentSessionStartRequest,
        auth_user_id: str = Depends(get_authenticated_user_id),
    ) -> AgentSessionResponse:
        _require_agents_enabled()
        discord_user_id = await require_discord_link(auth_user_id)

        agent_name = body.agent
        if resolve_agent(agent_name) is None:
            raise HTTPException(status_code=400, detail=f"Unknown agent: {agent_name}")

        try:
            result = await start_agent_session(
                innersync_user_id=auth_user_id,
                discord_user_id=discord_user_id,
                guild_id=None,
                agent_name=agent_name,
                user_message=body.message,
                channel="app",
            )
        except ActiveAgentSessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AgentSessionQuotaExceededError as exc:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": str(exc),
                    "count": exc.count,
                    "limit": exc.limit,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session = await get_session_by_id(result.session_id)
        metadata = (
            session.get("metadata") if session and isinstance(session.get("metadata"), dict) else {}
        )
        response = _session_response(result, status="active", metadata=metadata)
        return AgentSessionResponse(**response.model_dump(exclude={"messages"}))

    @api_router.post("/agents/sessions/{session_id}/turns", response_model=AgentSessionResponse)
    async def continue_agent_session_http(
        session_id: str,
        body: AgentSessionTurnRequest,
        auth_user_id: str = Depends(get_authenticated_user_id),
    ) -> AgentSessionResponse:
        _require_agents_enabled()
        discord_user_id = await require_discord_link(auth_user_id)

        session = await _load_owned_active_session(session_id, auth_user_id)
        agent_name = str(session.get("agent_name", "reflection"))
        guild_id = _parse_guild_id(session)

        try:
            result = await continue_agent_session(
                innersync_user_id=auth_user_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                user_message=body.message.strip(),
                channel="app",
            )
        except NoActiveAgentSessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result.session_id != session_id:
            raise HTTPException(status_code=409, detail="Session mismatch.")

        updated = await get_session_by_id(session_id)
        metadata = (
            updated.get("metadata") if updated and isinstance(updated.get("metadata"), dict) else {}
        )
        response = _session_response(result, status="active", metadata=metadata)
        return AgentSessionResponse(**response.model_dump(exclude={"messages"}))

    @api_router.post("/agents/sessions/{session_id}/complete", response_model=AgentSessionResponse)
    async def complete_agent_session_http(
        session_id: str,
        auth_user_id: str = Depends(get_authenticated_user_id),
    ) -> AgentSessionResponse:
        _require_agents_enabled()
        discord_user_id = await require_discord_link(auth_user_id)

        session = await _load_owned_active_session(session_id, auth_user_id)
        agent_name = str(session.get("agent_name", "reflection"))
        guild_id = _parse_guild_id(session)

        try:
            result = await end_agent_session(
                innersync_user_id=auth_user_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                agent_name=agent_name,
                channel="app",
            )
        except NoActiveAgentSessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result.session_id != session_id:
            raise HTTPException(status_code=409, detail="Session mismatch.")

        await emit_hermit_event(
            event_type="gpt_command",
            user_id=discord_user_id,
            guild_id=guild_id,
            payload={"agent": agent_name, "session_id": result.session_id},
        )

        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        metadata = {**metadata, "last_channel": "app"}
        response = _session_response(result, status="completed", metadata=metadata)
        return AgentSessionResponse(**response.model_dump(exclude={"messages"}))


def public_agent_names() -> list[str]:
    """Expose registered agent names for OpenAPI."""
    return list_agents()
