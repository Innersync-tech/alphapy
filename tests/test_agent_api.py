"""
Tests for cross-platform agent HTTP API (Phase 4.0).

Uses a minimal FastAPI app with the main router; auth and discord-link deps are overridden.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api as api_module
from api import get_authenticated_user_id, router, verify_api_key

AUTH_SUB = "550e8400-e29b-41d4-a716-446655440000"
DISCORD_USER_ID = 999999999999999999


def make_app(auth_user: str = AUTH_SUB) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_key] = lambda: None
    app.dependency_overrides[get_authenticated_user_id] = lambda: auth_user
    return app


def _mock_db_pool():
    pool = MagicMock()
    return pool


def _patch_discord_link():
    return (
        patch.object(api_module, "db_pool", _mock_db_pool()),
        patch(
            "utils.innersync_identity.resolve_innersync_jwt_sub_to_discord_int",
            new=AsyncMock(return_value=DISCORD_USER_ID),
        ),
    )


def _patch_agents_enabled(enabled: bool = True):
    import config

    return patch.object(config, "ALPHAPY_AGENTS_ENABLED", enabled)


def _patch_runtime_llm():
    async def _fake_ask_gpt(messages, user_id=None, **kwargs):
        return "Reflection assistant reply."

    async def _fake_load_reflections(discord_id, limit=5):
        return ""

    async def _fake_quota(*args, **kwargs):
        return True, 0, 10

    return (
        patch("agents.runtime.ask_gpt", new=_fake_ask_gpt),
        patch("agents.skills.journal_sync.load_agent_reflection_context", new=_fake_load_reflections),
        patch(
            "utils.premium_guard.check_and_increment_agent_session_quota",
            new=AsyncMock(side_effect=_fake_quota),
        ),
        patch("agents.http_routes.emit_hermit_event", new=AsyncMock()),
    )


@pytest.fixture(autouse=True)
def memory_backend(monkeypatch):
    import config
    from agents.memory import clear_local_store

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()
    yield
    clear_local_store()


class TestAgentSessionApi:
    def test_start_requires_agents_enabled(self):
        app = make_app()
        discord_patches = _patch_discord_link()
        with discord_patches[0], discord_patches[1], _patch_agents_enabled(False):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/agents/sessions", json={"agent": "reflection"})
        assert response.status_code == 503

    def test_start_continue_complete_flow(self):
        app = make_app()
        patches = _patch_runtime_llm()
        discord_patches = _patch_discord_link()
        with (
            discord_patches[0],
            discord_patches[1],
            _patch_agents_enabled(True),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            client = TestClient(app)

            start = client.post(
                "/api/agents/sessions",
                json={"agent": "reflection", "message": "How am I doing?"},
            )
            assert start.status_code == 201
            body = start.json()
            assert body["status"] == "active"
            assert body["origin_channel"] == "app"
            assert body["last_channel"] == "app"
            session_id = body["session_id"]

            active = client.get("/api/agents/sessions/active?agent=reflection")
            assert active.status_code == 200
            active_body = active.json()
            assert active_body["session_id"] == session_id
            assert len(active_body["messages"]) == 2

            cont = client.post(
                f"/api/agents/sessions/{session_id}/turns",
                json={"message": "Tell me more"},
            )
            assert cont.status_code == 200
            assert cont.json()["turn_count"] == 2

            done = client.post(f"/api/agents/sessions/{session_id}/complete")
            assert done.status_code == 200
            assert done.json()["status"] == "completed"

            missing = client.get("/api/agents/sessions/active?agent=reflection")
            assert missing.status_code == 404

    def test_start_conflict_when_session_active(self):
        app = make_app()
        patches = _patch_runtime_llm()
        discord_patches = _patch_discord_link()
        with (
            discord_patches[0],
            discord_patches[1],
            _patch_agents_enabled(True),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            client = TestClient(app)
            first = client.post("/api/agents/sessions", json={"agent": "reflection"})
            assert first.status_code == 201
            second = client.post("/api/agents/sessions", json={"agent": "reflection"})
            assert second.status_code == 409

    def test_quota_exceeded_returns_402(self):
        app = make_app()
        discord_patches = _patch_discord_link()

        async def _deny_quota(*args, **kwargs):
            return False, 10, 10

        with (
            discord_patches[0],
            discord_patches[1],
            _patch_agents_enabled(True),
            patch(
                "utils.premium_guard.check_and_increment_agent_session_quota",
                new=AsyncMock(side_effect=_deny_quota),
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/agents/sessions", json={"agent": "reflection"})
        assert response.status_code == 402

    def test_forbidden_session_owner(self):
        app = make_app()
        patches = _patch_runtime_llm()
        discord_patches = _patch_discord_link()
        with (
            discord_patches[0],
            discord_patches[1],
            _patch_agents_enabled(True),
            patches[0],
            patches[1],
            patches[2],
            patches[3],
        ):
            client = TestClient(app)
            start = client.post("/api/agents/sessions", json={"agent": "reflection"})
            session_id = start.json()["session_id"]

        other_app = make_app(auth_user="660e8400-e29b-41d4-a716-446655440001")
        other_discord = _patch_discord_link()
        with other_discord[0], other_discord[1], _patch_agents_enabled(True):
            other_client = TestClient(other_app, raise_server_exceptions=False)
            response = other_client.post(
                f"/api/agents/sessions/{session_id}/turns",
                json={"message": "nope"},
            )
        assert response.status_code == 403
