"""Unit tests for /growthhistory display helpers."""

from datetime import UTC, datetime

from cogs.growth import GrowthDetailView, GrowthHistoryView, _checkin_date_str


def test_checkin_date_str_from_datetime() -> None:
    row = {"created_at": datetime(2026, 7, 14, 21, 18, tzinfo=UTC)}
    assert _checkin_date_str(row) == "2026-07-14"


def test_detail_embed_uses_plaintext_fields_not_ciphertext() -> None:
    row = {
        "id": 1,
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "goal": "More focus",
        "obstacle": "Late nights",
        "feeling": "Tired but hopeful",
        "grok_response": "You are noticing a pattern — start with one early night.",
    }
    view = GrowthDetailView(row=row, back_view=GrowthHistoryView([]), discord_user_id=1)
    embed = view.build_embed()
    field_map = {f.name: f.value for f in embed.fields}
    assert field_map["Goal"] == "More focus"
    assert field_map["Obstacle"] == "Late nights"
    assert field_map["How I felt"] == "Tired but hopeful"
    assert "early night" in field_map["🤖 Grok's reflection"]
    assert "4Sb6jVdv" not in field_map["🤖 Grok's reflection"]


def test_history_list_uses_goal_preview() -> None:
    rows = [
        {
            "id": 2,
            "created_at": datetime(2026, 7, 14, tzinfo=UTC),
            "goal": "Ship the fix",
            "obstacle": "Ciphertext in vault",
            "feeling": "Annoyed",
            "grok_response": "Store growth history on Railway.",
        }
    ]
    view = GrowthHistoryView(checkins=rows, discord_user_id=42)
    embed = view.build_embed()
    assert "Ship the fix" in embed.fields[0].value
    assert "Ciphertext in vault" in embed.fields[0].value
