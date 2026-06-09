"""
HTTP calls to Core API for Discord ↔ Innersync linking and profile reads.

Endpoints are provisional; Core must implement the same paths or Alphapy env
can override path suffixes via CORE_DISCORD_LINK_SESSION_PATH and
CORE_DISCORD_BOT_PROFILE_PATH.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.logger import logger

_TIMEOUT = 12.0


def _base_url() -> str:
    return (getattr(config, "CORE_API_URL", None) or "").rstrip("/")


def _service_key() -> str | None:
    key = getattr(config, "ALPHAPY_SERVICE_KEY", None)
    return key if key else None


def _link_session_path() -> str:
    p = getattr(config, "CORE_DISCORD_LINK_SESSION_PATH", None) or "/integrations/discord/link-session"
    return p if p.startswith("/") else f"/{p}"


def _bot_profile_path() -> str:
    p = getattr(config, "CORE_DISCORD_BOT_PROFILE_PATH", None) or "/integrations/discord/bot-profile"
    return p if p.startswith("/") else f"/{p}"


def _unlink_path() -> str:
    p = getattr(config, "CORE_DISCORD_UNLINK_PATH", None) or "/integrations/discord/unlink"
    return p if p.startswith("/") else f"/{p}"


async def request_discord_link_session(discord_user_id: int) -> dict[str, Any] | None:
    """
    Ask Core to start a link session for this Discord user.

    Expected successful JSON examples:
        {"link_url": "https://app.innersync.tech/..."}
        {"url": "https://..."}  # alternate key
    """
    base = _base_url()
    key = _service_key()
    if not base or not key:
        logger.debug("Core link session: CORE_API_URL or ALPHAPY_SERVICE_KEY not set")
        return None
    url = f"{base}{_link_session_path()}"
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    payload = {"discord_user_id": discord_user_id}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
        if not response.is_success:
            logger.warning(
                "Core link session non-2xx: status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("Core link session request failed: %s", e)
        return None


def normalize_http_url(url: str) -> str | None:
    """
    Return a Discord-safe http(s) URL, or None if still invalid after fixes.

    Repairs https:/host → https://host (common INNERSYNC_APP_URL typo on Core).
    """
    if not url or not isinstance(url, str):
        return None
    fixed = url.strip()
    fixed = re.sub(r"^(https?):/([^/])", r"\1://\2", fixed)
    parsed = urlparse(fixed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return fixed


def extract_link_url(session: dict[str, Any] | None) -> str | None:
    """Pick a browser URL from a link-session response."""
    if not session:
        return None
    for key in ("link_url", "url", "magic_link_url", "authorize_url"):
        v = session.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return normalize_http_url(v)
    return None


async def fetch_innersync_profile_for_discord(discord_user_id: int) -> dict[str, Any] | None:
    """
    Fetch central profile for a Discord user (GET with query param).

    Expected JSON keys (all optional): display_name, avatar_url, email,
    innersync_user_id.
    """
    base = _base_url()
    key = _service_key()
    if not base or not key:
        return None
    path = _bot_profile_path()
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                url,
                params={"discord_user_id": str(discord_user_id)},
                headers={"X-API-Key": key},
            )
        if response.status_code == 404:
            return None
        if not response.is_success:
            logger.debug(
                "Core bot-profile non-2xx: status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.debug("Core bot-profile request failed: %s", e)
        return None


async def request_discord_unlink(discord_user_id: int) -> bool:
    """
    Ask Core to unlink this Discord user (Supabase + webhook back to Alphapy).

    Returns True when Core reports unlinked, False on error or noop.
    """
    base = _base_url()
    key = _service_key()
    if not base or not key:
        logger.debug("Core unlink: CORE_API_URL or ALPHAPY_SERVICE_KEY not set")
        return False
    url = f"{base}{_unlink_path()}"
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    payload = {"discord_user_id": discord_user_id}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code == 404:
            return False
        if not response.is_success:
            logger.warning(
                "Core unlink non-2xx: status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            return False
        data = response.json()
        if isinstance(data, dict) and data.get("status") == "unlinked":
            return True
        return False
    except Exception as e:
        logger.warning("Core unlink request failed: %s", e)
        return False
