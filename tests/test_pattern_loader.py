"""Tests for pattern_loader."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.pattern_loader import load_pattern_context


@pytest.mark.asyncio
async def test_load_pattern_context_disabled() -> None:
    result = await load_pattern_context("user-1", {"learn_from_patterns": False})
    assert result is None


@pytest.mark.asyncio
async def test_load_pattern_context_with_nodes() -> None:
    nodes = [
        {
            "label": "gpt_command dominance",
            "body_md": "Prioritize agent flows.",
            "usage_count": 3,
        }
    ]
    with patch(
        "agents.pattern_loader._fetch_pattern_nodes",
        new_callable=AsyncMock,
        return_value=nodes,
    ):
        result = await load_pattern_context("user-1", {"learn_from_patterns": True})
    assert result is not None
    assert "learned_patterns" in result
    assert "gpt_command dominance" in result
