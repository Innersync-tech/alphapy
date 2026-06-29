"""Simple trade insight skill — reads recent demo trades from Supabase."""
from __future__ import annotations

import logging

import httpx

from agents.base import AgentContext, BaseAgentSkill
from utils.supabase_client import SupabaseConfigurationError, _supabase_get

logger = logging.getLogger("alphapy.agents.skills.trade_insight")


class TradeInsightSkill(BaseAgentSkill):
    name = "trade_insight"
    priority = 20

    async def gather(self, ctx: AgentContext) -> str:
        try:
            rows = await _supabase_get(
                "trades",
                {
                    "select": "symbol,side,pnl,notes,created_at",
                    "user_id": f"eq.{ctx.innersync_user_id}",
                    "order": "created_at.desc",
                    "limit": 5,
                },
            )
        except (SupabaseConfigurationError, httpx.HTTPError) as exc:
            logger.debug("Trade fetch unavailable: %s", exc)
            return "Trade data unavailable (Supabase not configured or no trades table access)."

        if not rows:
            return "No recent demo trades found for this user."

        lines = ["Recent demo trades:"]
        wins = 0
        losses = 0
        for row in rows:
            pnl = row.get("pnl")
            if isinstance(pnl, (int, float)):
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
            symbol = row.get("symbol") or "?"
            side = row.get("side") or "?"
            note = (row.get("notes") or "").strip()[:120]
            created = row.get("created_at") or "?"
            suffix = f" — {note}" if note else ""
            lines.append(f"- {created}: {symbol} {side} pnl={pnl}{suffix}")

        total = wins + losses
        if total:
            lines.append(f"Win/loss in sample: {wins}/{losses} ({total} trades).")
        return "\n".join(lines)

    async def execute(self, ctx: AgentContext) -> str | None:
        ctx.metadata["trade_insight_ran"] = True
        return None
