from __future__ import annotations

from agents.profile import (
    build_agent_profile_block,
    extract_tier3_memory,
    normalize_agent_prefs,
    tier3_memory_patch,
)


def test_normalize_agent_prefs_trims_and_validates_persona() -> None:
    prefs = normalize_agent_prefs(
        {
            "display_name": "  Nova  ",
            "persona": "INVALID",
            "default_focus": "boundaries",
        }
    )
    assert prefs["display_name"] == "Nova"
    assert prefs["persona"] == "calm"
    assert prefs["default_focus"] == "boundaries"


def test_normalize_agent_prefs_preserves_learning_flags() -> None:
    prefs = normalize_agent_prefs(
        {
            "display_name": "Nova",
            "persona": "calm",
            "learn_from_shared": True,
            "learn_from_patterns": True,
            "energy_level": "3",
        }
    )
    assert prefs["learn_from_shared"] is True
    assert prefs["learn_from_patterns"] is True
    assert prefs["display_name"] == "Nova"
    assert prefs["energy_level"] == "3"


def test_normalize_agent_prefs_fatigue_merge_keeps_app_fields() -> None:
    """Discord energy write must not drop App Tier-1 prefs."""
    merged = {
        "display_name": "Nova",
        "persona": "direct",
        "default_focus": "boundaries",
        "inner_voice": "harsh when tired",
        "learn_from_shared": True,
        "learn_from_patterns": False,
        "energy_level": "2",
        "fatigue_reported_at": "2026-07-30T12:00:00+00:00",
    }
    prefs = normalize_agent_prefs(merged)
    assert prefs["display_name"] == "Nova"
    assert prefs["persona"] == "direct"
    assert prefs["default_focus"] == "boundaries"
    assert prefs["inner_voice"] == "harsh when tired"
    assert prefs["learn_from_shared"] is True
    assert prefs["learn_from_patterns"] is False
    assert prefs["energy_level"] == "2"


def test_build_agent_profile_block_includes_tier1_and_tier3() -> None:
    block = build_agent_profile_block(
        {"display_name": "Nova", "persona": "direct", "default_focus": "energy"},
        {"session_count": 3, "last_session_at": "2026-06-30T12:00:00+00:00"},
    )
    assert "Preferred agent name: Nova" in block
    assert "Tone: direct" in block
    assert "Default reflection focus: energy" in block
    assert "Prior agent sessions" in block


def test_extract_tier3_memory_strips_legacy_keys() -> None:
    tier3 = extract_tier3_memory(
        {
            "session_count": 2,
            "last_summary_preview": "secret journal",
            "display_name": "should-not-be-here",
        }
    )
    assert tier3 == {"session_count": 2}


def test_tier3_memory_patch_increments_count() -> None:
    patch = tier3_memory_patch(
        session_id="sess-1",
        agent_name="reflection",
        prior_session_count=4,
    )
    assert patch["session_count"] == 5
    assert patch["last_agent"] == "reflection"
    assert patch["last_session_id"] == "sess-1"
    assert "last_session_at" in patch
