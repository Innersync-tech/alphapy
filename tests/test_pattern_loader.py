"""Tests for pattern_loader."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.pattern_loader import _fetch_tier2_insights, load_pattern_context


@pytest.mark.asyncio
async def test_load_pattern_context_disabled() -> None:
    result = await load_pattern_context("user-1", {"learn_from_patterns": False})
    assert result is None


@pytest.mark.asyncio
async def test_load_pattern_context_with_tier2_insights() -> None:
    memory = {
        "derived_profile": {
            "version": 1,
            "insights": [
                {
                    "id": "1",
                    "type": "theme",
                    "label": "Avoidance when energy drops",
                    "confidence": 0.85,
                    "source_reflection_ids": [],
                },
                {
                    "id": "2",
                    "type": "habit",
                    "label": "Gentle evening wind-down",
                    "confidence": 0.75,
                    "source_reflection_ids": [],
                },
            ],
            "active_themes": [],
            "open_loops": [],
        }
    }
    with patch(
        "agents.pattern_loader.get_user_memory",
        new_callable=AsyncMock,
        return_value=memory,
    ):
        result = await load_pattern_context("user-1", {"learn_from_patterns": True})
    assert result is not None
    assert "[learned_patterns]" in result
    assert "Avoidance when energy drops (theme)" in result
    assert "Gentle evening wind-down (habit)" in result


@pytest.mark.asyncio
async def test_load_pattern_context_falls_back_to_learn_from_shared() -> None:
    memory = {
        "derived_profile": {
            "version": 1,
            "insights": [
                {
                    "id": "1",
                    "type": "goal",
                    "label": "Recovery pacing after surgery",
                    "confidence": 0.9,
                    "source_reflection_ids": [],
                }
            ],
            "active_themes": [],
            "open_loops": [],
        }
    }
    with patch(
        "agents.pattern_loader.get_user_memory",
        new_callable=AsyncMock,
        return_value=memory,
    ):
        result = await load_pattern_context("user-1", {"learn_from_shared": True})
    assert result is not None
    assert "Recovery pacing after surgery (goal)" in result


@pytest.mark.asyncio
async def test_load_pattern_context_empty_memory() -> None:
    with patch(
        "agents.pattern_loader.get_user_memory",
        new_callable=AsyncMock,
        return_value={},
    ):
        result = await load_pattern_context("user-1", {"learn_from_patterns": True})
    assert result is None


@pytest.mark.asyncio
async def test_fetch_tier2_insights_skips_invalid_labels() -> None:
    memory = {
        "derived_profile": {
            "version": 1,
            "insights": [
                {
                    "id": "1",
                    "type": "theme",
                    "label": "short",
                    "confidence": 0.9,
                    "source_reflection_ids": [],
                },
                {
                    "id": "2",
                    "type": "theme",
                    "label": "Valid insight label here",
                    "confidence": 0.9,
                    "source_reflection_ids": [],
                },
            ],
            "active_themes": [],
            "open_loops": [],
        }
    }
    with patch(
        "agents.pattern_loader.get_user_memory",
        new_callable=AsyncMock,
        return_value=memory,
    ):
        out = await _fetch_tier2_insights("user-1")
    assert len(out) == 1
    assert out[0]["label"] == "Valid insight label here"
