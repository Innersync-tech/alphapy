"""Tests for Innersync ID platform locale helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from utils.platform_locale import (
    DEFAULT_PLATFORM_LOCALE,
    locale_output_instruction,
    normalize_platform_locale,
    resolve_locale_for_discord,
)


def test_normalize_platform_locale() -> None:
    assert normalize_platform_locale("en") == "en"
    assert normalize_platform_locale("nl-BE") == "nl-BE"
    assert normalize_platform_locale("fr-FR") == DEFAULT_PLATFORM_LOCALE
    assert normalize_platform_locale(None) == DEFAULT_PLATFORM_LOCALE


def test_locale_output_instruction() -> None:
    assert "nl-BE" in locale_output_instruction("nl-BE")
    assert "English" in locale_output_instruction("en")


def test_resolve_locale_from_bot_profile() -> None:
    async def _run() -> None:
        with patch(
            "utils.core_discord_integration.fetch_innersync_profile_for_discord",
            new_callable=AsyncMock,
            return_value={"locale": "nl-BE", "innersync_user_id": "x"},
        ):
            assert await resolve_locale_for_discord(1) == "nl-BE"

    asyncio.run(_run())


def test_resolve_locale_unlinked_falls_back_to_prefs_then_en() -> None:
    async def _run() -> None:
        with patch(
            "utils.core_discord_integration.fetch_innersync_profile_for_discord",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert (
                await resolve_locale_for_discord(1, {"language_pref": "nl-BE"})
                == "nl-BE"
            )
            assert await resolve_locale_for_discord(1, {}) == "en"

    asyncio.run(_run())
