from __future__ import annotations

import pytest

from agents.tier2 import (
    build_blocklist_from_tier0,
    delete_insight_by_id,
    empty_derived_profile,
    merge_derived_profiles,
    normalize_derived_profile,
    purge_insights_for_reflection,
    session_summary_from_profile,
)


def test_merge_derived_profiles_reinforces_similar_label() -> None:
    existing = {
        "version": 1,
        "insights": [
            {
                "id": "a",
                "type": "theme",
                "label": "harsh inner voice when tired",
                "confidence": 0.7,
                "source_reflection_ids": ["ref-1"],
                "consent_epoch": "2026-06-01T00:00:00Z",
                "last_reinforced_at": "2026-06-01T00:00:00Z",
                "expires_at": None,
            }
        ],
        "active_themes": [],
        "open_loops": [],
    }
    candidate = {
        "insights": [
            {
                "type": "theme",
                "label": "Harsh inner voice when tired",
                "confidence": 0.75,
            }
        ],
        "active_themes": ["self-criticism"],
    }
    merged = merge_derived_profiles(
        existing,
        candidate,
        source_reflection_ids=frozenset({"ref-1"}),
        consent_epoch="2026-06-02T00:00:00Z",
        blocklist=set(),
    )
    assert len(merged["insights"]) == 1
    assert merged["insights"][0]["confidence"] > 0.7
    assert "self-criticism" in merged["active_themes"]


def test_blocklist_rejects_journal_token_in_label() -> None:
    tier0 = "My mantra today: FOCUS and breathe"
    blocklist = build_blocklist_from_tier0(tier0)
    merged = merge_derived_profiles(
        empty_derived_profile(),
        {
            "insights": [
                {
                    "type": "theme",
                    "label": "mantra focus every morning",
                    "confidence": 0.9,
                }
            ]
        },
        source_reflection_ids=frozenset({"ref-1"}),
        consent_epoch="2026-06-02T00:00:00Z",
        blocklist=blocklist,
    )
    assert merged["insights"] == []


def test_purge_insights_for_reflection() -> None:
    derived = normalize_derived_profile(
        {
            "insights": [
                {
                    "id": "1",
                    "type": "theme",
                    "label": "recovery after burnout",
                    "confidence": 0.8,
                    "source_reflection_ids": ["keep-me"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                },
                {
                    "id": "2",
                    "type": "goal",
                    "label": "small steps without judgment",
                    "confidence": 0.7,
                    "source_reflection_ids": ["drop-me"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                },
            ]
        }
    )
    purged = purge_insights_for_reflection(derived, "drop-me")
    assert len(purged["insights"]) == 1
    assert purged["insights"][0]["id"] == "1"
    assert "drop-me" not in purged["insights"][0]["source_reflection_ids"]


def test_delete_insight_by_id() -> None:
    derived = normalize_derived_profile(
        {
            "insights": [
                {
                    "id": "x",
                    "type": "theme",
                    "label": "gentle self talk",
                    "confidence": 0.8,
                    "source_reflection_ids": ["r"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                }
            ]
        }
    )
    updated = delete_insight_by_id(derived, "x")
    assert updated["insights"] == []


def test_session_summary_from_profile() -> None:
    text = session_summary_from_profile(
        {
            "insights": [
                {
                    "id": "1",
                    "type": "theme",
                    "label": "energy dips after meetings",
                    "confidence": 0.8,
                    "source_reflection_ids": [],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                }
            ]
        }
    )
    assert "energy dips" in text
    assert "Derived patterns" in text


@pytest.mark.asyncio
async def test_purge_tier2_for_reflection_local(monkeypatch) -> None:
    import config
    from agents.memory import clear_local_store, get_user_memory, patch_user_memory, purge_tier2_for_reflection

    monkeypatch.setattr(config, "ALPHAPY_AGENTS_MEMORY_BACKEND", "memory")
    clear_local_store()

    user_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    await patch_user_memory(
        user_id,
        "reflection",
        {
            "session_count": 2,
            "derived_profile": {
                "version": 1,
                "insights": [
                    {
                        "id": "1",
                        "type": "theme",
                        "label": "recovery pacing",
                        "confidence": 0.8,
                        "source_reflection_ids": ["ref-a"],
                        "consent_epoch": "t",
                        "last_reinforced_at": "t",
                    }
                ],
                "active_themes": [],
                "open_loops": [],
            },
        },
    )

    await purge_tier2_for_reflection(user_id, "reflection", "ref-a")
    stored = await get_user_memory(user_id, "reflection")
    assert "derived_profile" not in stored
    assert stored.get("session_count") == 2
