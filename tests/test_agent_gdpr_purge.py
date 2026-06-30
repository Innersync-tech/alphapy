from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_purge_agent_user_data_local_by_innersync_id(monkeypatch) -> None:
    import config
    from agents.memory import (
        clear_local_store,
        create_session,
        get_user_memory,
        patch_user_memory,
        purge_agent_user_data,
    )

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    await patch_user_memory(user_id, "reflection", {"session_count": 2})
    await create_session(
        innersync_user_id=user_id,
        discord_user_id=123456789,
        guild_id=1,
        agent_name="reflection",
    )

    await purge_agent_user_data(user_id)

    assert await get_user_memory(user_id, "reflection") == {}
    from agents.memory import get_active_session

    assert await get_active_session(user_id, "reflection") is None


@pytest.mark.asyncio
async def test_purge_agent_user_data_local_by_discord_id(monkeypatch) -> None:
    import config
    from agents.memory import (
        clear_local_store,
        create_session,
        get_user_memory,
        patch_user_memory,
        purge_agent_user_data,
    )

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee"
    await patch_user_memory(user_id, "reflection", {"session_count": 1})
    await create_session(
        innersync_user_id=user_id,
        discord_user_id=987654321,
        guild_id=1,
        agent_name="reflection",
    )

    await purge_agent_user_data(None, discord_user_id=987654321)

    assert await get_user_memory(user_id, "reflection") == {}


@pytest.mark.asyncio
async def test_purge_agent_user_data_requires_identifier() -> None:
    from agents.memory import purge_agent_user_data

    with pytest.raises(ValueError, match="requires"):
        await purge_agent_user_data()


@pytest.mark.asyncio
async def test_user_deleted_webhook_purges_agent_data_without_discord_id(monkeypatch) -> None:
    import json

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    purge_mock = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_agent_user_data", purge_mock)
    monkeypatch.setattr(
        "webhooks.supabase.validate_webhook_signature",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("webhooks.supabase.forward_supabase_auth", lambda payload: None)

    from webhooks.supabase import router

    app = FastAPI()
    app.include_router(router)

    user_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    payload = {
        "type": "USER_DELETED",
        "record": {"id": user_id, "raw_user_meta_data": {"provider": "email"}},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/supabase/auth",
            content=json.dumps(payload),
            headers={"x-supabase-signature": "test"},
        )

    assert response.status_code == 200
    purge_mock.assert_awaited_once_with(innersync_user_id=user_id)
