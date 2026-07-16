"""Tests for pattern_loader."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.pattern_loader import (
    _is_ops_telemetry_node,
    load_pattern_context,
)


@pytest.mark.asyncio
async def test_load_pattern_context_disabled() -> None:
    result = await load_pattern_context("user-1", {"learn_from_patterns": False})
    assert result is None


@pytest.mark.asyncio
async def test_load_pattern_context_with_progress_nodes() -> None:
    nodes = [
        {
            "label": "progress: last, mijn, knie",
            "body_md": "Goal themes from growth check-ins.",
            "usage_count": 1,
            "metadata": {"kind": "user_progress", "source": "growthcheckin"},
            "memory_tree_path": "patterns/2026-07-16-reflection.md",
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
    assert "progress: last, mijn, knie" in result


def test_ops_dominance_nodes_are_filtered() -> None:
    assert _is_ops_telemetry_node(
        {
            "label": "gpt_command dominance",
            "body_md": "Dominant event: gpt_command",
            "metadata": {"event_type": "gpt_command"},
        }
    )
    assert not _is_ops_telemetry_node(
        {
            "label": "progress: knie",
            "metadata": {"kind": "user_progress", "source": "growthcheckin"},
            "memory_tree_path": "patterns/x.md",
        }
    )


@pytest.mark.asyncio
async def test_fetch_pattern_nodes_skips_ops() -> None:
    rows = [
        {
            "label": "gpt_command dominance",
            "body_md": "Dominant event: gpt_command",
            "metadata": {"event_type": "gpt_command"},
            "usage_count": 33,
        },
        {
            "label": "progress: knie",
            "body_md": "Goal: last van mijn knie",
            "metadata": {"kind": "user_progress", "source": "growthcheckin"},
            "memory_tree_path": "patterns/2026-07-16-reflection.md",
            "usage_count": 1,
        },
    ]
    with patch("agents.pattern_loader._require_config"), patch(
        "agents.pattern_loader._supabase_get",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        from agents.pattern_loader import _fetch_pattern_nodes

        out = await _fetch_pattern_nodes("user-1", limit=5)
    assert len(out) == 1
    assert out[0]["label"] == "progress: knie"
