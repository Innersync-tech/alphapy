"""Shared image-reminder rate limit for Discord cog + dashboard API.

Max 3 image reminder creates (or first-time image attaches) per user+guild
per IMAGE_REMINDER_RATE_LIMIT_WINDOW. State is process-global so dashboard and
bot paths share the same counter when they run in one process.
"""
from __future__ import annotations

import time as time_module

import config

# Hard cap matching Discord cog UX (config.IMAGE_REMINDER_RATE_LIMIT_COUNT is list retention).
IMAGE_REMINDER_RATE_LIMIT = 3

_timestamps: dict[tuple[int, int], list[float]] = {}


def recent_image_reminder_count(user_id: int, guild_id: int) -> int:
    key = (user_id, guild_id)
    now_ts = time_module.time()
    window = config.IMAGE_REMINDER_RATE_LIMIT_WINDOW
    return sum(1 for t in _timestamps.get(key, []) if t > now_ts - window)


def would_exceed_image_reminder_rate_limit(user_id: int, guild_id: int) -> bool:
    return recent_image_reminder_count(user_id, guild_id) >= IMAGE_REMINDER_RATE_LIMIT


def record_image_reminder(user_id: int, guild_id: int) -> None:
    key = (user_id, guild_id)
    now_ts = time_module.time()
    window = config.IMAGE_REMINDER_RATE_LIMIT_WINDOW
    ts_list = [t for t in _timestamps.get(key, []) if t > now_ts - window]
    ts_list.append(now_ts)
    _timestamps[key] = ts_list[-config.IMAGE_REMINDER_RATE_LIMIT_COUNT :]


def sweep_stale_image_reminder_timestamps() -> None:
    sweep_cutoff = time_module.time() - config.IMAGE_REMINDER_RATE_LIMIT_WINDOW
    stale_keys = [k for k, v in _timestamps.items() if not any(t > sweep_cutoff for t in v)]
    for k in stale_keys:
        del _timestamps[k]


def clear_image_reminder_timestamps_for_tests() -> None:
    """Test helper — empty shared state between cases."""
    _timestamps.clear()
