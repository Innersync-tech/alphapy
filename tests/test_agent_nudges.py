"""Unit tests for Phase 5A agent Discord DM nudges."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.nudges import (
    NUDGE_COOLDOWN,
    agent_nudges_enabled,
    build_nudge_dm_text,
    is_due_for_nudge,
    list_due_nudge_candidates,
    user_has_agents_enabled_guild,
)
from agents.profile import normalize_agent_prefs


def test_normalize_agent_prefs_preserves_nudges_flag() -> None:
    prefs = normalize_agent_prefs({"agent_nudges_enabled": True, "persona": "calm"})
    assert prefs.get("agent_nudges_enabled") is True
    assert prefs.get("persona") == "calm"


def test_agent_nudges_enabled_default_off() -> None:
    assert agent_nudges_enabled({}) is False
    assert agent_nudges_enabled({"agent_nudges_enabled": False}) is False
    assert agent_nudges_enabled({"agent_nudges_enabled": True}) is True


def test_is_due_for_nudge_never_sent() -> None:
    assert is_due_for_nudge(None) is True


def test_is_due_for_nudge_within_cooldown() -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    last = now - timedelta(hours=6)
    assert is_due_for_nudge(last, now=now) is False


def test_is_due_for_nudge_after_cooldown() -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    last = now - NUDGE_COOLDOWN - timedelta(minutes=1)
    assert is_due_for_nudge(last, now=now) is True


def test_build_nudge_dm_text_has_no_journal_hooks() -> None:
    text = build_nudge_dm_text(app_url="https://app.innersync.tech/dashboard/agent")
    assert "/agent start" in text
    assert "dashboard/agent" in text
    assert "plaintext" not in text.lower()
    assert "vault" not in text.lower()
    assert "shared reflection" not in text.lower()


def test_user_has_agents_enabled_guild() -> None:
    helper = MagicMock()
    helper.get_bool.side_effect = lambda scope, key, guild_id, fallback=False: guild_id == 2

    member_ok = MagicMock()
    guild_off = SimpleNamespace(id=1, get_member=lambda _uid: member_ok)
    guild_on = SimpleNamespace(id=2, get_member=lambda _uid: member_ok)
    bot = SimpleNamespace(guilds=[guild_off, guild_on], settings_helper=helper)

    assert user_has_agents_enabled_guild(bot, 99) is True

    bot_none = SimpleNamespace(guilds=[guild_off], settings_helper=helper)
    assert user_has_agents_enabled_guild(bot_none, 99) is False


@pytest.mark.asyncio
async def test_list_due_skips_unlinked_and_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_opted_in(**_kwargs):
        return ["user-a", "user-b", "user-c"]

    async def fake_links(_pool, ids):
        return {"user-a": 111, "user-c": 333}

    async def fake_last(_pool, ids):
        return {
            "user-a": None,
            "user-c": datetime.now(UTC) - timedelta(hours=1),
        }

    async def fake_prefs(uid: str):
        return {"agent_nudges_enabled": True}

    monkeypatch.setattr("agents.nudges.fetch_opted_in_user_ids", fake_opted_in)
    monkeypatch.setattr("agents.nudges.load_discord_links_for_users", fake_links)
    monkeypatch.setattr("agents.nudges.load_last_sent_map", fake_last)
    monkeypatch.setattr("agents.nudges.load_agent_prefs", fake_prefs)
    monkeypatch.setattr("agents.nudges.user_has_agents_enabled_guild", lambda *_a, **_k: True)

    bot = SimpleNamespace(guilds=[])
    due = await list_due_nudge_candidates(bot, pool=MagicMock())
    assert len(due) == 1
    assert due[0].innersync_user_id == "user-a"
    assert due[0].discord_user_id == 111
