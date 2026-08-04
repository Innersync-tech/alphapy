"""Tests for Innersync→Discord resolution helpers (403 / success paths)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import api as api_module

AUTH_SUB = "550e8400-e29b-41d4-a716-446655440000"
DISCORD_SNOWFLAKE = "999999999999999999"


@pytest.mark.asyncio
async def test_require_discord_raises_403_when_unlinked() -> None:
    pool = MagicMock()
    with (
        patch.object(api_module, "db_pool", pool),
        patch(
            "utils.innersync_identity.resolve_innersync_jwt_sub_to_discord_int",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as ei:
            await api_module._require_discord_id_for_linked_innersync(AUTH_SUB)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_require_discord_returns_int_when_linked() -> None:
    pool = MagicMock()
    with (
        patch.object(api_module, "db_pool", pool),
        patch(
            "utils.innersync_identity.resolve_innersync_jwt_sub_to_discord_int",
            new=AsyncMock(return_value=777888),
        ),
    ):
        out = await api_module._require_discord_id_for_linked_innersync(AUTH_SUB)
    assert out == 777888


@pytest.mark.asyncio
async def test_resolve_dashboard_actor_uses_snowflake_directly() -> None:
    """Control-panel Discord-header actors must not go through JWT link lookup."""
    with patch.object(
        api_module,
        "_require_discord_id_for_linked_innersync",
        new=AsyncMock(side_effect=AssertionError("must not resolve snowflake via links")),
    ):
        out = await api_module._resolve_dashboard_actor_discord_id(DISCORD_SNOWFLAKE)
    assert out == int(DISCORD_SNOWFLAKE)


@pytest.mark.asyncio
async def test_resolve_dashboard_actor_resolves_jwt_sub() -> None:
    with patch.object(
        api_module,
        "_require_discord_id_for_linked_innersync",
        new=AsyncMock(return_value=777888),
    ) as require_link:
        out = await api_module._resolve_dashboard_actor_discord_id(AUTH_SUB)
    assert out == 777888
    require_link.assert_awaited_once_with(AUTH_SUB)
