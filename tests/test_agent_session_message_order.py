from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_session_messages_order_user_before_assistant(monkeypatch) -> None:
    import config
    from agents.memory import (
        append_session_messages,
        clear_local_store,
        create_session,
        get_session_messages,
    )

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    session_id = await create_session(
        innersync_user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        discord_user_id=42,
        guild_id=1,
        agent_name="reflection",
    )
    await append_session_messages(
        session_id,
        turn_index=0,
        user_content="hey",
        assistant_content="Hello there.",
    )
    await append_session_messages(
        session_id,
        turn_index=1,
        user_content="again",
        assistant_content="Sure.",
    )

    rows = await get_session_messages(session_id)
    assert [row["role"] for row in rows] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
