from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_agent_session_quota_allows_under_limit() -> None:
    from utils.premium_guard import check_and_increment_agent_session_quota

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"session_count": 3})
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    with patch(
        "utils.premium_guard._tier_for_agent_quota",
        new_callable=AsyncMock,
        return_value="free",
    ), patch(
        "utils.premium_guard._ensure_pool",
        new_callable=AsyncMock,
        return_value=object(),
    ), patch("utils.premium_guard.acquire_safe", return_value=mock_cm):
        allowed, count, limit = await check_and_increment_agent_session_quota(42, 99)

    assert allowed is True
    assert count == 3
    assert limit == 10


@pytest.mark.asyncio
async def test_agent_session_quota_blocks_at_limit() -> None:
    from utils.premium_guard import check_and_increment_agent_session_quota

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"session_count": 11})
    mock_conn.execute = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    with patch(
        "utils.premium_guard._tier_for_agent_quota",
        new_callable=AsyncMock,
        return_value="free",
    ), patch(
        "utils.premium_guard._ensure_pool",
        new_callable=AsyncMock,
        return_value=object(),
    ), patch("utils.premium_guard.acquire_safe", return_value=mock_cm):
        allowed, count, limit = await check_and_increment_agent_session_quota(42, 99)

    assert allowed is False
    assert count == 10
    assert limit == 10
    mock_conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_session_quota_unlimited_tier_skips_db() -> None:
    from utils.premium_guard import check_and_increment_agent_session_quota

    with patch(
        "utils.premium_guard._tier_for_agent_quota",
        new_callable=AsyncMock,
        return_value="yearly",
    ), patch(
        "utils.premium_guard._ensure_pool",
        new_callable=AsyncMock,
    ) as pool_mock:
        allowed, count, limit = await check_and_increment_agent_session_quota(42, 99)

    assert allowed is True
    assert count == 0
    assert limit is None
    pool_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_agent_session_raises_when_quota_exceeded(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store
    from agents.runtime import AgentSessionQuotaExceededError, start_agent_session

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    async def _deny_quota(_user_id: int, _guild_id: int | None = None):
        return False, 10, 10

    monkeypatch.setattr(
        "utils.premium_guard.check_and_increment_agent_session_quota",
        _deny_quota,
    )

    with pytest.raises(AgentSessionQuotaExceededError) as exc_info:
        await start_agent_session(
            innersync_user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            discord_user_id=42,
            guild_id=99,
            agent_name="reflection",
        )

    assert exc_info.value.limit == 10
