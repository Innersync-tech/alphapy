"""
Tests for utils.fyi_tips: contextual FYI tips (first-time events, cooldown, embed content).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.fyi_tips import (
    FYI_CONTENT,
    FYI_COOLDOWN_SECONDS,
    FYI_KEYS,
    _build_fyi_embed,
    _parse_last_sent,
    force_send_fyi,
    reset_fyi,
    send_fyi_if_first,
)

# --- _parse_last_sent (unit) ---


def test_parse_last_sent_none():
    assert _parse_last_sent(None) is None


def test_parse_last_sent_int_epoch():
    # 0 = 1970-01-01 UTC
    result = _parse_last_sent(0)
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 1970


def test_parse_last_sent_float():
    result = _parse_last_sent(1700000000.0)  # 2023-11-15 or so
    assert result is not None
    assert result.tzinfo is not None


def test_parse_last_sent_iso_string():
    s = "2025-02-23T12:00:00+00:00"
    result = _parse_last_sent(s)
    assert result is not None
    assert result.year == 2025
    assert result.month == 2
    assert result.day == 23


def test_parse_last_sent_iso_string_with_z():
    s = "2025-02-23T12:00:00Z"
    result = _parse_last_sent(s)
    assert result is not None
    assert result.year == 2025


def test_parse_last_sent_naive_iso_gets_utc():
    s = "2025-02-23T12:00:00"
    result = _parse_last_sent(s)
    assert result is not None
    assert result.tzinfo is not None


def test_parse_last_sent_invalid_string_returns_none():
    assert _parse_last_sent("not-a-date") is None


def test_parse_last_sent_other_type_returns_none():
    assert _parse_last_sent([]) is None
    assert _parse_last_sent({}) is None


# --- _build_fyi_embed (unit) ---


def test_build_fyi_embed_valid_key():
    embed = _build_fyi_embed("first_onboarding_done")
    assert embed is not None
    assert embed.title is not None
    assert "onboarding" in embed.title.lower() or "fyi" in embed.title.lower()
    assert embed.description is not None
    assert embed.color is not None


def test_build_fyi_embed_unknown_key_returns_none():
    assert _build_fyi_embed("unknown_key_xyz") is None


def test_build_fyi_embed_all_phase1_keys_have_content():
    phase1 = {"first_guild_join", "first_onboarding_done", "first_config_wizard_complete", "first_reminder", "first_ticket"}
    for key in phase1:
        assert key in FYI_KEYS
        assert key in FYI_CONTENT
        embed = _build_fyi_embed(key)
        assert embed is not None, f"Missing embed for {key}"


def test_build_fyi_embed_phase2_keys_have_content():
    phase2 = {"first_invite_leaderboard", "first_growthcheckin"}
    for key in phase2:
        assert key in FYI_KEYS
        assert key in FYI_CONTENT
        embed = _build_fyi_embed(key)
        assert embed is not None, f"Missing embed for {key}"


# --- send_fyi_if_first (async, mocked) ---


@pytest.fixture
def fyi_mock_bot():
    """Bot mock with async get_raw/set_raw/clear_raw and optional channel."""
    storage = {}

    async def get_raw(scope, key, guild_id, fallback=None):
        if scope != "fyi":
            return fallback
        return storage.get((guild_id, key), fallback)

    async def set_raw(scope, key, value, guild_id=0):
        if scope != "fyi":
            return
        storage[(guild_id, key)] = value

    async def clear_raw(scope, key, guild_id=0):
        if scope != "fyi":
            return
        storage.pop((guild_id, key), None)

    settings = MagicMock()
    settings.get_raw = AsyncMock(side_effect=get_raw)
    settings.set_raw = AsyncMock(side_effect=set_raw)
    settings.clear_raw = AsyncMock(side_effect=clear_raw)
    settings.get = MagicMock(return_value=0)  # default: no log channel

    bot = MagicMock()
    bot.settings = settings
    bot.get_channel = MagicMock(return_value=None)

    bot._storage = storage  # for test assertions
    return bot


@pytest.mark.asyncio
async def test_send_fyi_if_first_unknown_key_does_nothing(fyi_mock_bot):
    await send_fyi_if_first(fyi_mock_bot, 123, "unknown_key_xyz")
    fyi_mock_bot.settings.set_raw.assert_not_called()


@pytest.mark.asyncio
async def test_send_fyi_if_first_no_get_raw_does_nothing():
    bot = MagicMock()
    bot.settings = MagicMock(spec=[])  # no get_raw
    await send_fyi_if_first(bot, 123, "first_onboarding_done")
    assert not hasattr(bot.settings, "get_raw") or not callable(getattr(bot.settings, "get_raw", None))


@pytest.mark.asyncio
async def test_send_fyi_if_first_already_sent_does_not_send(fyi_mock_bot):
    # Pre-mark as sent
    await fyi_mock_bot.settings.set_raw("fyi", "first_onboarding_done", True, 456)
    channel = MagicMock()
    channel.send = AsyncMock()
    fyi_mock_bot.get_channel.return_value = channel
    fyi_mock_bot.settings.get.return_value = 999

    await send_fyi_if_first(fyi_mock_bot, 456, "first_onboarding_done", channel_id_override=999)

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_fyi_if_first_cooldown_active_does_not_send(fyi_mock_bot):
    from utils.fyi_tips import LAST_SENT_KEY
    # Set last_sent_at to "now" so cooldown is active
    now_iso = datetime.now(UTC).isoformat()
    await fyi_mock_bot.settings.set_raw("fyi", LAST_SENT_KEY, now_iso, 789)
    channel = MagicMock()
    channel.send = AsyncMock()
    fyi_mock_bot.get_channel.return_value = channel

    await send_fyi_if_first(fyi_mock_bot, 789, "first_ticket", channel_id_override=111)

    channel.send.assert_not_called()
    # Key should not be set because we skipped due to cooldown
    assert fyi_mock_bot._storage.get((789, "first_ticket")) is None


@pytest.mark.asyncio
async def test_send_fyi_if_first_sends_and_marks(fyi_mock_bot):
    from utils.fyi_tips import LAST_SENT_KEY
    channel = MagicMock()
    channel.send = AsyncMock()
    fyi_mock_bot.get_channel.return_value = channel
    guild_id = 1000
    await send_fyi_if_first(fyi_mock_bot, guild_id, "first_reminder", channel_id_override=2000)

    channel.send.assert_called_once()
    call_args = channel.send.call_args
    assert call_args[1]["embed"] is not None
    assert fyi_mock_bot._storage.get((guild_id, "first_reminder")) is True
    assert (guild_id, LAST_SENT_KEY) in fyi_mock_bot._storage


# --- force_send_fyi (async) ---


@pytest.mark.asyncio
async def test_force_send_fyi_unknown_key_returns_false(fyi_mock_bot):
    result = await force_send_fyi(fyi_mock_bot, 1, "unknown_key")
    assert result is False


@pytest.mark.asyncio
async def test_force_send_fyi_no_channel_returns_false(fyi_mock_bot):
    fyi_mock_bot.get_channel.return_value = None
    fyi_mock_bot.settings.get.return_value = 999
    result = await force_send_fyi(fyi_mock_bot, 1, "first_onboarding_done")
    assert result is False


@pytest.mark.asyncio
async def test_force_send_fyi_sends_and_returns_true(fyi_mock_bot):
    channel = MagicMock()
    channel.send = AsyncMock()
    fyi_mock_bot.get_channel.return_value = channel
    result = await force_send_fyi(fyi_mock_bot, 2, "first_guild_join", channel_id_override=3000, mark_as_sent=True)
    assert result is True
    channel.send.assert_called_once()
    assert fyi_mock_bot._storage.get((2, "first_guild_join")) is True


# --- reset_fyi (async) ---


@pytest.mark.asyncio
async def test_reset_fyi_unknown_key_returns_false(fyi_mock_bot):
    result = await reset_fyi(fyi_mock_bot, 1, "unknown_key")
    assert result is False
    fyi_mock_bot.settings.clear_raw.assert_not_called()


@pytest.mark.asyncio
async def test_reset_fyi_valid_key_calls_clear_raw(fyi_mock_bot):
    from unittest.mock import call
    result = await reset_fyi(fyi_mock_bot, 3, "first_ticket")
    assert result is True
    assert fyi_mock_bot.settings.clear_raw.call_count == 2
    fyi_mock_bot.settings.clear_raw.assert_has_calls([
        call("fyi", "first_ticket", 3),
        call("fyi", "_last_sent_at", 3),
    ])


# --- constants ---


def test_fyi_cooldown_is_24_hours():
    assert FYI_COOLDOWN_SECONDS == 24 * 3600


def test_fyi_keys_contains_phase1():
    phase1 = ["first_guild_join", "first_onboarding_done", "first_reminder", "first_ticket"]
    for k in phase1:
        assert k in FYI_KEYS


# --- Raw FYI in-memory persistence (no DB pool) ---


@pytest.mark.asyncio
async def test_raw_fyi_persists_in_memory_when_pool_unavailable():
    """When SettingsService has no DB pool, get_raw/set_raw/clear_raw use in-memory store so FYI state is durable within the process."""
    from utils.settings_service import SettingsService

    service = SettingsService(dsn=None)  # no pool
    guild_id = 999
    scope = "fyi"
    key = "first_guild_join"

    # Initially not set
    out = await service.get_raw(scope, key, guild_id, fallback=None)
    assert out is None

    # set_raw persists in memory
    await service.set_raw(scope, key, True, guild_id)
    out = await service.get_raw(scope, key, guild_id, fallback=None)
    assert out is True

    # clear_raw removes from memory
    await service.clear_raw(scope, key, guild_id)
    out = await service.get_raw(scope, key, guild_id, fallback=None)
    assert out is None

    # Non-fyi scope is no-op for set/clear and returns fallback for get
    await service.set_raw("other", "k", "v", guild_id)
    assert await service.get_raw("other", "k", guild_id, fallback="default") == "default"
