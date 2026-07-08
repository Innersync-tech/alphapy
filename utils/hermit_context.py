"""
Hermit (Hermes) strategic context fetcher for Alphapy.

All calls are mediated through Core API. This module never talks directly to Hermes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

try:
    import config_local as config  # type: ignore
except ImportError:
    import config  # type: ignore

from utils.logger import logger

DEFAULT_TTL_SECONDS = 1800
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_CONTEXT_PATH = "/integrations/hermit/strategic-context"
MAX_INJECT_CHARS = 1500
MAX_STALE_SECONDS = 6 * 60 * 60

_cache_lock = threading.Lock()
_cache: dict[int, dict[str, Any]] = {}

_stats_attempts = 0
_stats_success = 0
_stats_failure = 0
_stats_cache_hits = 0
_stats_cache_misses = 0
_stats_stale_hits = 0
_stats_prompt_applied = 0
_stats_prompt_omitted = 0


def _feature_enabled() -> bool:
    return bool(getattr(config, "HERMIT_CONTEXT_ENABLED", False))


def _core_url() -> str:
    return (getattr(config, "CORE_API_URL", "") or "").rstrip("/")


def _service_key() -> str:
    return (getattr(config, "ALPHAPY_SERVICE_KEY", "") or "").strip()


def _context_path() -> str:
    path = (getattr(config, "CORE_HERMIT_CONTEXT_PATH", DEFAULT_CONTEXT_PATH) or "").strip()
    if not path:
        return DEFAULT_CONTEXT_PATH
    return path if path.startswith("/") else f"/{path}"


def _ttl_seconds() -> int:
    raw = int(getattr(config, "HERMIT_CONTEXT_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    return max(raw, 0)


def _timeout_seconds() -> float:
    raw = float(getattr(config, "HERMIT_CONTEXT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    return max(raw, 0.1)


def _normalize_context_text(text: str) -> str:
    compact = " ".join(text.replace("\x00", "").split())
    return compact[:MAX_INJECT_CHARS].strip()


def _get_cache(user_id: int) -> dict[str, Any] | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(user_id)
        if not entry:
            return None
        if now > float(entry["expires_at"]):
            return None
        return dict(entry)


def _set_cache(
    user_id: int,
    context_text: str,
    updated_at: str,
    staleness_minutes: int,
    strategy_packet: dict[str, Any] | None = None,
) -> None:
    expires_at = time.monotonic() + _ttl_seconds()
    with _cache_lock:
        _cache[user_id] = {
            "context_text": context_text,
            "strategy_packet": strategy_packet,
            "updated_at": updated_at,
            "staleness_minutes": staleness_minutes,
            "cached_at": time.time(),
            "expires_at": expires_at,
        }


def _strategy_packet_enabled() -> bool:
    return bool(getattr(config, "HERMIT_STRATEGY_PACKET_ENABLED", False))


def get_cached_strategy_packet(user_id: int) -> dict[str, Any] | None:
    entry = _get_cache(user_id)
    if not entry:
        return None
    packet = entry.get("strategy_packet")
    return packet if isinstance(packet, dict) else None


def record_prompt_usage(applied: bool) -> None:
    global _stats_prompt_applied, _stats_prompt_omitted
    with _cache_lock:
        if applied:
            _stats_prompt_applied += 1
        else:
            _stats_prompt_omitted += 1


def get_hermit_context_stats() -> dict[str, int]:
    with _cache_lock:
        return {
            "hermit_context_attempts": _stats_attempts,
            "hermit_context_success": _stats_success,
            "hermit_context_failure": _stats_failure,
            "hermit_context_cache_hits": _stats_cache_hits,
            "hermit_context_cache_misses": _stats_cache_misses,
            "hermit_context_stale_hits": _stats_stale_hits,
            "hermit_prompt_applied": _stats_prompt_applied,
            "hermit_prompt_omitted": _stats_prompt_omitted,
        }


def _fresh_stale_candidate(user_id: int) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _cache.get(user_id)
        if not entry:
            return None
        age_seconds = time.time() - float(entry.get("cached_at", 0))
        if age_seconds > MAX_STALE_SECONDS:
            return None
        return dict(entry)


async def fetch_hermit_context(user_id: int | None) -> str | None:
    global _stats_attempts, _stats_success, _stats_failure, _stats_cache_hits, _stats_cache_misses, _stats_stale_hits
    if not _feature_enabled() or user_id is None:
        return None

    cached = _get_cache(user_id)
    if cached:
        with _cache_lock:
            _stats_cache_hits += 1
        return str(cached["context_text"])

    with _cache_lock:
        _stats_cache_misses += 1
        _stats_attempts += 1

    base_url = _core_url()
    key = _service_key()
    if not base_url or not key:
        logger.debug("Hermit context skipped: CORE_API_URL or ALPHAPY_SERVICE_KEY missing")
        with _cache_lock:
            _stats_failure += 1
        return None

    url = f"{base_url}{_context_path()}"
    headers = {"X-API-Key": key}
    params = {"user_id": str(user_id)}

    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as client:
            response = await client.get(url, headers=headers, params=params)

        if response.status_code == 204:
            with _cache_lock:
                _stats_success += 1
            return None

        if not response.is_success:
            logger.warning(
                "Hermit context fetch failed: status=%s user_id=%s",
                response.status_code,
                user_id,
            )
            with _cache_lock:
                _stats_failure += 1
            stale = _fresh_stale_candidate(user_id)
            if stale:
                with _cache_lock:
                    _stats_stale_hits += 1
                return str(stale["context_text"])
            return None

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")

        raw_context = payload.get("context_text")
        if not isinstance(raw_context, str):
            raise ValueError("context_text missing")

        normalized_context = _normalize_context_text(raw_context)
        if not normalized_context:
            with _cache_lock:
                _stats_success += 1
            return None

        updated_at = str(payload.get("updated_at") or "")
        staleness_minutes = int(payload.get("staleness_minutes") or 0)
        strategy_packet = payload.get("strategy_packet")
        if not isinstance(strategy_packet, dict):
            strategy_packet = None

        _set_cache(
            user_id=user_id,
            context_text=normalized_context,
            updated_at=updated_at,
            staleness_minutes=staleness_minutes,
            strategy_packet=strategy_packet,
        )

        with _cache_lock:
            _stats_success += 1
        return normalized_context
    except Exception as error:
        logger.warning("Hermit context fetch exception for user %s: %s", user_id, error)
        with _cache_lock:
            _stats_failure += 1
        stale = _fresh_stale_candidate(user_id)
        if stale:
            with _cache_lock:
                _stats_stale_hits += 1
            return str(stale["context_text"])
        return None

