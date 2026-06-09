"""Exercise cogs/innersync_identity slash handlers (diff coverage)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import cogs.innersync_identity as cog


def test_link_rate_limit_blocks_after_three_calls() -> None:
    uid = 910_001
    cog._LINK_RATELIMIT.clear()
    assert cog._link_rate_ok(uid) is True
    assert cog._link_rate_ok(uid) is True
    assert cog._link_rate_ok(uid) is True
    assert cog._link_rate_ok(uid) is False


@pytest.mark.asyncio
async def test_link_slash_no_database_pool() -> None:
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.client = MagicMock()
    interaction.response.send_message = AsyncMock()
    with patch.object(cog, "get_bot_db_pool", return_value=None), patch.object(
        cog, "_link_rate_ok", return_value=True
    ):
        await cog.link_slash.callback(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_slash_already_linked() -> None:
    interaction = MagicMock()
    interaction.user.id = 43
    interaction.client = MagicMock()
    interaction.response.send_message = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "_link_rate_ok", return_value=True),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440000")),
    ):
        await cog.link_slash.callback(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_slash_sends_session_url() -> None:
    interaction = MagicMock()
    interaction.user.id = 44
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "_link_rate_ok", return_value=True),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
        patch.object(
            cog,
            "request_discord_link_session",
            new=AsyncMock(return_value={"link_url": "https://app.example/l"}),
        ),
    ):
        await cog.link_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()
    view = interaction.followup.send.await_args.kwargs.get("view")
    assert view is not None
    link_buttons = [c for c in view.children if getattr(c, "url", None)]
    assert len(link_buttons) == 1
    assert link_buttons[0].url == "https://app.example/l"
    assert link_buttons[0].label == "Open link session"


@pytest.mark.asyncio
async def test_link_slash_no_url_from_core() -> None:
    interaction = MagicMock()
    interaction.user.id = 45
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "_link_rate_ok", return_value=True),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
        patch.object(cog, "request_discord_link_session", new=AsyncMock(return_value={})),
    ):
        await cog.link_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlink_slash_deleted_and_not_deleted() -> None:
    for deleted in (True, False):
        interaction = MagicMock()
        interaction.user.id = 46
        interaction.client = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        pool = MagicMock()
        with (
            patch.object(cog, "get_bot_db_pool", return_value=pool),
            patch.object(cog, "request_discord_unlink", new=AsyncMock(return_value=deleted)),
            patch.object(
                cog, "delete_discord_link_for_discord_user", new=AsyncMock(return_value=False)
            ),
            patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
        ):
            await cog.unlink_slash.callback(interaction)
        interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlink_slash_not_deleted_legacy_profile_hint() -> None:
    interaction = MagicMock()
    interaction.user.id = 46
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "request_discord_unlink", new=AsyncMock(return_value=False)),
        patch.object(cog, "delete_discord_link_for_discord_user", new=AsyncMock(return_value=False)),
        patch.object(
            cog,
            "get_innersync_id_for_discord",
            new=AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440000"),
        ),
    ):
        await cog.unlink_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs.get("embed")
    assert embed is not None
    assert "No active link was found" in embed.description


@pytest.mark.asyncio
async def test_unlink_slash_no_pool() -> None:
    interaction = MagicMock()
    interaction.user.id = 47
    interaction.client = MagicMock()
    interaction.response.send_message = AsyncMock()
    with patch.object(cog, "get_bot_db_pool", return_value=None):
        await cog.unlink_slash.callback(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlink_slash_delete_raises() -> None:
    interaction = MagicMock()
    interaction.user.id = 48
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "request_discord_unlink", new=AsyncMock(return_value=False)),
        patch.object(
            cog,
            "delete_discord_link_for_discord_user",
            new=AsyncMock(side_effect=RuntimeError("db")),
        ),
    ):
        await cog.unlink_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_slash_with_core_profile() -> None:
    interaction = MagicMock()
    interaction.user.id = 49
    interaction.user.display_name = "Tester"
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    with patch.object(
        cog,
        "fetch_innersync_profile_for_discord",
        new=AsyncMock(
            return_value={
                "display_name": "Core Name",
                "avatar_url": "https://cdn.example/a.png",
                "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        ),
    ):
        await cog.profile_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_slash_fallback_linked() -> None:
    interaction = MagicMock()
    interaction.user.id = 50
    interaction.user.display_name = "Tester"
    interaction.user.display_avatar = None
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "fetch_innersync_profile_for_discord", new=AsyncMock(return_value=None)),
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440000")),
    ):
        await cog.profile_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_profile_slash_fallback_not_linked_with_avatar() -> None:
    interaction = MagicMock()
    interaction.user.id = 51
    interaction.user.display_name = "Tester"
    av = MagicMock()
    av.url = "https://cdn.discord.example/avatar.png"
    interaction.user.display_avatar = av
    interaction.client = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    pool = MagicMock()
    with (
        patch.object(cog, "fetch_innersync_profile_for_discord", new=AsyncMock(return_value=None)),
        patch.object(cog, "get_bot_db_pool", return_value=pool),
        patch.object(cog, "get_innersync_id_for_discord", new=AsyncMock(return_value=None)),
    ):
        await cog.profile_slash.callback(interaction)
    interaction.followup.send.assert_awaited_once()
