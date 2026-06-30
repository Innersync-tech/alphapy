"""Daily journal sync skill — reflections, streaks, shared app data."""
from __future__ import annotations

import logging

from agents.base import AgentContext, BaseAgentSkill
from gpt.context_loader import load_agent_reflection_context
from gpt.helpers import bot_instance
from utils.db_helpers import get_bot_db_pool
from utils.engagement_service import get_streak

logger = logging.getLogger("alphapy.agents.skills.journal_sync")


class JournalSyncSkill(BaseAgentSkill):
    name = "journal_sync"
    priority = 10

    async def gather(self, ctx: AgentContext) -> str:
        lines: list[str] = []

        reflection_context = await load_agent_reflection_context(ctx.discord_user_id, limit=5)
        if reflection_context.strip():
            lines.append(reflection_context.strip())
        else:
            lines.append(
                "No reflections explicitly shared with Alphapy "
                "(share per entry from the App dashboard)."
            )

        if ctx.guild_id is not None:
            pool = get_bot_db_pool(bot_instance) if bot_instance else None
            if pool is not None:
                streak = await get_streak(pool, ctx.guild_id, ctx.discord_user_id)
                if streak:
                    days = streak.get("current_days") or 0
                    last_day = streak.get("last_day") or "unknown"
                    lines.append(
                        f"Discord engagement streak: {days} day(s), last active {last_day}."
                    )

        memory_notes = ctx.memory.get("journal_notes")
        if memory_notes:
            lines.append(f"Agent memory notes: {memory_notes}")

        return "\n".join(lines)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["journal_sync_ran"] = True
        return None
