from __future__ import annotations

import pytest

from agents.tier2 import (
    append_skill_insights,
    build_blocklist_from_tier0,
    build_session_insight_snapshot,
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


def test_append_skill_insights_merges_valid_candidate() -> None:
    existing = normalize_derived_profile(
        {
            "insights": [
                {
                    "id": "a",
                    "type": "theme",
                    "label": "avoidance when overwhelmed",
                    "confidence": 0.7,
                    "source_reflection_ids": ["ref-1"],
                    "consent_epoch": "2026-06-01T00:00:00Z",
                    "last_reinforced_at": "2026-06-01T00:00:00Z",
                }
            ]
        }
    )
    merged = append_skill_insights(
        existing,
        [{"type": "habit", "label": "two minute pause before scrolling", "confidence": 0.75}],
        source_reflection_ids=frozenset({"ref-1"}),
        consent_epoch="2026-06-02T00:00:00Z",
    )
    assert len(merged["insights"]) == 2
    types = {item["type"] for item in merged["insights"]}
    assert types == {"theme", "habit"}


def test_build_session_insight_snapshot_new_and_reinforced() -> None:
    before = normalize_derived_profile(
        {
            "insights": [
                {
                    "id": "old",
                    "type": "theme",
                    "label": "gentle morning routine",
                    "confidence": 0.6,
                    "source_reflection_ids": ["r"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                }
            ]
        }
    )
    after = normalize_derived_profile(
        {
            "insights": [
                {
                    "id": "old",
                    "type": "theme",
                    "label": "gentle morning routine",
                    "confidence": 0.85,
                    "source_reflection_ids": ["r"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                },
                {
                    "id": "new",
                    "type": "habit",
                    "label": "single breath before replying",
                    "confidence": 0.7,
                    "source_reflection_ids": ["r"],
                    "consent_epoch": "t",
                    "last_reinforced_at": "t",
                },
            ]
        }
    )
    snapshot = build_session_insight_snapshot(before, after)
    assert len(snapshot) == 2
    assert {row["id"] for row in snapshot} == {"old", "new"}


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


def test_format_catalog_for_distill_keep_apart_and_cap() -> None:
    from agents.tier2 import (
        CATALOG_INSIGHT_CAP,
        CATALOG_KEEP_APART_RULES,
        INSIGHT_TYPE_RULES,
        format_catalog_for_distill,
        with_catalog_user_message,
    )

    existing = {
        "insights": [
            {
                "type": "habit",
                "label": "waiting before acting on impulse",
                "confidence": 0.7,
            },
            {"type": "habit", "label": "short", "confidence": 0.9},
            {
                "type": "theme",
                "label": "resting to recover energy after strain",
                "confidence": 0.8,
            },
        ]
    }
    lines = format_catalog_for_distill(existing)
    assert "waiting before acting on impulse (habit)" in lines
    assert "resting to recover energy after strain (theme)" in lines
    assert "short" not in lines

    many = {
        "insights": [
            {
                "type": "theme",
                "label": f"recurring inner critic pattern number {i}",
                "confidence": 0.7,
            }
            for i in range(CATALOG_INSIGHT_CAP + 5)
        ]
    }
    assert len(format_catalog_for_distill(many).splitlines()) == CATALOG_INSIGHT_CAP
    assert format_catalog_for_distill({}) == ""

    assert "EXACT stored label" in CATALOG_KEEP_APART_RULES
    assert "keep them apart" in CATALOG_KEEP_APART_RULES
    assert "impulse control" in CATALOG_KEEP_APART_RULES
    assert "trigger: the cue" in INSIGHT_TYPE_RULES
    assert "habit: what you then do or avoid" in INSIGHT_TYPE_RULES
    assert "emit two insights (trigger + habit)" in INSIGHT_TYPE_RULES
    assert "type cues as trigger" in INSIGHT_TYPE_RULES
    base = "Ephemeral journal context (do not quote):\nhi"
    with_cat = with_catalog_user_message(base, lines)
    assert with_cat.startswith("Existing catalog")
    assert "waiting before acting on impulse" in with_cat
    assert with_catalog_user_message(base, "") == base


@pytest.mark.asyncio
async def test_distill_session_profile_injects_catalog(monkeypatch) -> None:
    import json

    from agents.tier2 import distill_session_profile, normalize_derived_profile

    captured: dict[str, list] = {}

    async def _fake_ask_gpt(messages, **_kwargs):
        captured["messages"] = messages
        return json.dumps(
            {
                "insights": [
                    {
                        "type": "habit",
                        "label": "waiting before acting on impulse",
                        "confidence": 0.8,
                    }
                ],
                "active_themes": [],
            }
        )

    monkeypatch.setattr("agents.tier2.ask_gpt", _fake_ask_gpt)
    existing = normalize_derived_profile(
        {
            "insights": [
                {
                    "type": "habit",
                    "label": "waiting before acting on impulse",
                    "confidence": 0.7,
                    "source_reflection_ids": ["ref-1"],
                }
            ]
        }
    )
    merged = await distill_session_profile(
        tier0_context="opted-in notes about evening wind-down after meetings with colleagues",
        user_message="I keep jumping in",
        agent_response="Notice the pause before you act.",
        source_reflection_ids=frozenset({"ref-1"}),
        existing=existing,
        discord_user_id=1,
        guild_id=2,
    )
    system = captured["messages"][0]["content"]
    user = captured["messages"][1]["content"]
    assert "keep them apart" in system
    assert "trigger: the cue" in system
    assert "waiting before acting on impulse" in user
    assert "Existing catalog" in user
    assert merged is not None
    assert any(
        "waiting before acting on impulse" in str(i.get("label"))
        for i in merged.get("insights") or []
    )


@pytest.mark.asyncio
async def test_distill_session_profile_allows_transcript_only(monkeypatch) -> None:
    import json

    from agents.tier2 import distill_session_profile

    async def _fake_ask_gpt(messages, **_kwargs):
        return json.dumps(
            {
                "insights": [
                    {
                        "type": "habit",
                        "label": "naming the freeze before sending",
                        "confidence": 0.8,
                    }
                ],
                "active_themes": ["pause before send"],
            }
        )

    monkeypatch.setattr("agents.tier2.ask_gpt", _fake_ask_gpt)
    merged = await distill_session_profile(
        tier0_context="",
        user_message="I freeze before I send the message",
        agent_response="Name the pause, then send a smaller note.",
        source_reflection_ids=frozenset(),
        existing={},
        discord_user_id=1,
        guild_id=2,
    )
    assert merged is not None
    assert any(
        "naming the freeze before sending" in str(i.get("label"))
        for i in merged.get("insights") or []
    )


@pytest.mark.asyncio
async def test_distill_session_profile_skips_empty_sources() -> None:
    from agents.tier2 import distill_session_profile

    merged = await distill_session_profile(
        tier0_context="",
        user_message="",
        agent_response="",
        source_reflection_ids=frozenset(),
        existing={},
        discord_user_id=1,
        guild_id=2,
    )
    assert merged is None


