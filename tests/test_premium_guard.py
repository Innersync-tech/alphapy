"""
Tests for Premium guard: message helper and is_premium behaviour.
"""

from unittest.mock import AsyncMock, patch

import pytest

from utils.premium_guard import (
    _get_cached,
    _set_cache,
    _transfer_core_api,
    get_active_premium_guild,
    invalidate_premium_cache,
    is_premium,
    premium_required_message,
    transfer_premium_to_guild,
)


class TestPremiumRequiredMessage:
    """Tests for premium_required_message helper."""

    def test_returns_string_with_premium_command(self):
        msg = premium_required_message("Reminders with images")
        assert isinstance(msg, str)
        assert "/premium" in msg
        assert "premium" in msg.lower()
        assert "Reminders with images" in msg

    def test_contains_mockingbird_tone(self):
        msg = premium_required_message("Feature")
        assert "mature" in msg.lower() or "power" in msg.lower() or "premium" in msg.lower()


class TestIsPremium:
    """Tests for is_premium with mocked Core-API and DB."""

    @pytest.mark.asyncio
    async def test_returns_false_when_core_and_db_both_fail(self):
        with patch("utils.premium_guard._check_core_api", new_callable=AsyncMock, return_value=None), \
             patch("utils.premium_guard._check_local_db", new_callable=AsyncMock, return_value=False):
            result = await is_premium(999, 888)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_core_returns_true(self):
        with patch("utils.premium_guard._check_core_api", new_callable=AsyncMock, return_value=True), \
             patch("utils.premium_guard._check_local_db", new_callable=AsyncMock):
            result = await is_premium(111, 222)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_local_db_returns_true_and_core_unconfigured(self):
        with patch("utils.premium_guard._check_core_api", new_callable=AsyncMock, return_value=None), \
             patch("utils.premium_guard._check_local_db", new_callable=AsyncMock, return_value=True):
            result = await is_premium(333, 444)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_core_returns_false(self):
        with patch("utils.premium_guard._check_core_api", new_callable=AsyncMock, return_value=False), \
             patch("utils.premium_guard._check_local_db", new_callable=AsyncMock):
            result = await is_premium(555, 666)
        assert result is False


class TestPremiumCache:
    """Tests for cache get/set (no TTL expiry in test)."""

    def test_set_and_get_cached(self):
        _set_cache(1, 2, True)
        assert _get_cached(1, 2) is True
        _set_cache(1, 2, False)
        assert _get_cached(1, 2) is False

    def test_get_cached_miss_returns_none(self):
        assert _get_cached(99999, 88888) is None


class TestInvalidatePremiumCache:
    """Tests for invalidate_premium_cache (webhook-driven cache invalidation)."""

    def test_clears_single_user_guild_entry(self):
        _set_cache(100, 200, True)
        assert _get_cached(100, 200) is True
        invalidate_premium_cache(100, 200)
        assert _get_cached(100, 200) is None

    def test_clears_all_entries_for_user_when_guild_id_none(self):
        _set_cache(50, 1, True)
        _set_cache(50, 2, False)
        assert _get_cached(50, 1) is True
        assert _get_cached(50, 2) is False
        invalidate_premium_cache(50, None)
        assert _get_cached(50, 1) is None
        assert _get_cached(50, 2) is None

    def test_does_not_clear_other_users_when_clearing_one_guild(self):
        _set_cache(10, 20, True)
        _set_cache(11, 20, True)
        invalidate_premium_cache(10, 20)
        assert _get_cached(10, 20) is None
        assert _get_cached(11, 20) is True

    def test_idempotent_when_key_not_in_cache(self):
        invalidate_premium_cache(999, 888)
        invalidate_premium_cache(999, None)
        assert _get_cached(999, 888) is None


class TestGetActivePremiumGuild:
    """get_active_premium_guild returns the guild_id (int) or None, never 0."""

    @pytest.mark.asyncio
    async def test_returns_none_when_pool_unavailable(self):
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=None):
            result = await get_active_premium_guild(12345)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm):
            result = await get_active_premium_guild(111)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_guild_id_int_when_row_exists(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"guild_id": 98765})
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm):
            result = await get_active_premium_guild(111)
        assert result == 98765
        assert isinstance(result, int)


