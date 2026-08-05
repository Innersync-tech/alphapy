"""theme_key must match Core agent_graph._theme_key fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from utils.core_agent_graph import _theme_key

FIXTURES = Path(__file__).parent / "fixtures" / "theme_key_vectors.json"


def test_theme_key_vectors_match_core() -> None:
    vectors = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for row in vectors:
        assert _theme_key(row["label"]) == row["theme_key"], row
