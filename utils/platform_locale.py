"""Innersync ID platform locale for Alphapy LLM output.

SoT: Core bot-profile `locale` from innersync_users.preferences.locale.
Allowlist: nl-BE | en. Default / unlinked / errors: en.
agent_prefs.language_pref is a secondary mirror only.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_LOCALES = frozenset({"nl-BE", "en"})
DEFAULT_PLATFORM_LOCALE = "en"


def normalize_platform_locale(raw: Any) -> str:
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed in SUPPORTED_LOCALES:
            return trimmed
    return DEFAULT_PLATFORM_LOCALE


def locale_output_instruction(locale: str | None = None) -> str:
    loc = normalize_platform_locale(locale)
    if loc == "nl-BE":
        return (
            "Platform locale: nl-BE. Write user-facing replies and pattern labels "
            "in Belgian Dutch (nl-BE) unless the user clearly writes in another language."
        )
    return (
        "Platform locale: en. Write user-facing replies and pattern labels "
        "in English unless the user clearly writes in another language."
    )


def locale_from_prefs(prefs: dict[str, Any] | None) -> str | None:
    """Map agent_prefs.language_pref when it is an allowlisted locale."""
    if not prefs:
        return None
    raw = prefs.get("language_pref")
    if isinstance(raw, str) and raw.strip() in SUPPORTED_LOCALES:
        return raw.strip()
    return None


async def resolve_locale_for_discord(
    discord_user_id: int,
    prefs: dict[str, Any] | None = None,
) -> str:
    """
    Resolve locale for a Discord user.

    Order: Core bot-profile locale → allowlisted language_pref → en.
    Fail-open on network/Core errors.
    """
    try:
        from utils.core_discord_integration import fetch_innersync_profile_for_discord

        profile = await fetch_innersync_profile_for_discord(discord_user_id)
        if isinstance(profile, dict) and "locale" in profile:
            # Present (even if invalid) means linked; normalize invalid → en
            return normalize_platform_locale(profile.get("locale"))
    except Exception:
        pass

    from_prefs = locale_from_prefs(prefs)
    if from_prefs:
        return from_prefs
    return DEFAULT_PLATFORM_LOCALE