class TestTransferCoreApi:
    """_transfer_core_api: Core POST /api/premium/transfer helper."""

    @pytest.mark.asyncio
    async def test_returns_none_when_core_not_configured(self):
        with patch("utils.premium_guard.config") as mock_cfg:
            mock_cfg.CORE_API_URL = ""
            mock_cfg.ALPHAPY_SERVICE_KEY = None
            assert await _transfer_core_api(1, 2) is None

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        mock_response = AsyncMock()
        mock_response.is_success = True
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        with patch("utils.premium_guard.config") as mock_cfg, \
             patch("utils.premium_guard._get_http_client", return_value=mock_client):
            mock_cfg.CORE_API_URL = "https://api.example.com/"
            mock_cfg.ALPHAPY_SERVICE_KEY = "key"
            assert await _transfer_core_api(10, 20) is True
        mock_client.post.assert_awaited_once()
        args, kwargs = mock_client.post.await_args
        assert args[0] == "https://api.example.com/api/premium/transfer"
        assert kwargs["json"] == {"user_id": 10, "guild_id": 20}
        assert kwargs["headers"]["X-API-Key"] == "key"

    @pytest.mark.asyncio
    async def test_returns_false_on_non_2xx(self):
        mock_response = AsyncMock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_response.text = "not found"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        with patch("utils.premium_guard.config") as mock_cfg, \
             patch("utils.premium_guard._get_http_client", return_value=mock_client):
            mock_cfg.CORE_API_URL = "https://api.example.com"
            mock_cfg.ALPHAPY_SERVICE_KEY = "key"
            assert await _transfer_core_api(1, 2) is False

    @pytest.mark.asyncio
    async def test_returns_none_on_request_error(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("network"))
        with patch("utils.premium_guard.config") as mock_cfg, \
             patch("utils.premium_guard._get_http_client", return_value=mock_client):
            mock_cfg.CORE_API_URL = "https://api.example.com"
            mock_cfg.ALPHAPY_SERVICE_KEY = "key"
            assert await _transfer_core_api(1, 2) is None


class TestTransferPremiumToGuild:
    """transfer_premium_to_guild: local DB + Core sync."""

    @pytest.mark.asyncio
    async def test_success_local_and_core(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {"guild_id": 111},
                {"id": 1},
            ]
        )
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=True), \
             patch("utils.premium_guard._clear_cache_for_user") as clear_cache:
            ok, reason = await transfer_premium_to_guild(99, 222)
        assert ok is True
        assert reason == "transferred"
        clear_cache.assert_called_once_with(99)

    @pytest.mark.asyncio
    async def test_success_core_only_when_no_local_row(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[None, None])
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=True), \
             patch("utils.premium_guard._clear_cache_for_user"):
            ok, reason = await transfer_premium_to_guild(99, 222)
        assert ok is True
        assert reason == "transferred"

    @pytest.mark.asyncio
    async def test_fails_when_pool_none_and_core_unconfigured(self):
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=None), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=None):
            ok, reason = await transfer_premium_to_guild(1, 2)
        assert ok is False
        assert reason == "database unavailable"

    @pytest.mark.asyncio
    async def test_fails_when_core_explicit_false_and_no_local(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=False):
            ok, reason = await transfer_premium_to_guild(1, 2)
        assert ok is False
        assert reason == "no active subscription"

    @pytest.mark.asyncio
    async def test_fails_when_no_local_and_core_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=None):
            ok, reason = await transfer_premium_to_guild(1, 2)
        assert ok is False
        assert reason == "no active subscription"

    @pytest.mark.asyncio
    async def test_local_exception_still_succeeds_via_core(self):
        mock_cm = AsyncMock()
        mock_cm.__aenter__.side_effect = RuntimeError("db down")
        with patch("utils.premium_guard._ensure_pool", new_callable=AsyncMock, return_value=AsyncMock()), \
             patch("utils.premium_guard.acquire_safe", return_value=mock_cm), \
             patch("utils.premium_guard._transfer_core_api", new_callable=AsyncMock, return_value=True), \
             patch("utils.premium_guard._clear_cache_for_user"):
            ok, reason = await transfer_premium_to_guild(5, 6)
        assert ok is True
        assert reason == "transferred"
