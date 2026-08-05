"""Unit tests for Memory Vault agent_chat Core payload builder."""

from __future__ import annotations

from utils.core_agent_graph import build_agent_chat_progress_payload


def test_build_payload_from_insight_labels() -> None:
    payload = build_agent_chat_progress_payload(
        discord_user_id=123456789012345678,
        session_id="sess-abc",
        insight_snapshot=[
            {"id": "1", "type": "theme", "label": "Avoidance when energy drops"},
            {"id": "2", "type": "theme", "label": "Avoidance when energy drops"},
            {"id": "3", "type": "habit", "label": "Gentle evening wind-down"},
        ],
        active_themes=["Evening wind-down"],
        day="2026-07-27",
    )
    assert payload is not None
    assert payload["user_id"] == "123456789012345678"
    assert payload["writer"] == "alphapy"
    node = payload["nodes"][0]
    assert node["node_type"] == "pattern"
    meta = node["metadata"]
    assert meta["kind"] == "user_progress"
    assert meta["source"] == "agent_chat"
    assert meta["day"] == "2026-07-27"
    assert meta["theme_source"] == "tier2"
    assert meta["content_ref"] == "agent_session:sess-abc"
    assert "Avoidance when energy drops" in meta["themes"]
    assert len(meta["themes"]) <= 8
    # No transcript / message dump
    assert "user_transcript" not in meta
    assert "messages" not in meta
    blob = str(payload).lower()
    assert "journal" not in blob
    assert "transcript" not in blob


def test_build_payload_theme_cap_is_eight() -> None:
    snapshot = [
        {"id": str(i), "type": "theme", "label": f"Meaningful pattern phrase number {i}"}
        for i in range(12)
    ]
    payload = build_agent_chat_progress_payload(
        discord_user_id=1,
        session_id="cap-test",
        insight_snapshot=snapshot,
        day="2026-08-05",
    )
    assert payload is not None
    themes = payload["nodes"][0]["metadata"]["themes"]
    assert len(themes) == 8
    assert len(payload["nodes"][0]["metadata"]["theme_keys"]) == 8
    assert payload["nodes"][0]["metadata"]["theme_source"] == "tier2"


def test_build_payload_empty_returns_none() -> None:
    assert (
        build_agent_chat_progress_payload(
            discord_user_id=1,
            session_id="x",
            insight_snapshot=[],
            active_themes=[],
        )
        is None
    )


def test_build_payload_theme_source_is_tier2_not_heuristic() -> None:
    payload = build_agent_chat_progress_payload(
        discord_user_id=1,
        session_id="source-test",
        insight_snapshot=[{"type": "theme", "label": "Evening wind-down routine"}],
        day="2026-08-06",
    )
    assert payload is not None
    theme_source = payload["nodes"][0]["metadata"]["theme_source"]
    assert theme_source == "tier2"
    assert theme_source != "heuristic"
    assert theme_source != "none"


def test_build_payload_rejects_short_labels() -> None:
    assert (
        build_agent_chat_progress_payload(
            discord_user_id=1,
            session_id="x",
            insight_snapshot=[{"label": "hi"}],
        )
        is None
    )
