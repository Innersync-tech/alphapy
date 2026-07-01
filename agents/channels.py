"""Cross-platform channel metadata for agent sessions."""
from __future__ import annotations

from typing import Any, Literal

AgentChannel = Literal["discord", "app"]


def merge_channel_metadata(
    existing: dict[str, Any],
    *,
    channel: AgentChannel,
    is_start: bool = False,
    guild_id: int | None = None,
) -> dict[str, Any]:
    """Merge origin/last channel fields into session metadata."""
    merged = dict(existing)
    merged["last_channel"] = channel
    if is_start:
        merged["origin_channel"] = channel
        if guild_id is not None:
            merged["origin_guild_id"] = str(guild_id)
    return merged
