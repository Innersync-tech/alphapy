"""Internal Hermit/Core broker routes (service API key)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import config

router = APIRouter(prefix="/api/hermit", tags=["hermit-broker"])


async def require_hermit_service_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Require Alphapy ``API_KEY`` — same gate as observability (no anonymous)."""
    configured_key = getattr(config, "API_KEY", None)
    if not configured_key:
        raise HTTPException(
            status_code=503,
            detail="Hermit broker unavailable: API key is not configured",
        )
    if x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


class GrowthCheckinItem(BaseModel):
    id: str
    created_at: datetime
    content: str
    type: Literal["growthcheckin"] = "growthcheckin"
    future_message: Optional[str] = None


class GrowthCheckinsResponse(BaseModel):
    items: list[GrowthCheckinItem] = Field(default_factory=list)
    source: Literal["railway"] = "railway"


@router.get(
    "/growth-checkins",
    response_model=GrowthCheckinsResponse,
    dependencies=[Depends(require_hermit_service_key)],
)
async def get_growth_checkins_for_hermit(
    user_id: str = Query(..., pattern=r"^\d+$", description="Discord snowflake"),
    lookback_days: int = Query(30, ge=1, le=180),
    limit: int = Query(20, ge=1, le=100),
) -> GrowthCheckinsResponse:
    """Return plaintext Discord /growthcheckin rows from Railway ``growth_checkins``.

    Canonical store for Hermit progress patterns (Core brokers here). Does not
    read Supabase vault ``reflections``.
    """
    # Late import — pool is owned by api.py lifespan
    import api as alphapy_api

    pool = getattr(alphapy_api, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    discord_id = int(user_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, created_at, goal, obstacle, feeling, grok_response
            FROM growth_checkins
            WHERE user_id = $1
              AND created_at >= $2
              AND (
                    COALESCE(goal, '') <> ''
                 OR COALESCE(obstacle, '') <> ''
                 OR COALESCE(feeling, '') <> ''
              )
            ORDER BY created_at DESC
            LIMIT $3
            """,
            discord_id,
            since,
            limit,
        )

    items: list[GrowthCheckinItem] = []
    for row in rows:
        goal = (row["goal"] or "").strip()
        obstacle = (row["obstacle"] or "").strip()
        feeling = (row["feeling"] or "").strip()
        parts = [
            f"Goal: {goal}" if goal else None,
            f"Obstacle: {obstacle}" if obstacle else None,
            f"Feeling: {feeling}" if feeling else None,
        ]
        content = "\n".join(p for p in parts if p)
        if not content:
            continue
        created = row["created_at"]
        if not isinstance(created, datetime):
            created = datetime.now(timezone.utc)
        elif created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        grok = row["grok_response"]
        items.append(
            GrowthCheckinItem(
                id=str(row["id"]),
                created_at=created.astimezone(timezone.utc),
                content=content[:4000],
                future_message=(str(grok)[:2000] if grok else None),
            )
        )
    return GrowthCheckinsResponse(items=items, source="railway")
