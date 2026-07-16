"""Tests for Hermit growth-checkins broker (Railway source of truth)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import config


def test_hermit_growth_checkins_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "svc-secret", raising=False)
    # Import app after env — use existing test pattern
    from api import app

    client = TestClient(app)
    response = client.get(
        "/api/hermit/growth-checkins",
        params={"user_id": "123456789012345678"},
    )
    assert response.status_code == 401


def test_hermit_growth_checkins_returns_railway_rows(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "svc-secret", raising=False)
    from api import app

    created = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": 42,
        "created_at": created,
        "goal": "sleep better",
        "obstacle": "late Discord",
        "feeling": "tired",
        "grok_response": "Rest is momentum.",
    }

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[row])
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)

    client = TestClient(app)
    with patch("api.db_pool", pool):
        response = client.get(
            "/api/hermit/growth-checkins",
            params={"user_id": "123456789012345678", "lookback_days": 30, "limit": 10},
            headers={"X-API-Key": "svc-secret"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "railway"
    assert len(body["items"]) == 1
    assert "Goal: sleep better" in body["items"][0]["content"]
    assert body["items"][0]["future_message"] == "Rest is momentum."
