from __future__ import annotations

from agents.channels import merge_channel_metadata


def test_merge_channel_metadata_start_discord() -> None:
    merged = merge_channel_metadata({}, channel="discord", is_start=True, guild_id=42)
    assert merged == {
        "origin_channel": "discord",
        "last_channel": "discord",
        "origin_guild_id": "42",
    }


def test_merge_channel_metadata_continue_app() -> None:
    existing = {"origin_channel": "discord", "last_channel": "discord", "origin_guild_id": "42"}
    merged = merge_channel_metadata(existing, channel="app", is_start=False)
    assert merged["origin_channel"] == "discord"
    assert merged["last_channel"] == "app"
    assert merged["origin_guild_id"] == "42"
