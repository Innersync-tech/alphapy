"""Active reminder quota helpers shared by Discord cog + dashboard API."""
from __future__ import annotations

from typing import Any

from utils.premium_guard import get_user_tier
from utils.premium_tiers import REMINDER_LIMIT


async def count_active_reminders(conn: Any, user_id: int, guild_id: int) -> int:
    """Count reminders that still count toward REMINDER_LIMIT (not mark-done)."""
    count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM reminders
        WHERE created_by = $1 AND guild_id = $2 AND completed IS NOT TRUE
        """,
        user_id,
        guild_id,
    )
    return int(count or 0)


async def get_reminder_quota_block_message(conn: Any, user_id: int, guild_id: int) -> str | None:
    """Return a user-facing block message when over free-tier limit, else None."""
    tier = await get_user_tier(user_id, guild_id)
    limit = REMINDER_LIMIT.get(tier)
    if limit is None:
        return None
    if await count_active_reminders(conn, user_id, guild_id) >= limit:
        return (
            f"You have reached the maximum of {limit} reminders for your tier. "
            "Upgrade via `/premium` to create unlimited reminders."
        )
    return None
