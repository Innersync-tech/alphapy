from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from utils import app_reflection_purge as purge


def _pool_with_delete_rows(user_ids: list[int]) -> MagicMock:
    rows = [{"user_id": uid} for uid in user_ids]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool._conn = conn
    return pool


def _pool_with_user_delete(reflection_ids: list[str]) -> MagicMock:
    rows = [{"reflection_id": rid} for rid in reflection_ids]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool._conn = conn
    return pool


@pytest.mark.asyncio
async def test_delete_app_reflection_rows_uses_reflection_id_only() -> None:
    pool = _pool_with_delete_rows([111, 222])
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="ref-1")
    assert count == 2
    assert user_ids == [111, 222]
    sql = pool._conn.fetch.await_args.args[0]
    assert "DELETE FROM app_reflections" in sql
    assert "plaintext_content" not in sql
    assert pool._conn.fetch.await_args.args[1] == "ref-1"


@pytest.mark.asyncio
async def test_delete_app_reflection_rows_empty_id() -> None:
    pool = MagicMock()
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="  ")
    assert count == 0
    assert user_ids == []
    pool.acquire.assert_not_called()


@pytest.mark.asyncio
async def test_purge_app_reflection_copy_deletes_and_purges_tier2(monkeypatch) -> None:
    pool = _pool_with_delete_rows([42])
    monkeypatch.setattr(
        purge,
        "get_innersync_id_for_discord",
        AsyncMock(return_value="user-uuid"),
    )
    tier2 = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", tier2)
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=False))
    forwarded: list[dict] = []
    monkeypatch.setattr(
        "utils.dashboard_webhooks.forward_revoke_reflection",
        lambda payload: forwarded.append(payload),
    )

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=42,
    )
    assert result["deleted"] == 1
    assert result["tier2_purged"] == 1
    tier2.assert_awaited_once_with("user-uuid", "reflection", "ref-1")
    assert forwarded == [{"user_id": 42, "reflection_id": "ref-1"}]


@pytest.mark.asyncio
async def test_delete_skips_unparseable_user_ids() -> None:
    pool = _pool_with_delete_rows([])
    pool._conn.fetch = AsyncMock(return_value=[{"user_id": "not-an-int"}, {"user_id": 9}])
    count, user_ids = await purge.delete_app_reflection_rows(pool, reflection_id="ref-1")
    assert count == 2
    assert user_ids == [9]


@pytest.mark.asyncio
async def test_purge_uses_explicit_innersync_id_and_swallows_tier2_errors(monkeypatch) -> None:
    pool = _pool_with_delete_rows([])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "agents.memory.purge_tier2_for_reflection",
        AsyncMock(side_effect=RuntimeError("memory down")),
    )
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=False))
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=55,
        innersync_user_id="user-uuid",
    )
    assert result["deleted"] == 0
    assert result["tier2_purged"] == 0
    assert 55 in result["user_ids"]


@pytest.mark.asyncio
async def test_purge_app_reflection_copy_is_idempotent_when_missing(monkeypatch) -> None:
    pool = _pool_with_delete_rows([])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value=None))
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", AsyncMock())
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(pool, reflection_id="gone")
    assert result["deleted"] == 0
    assert result["tier2_purged"] == 0


def test_coerce_purge_patterns() -> None:
    assert purge.coerce_purge_patterns(True) is True
    assert purge.coerce_purge_patterns("true") is True
    assert purge.coerce_purge_patterns("YES") is True
    assert purge.coerce_purge_patterns(False) is False
    assert purge.coerce_purge_patterns(None) is False
    assert purge.coerce_purge_patterns("no") is False


@pytest.mark.asyncio
async def test_has_active_patterns_consent_true(monkeypatch) -> None:
    monkeypatch.setattr(purge, "_supabase_get", AsyncMock(return_value=[{"reflection_id": "ref-1"}]))
    assert await purge.has_active_patterns_consent("user-uuid", "ref-1") is True


@pytest.mark.asyncio
async def test_has_active_patterns_consent_false(monkeypatch) -> None:
    monkeypatch.setattr(purge, "_supabase_get", AsyncMock(return_value=[]))
    assert await purge.has_active_patterns_consent("user-uuid", "ref-1") is False


@pytest.mark.asyncio
async def test_has_active_patterns_consent_lookup_failure(monkeypatch) -> None:
    monkeypatch.setattr(purge, "_supabase_get", AsyncMock(side_effect=RuntimeError("down")))
    assert await purge.has_active_patterns_consent("user-uuid", "ref-1") is None


@pytest.mark.asyncio
async def test_has_active_patterns_consent_unconfigured(monkeypatch) -> None:
    from utils.supabase_client import SupabaseConfigurationError

    monkeypatch.setattr(
        purge,
        "_supabase_get",
        AsyncMock(side_effect=SupabaseConfigurationError("missing")),
    )
    assert await purge.has_active_patterns_consent("user-uuid", "ref-1") is None
    assert await purge.has_active_patterns_consent("", "ref-1") is None


@pytest.mark.asyncio
async def test_purge_skips_tier2_when_patterns_consent_active(monkeypatch) -> None:
    pool = _pool_with_delete_rows([42])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value="user-uuid"))
    tier2 = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", tier2)
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=True))
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=42,
    )
    assert result["deleted"] == 1
    assert result["tier2_purged"] == 0
    tier2.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_skips_tier2_when_consent_lookup_fails(monkeypatch) -> None:
    pool = _pool_with_delete_rows([42])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value="user-uuid"))
    tier2 = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", tier2)
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=None))
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(pool, reflection_id="ref-1", discord_user_id=42)
    assert result["tier2_purged"] == 0
    tier2.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_patterns_true_strips_even_with_consent(monkeypatch) -> None:
    pool = _pool_with_delete_rows([42])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value="user-uuid"))
    tier2 = AsyncMock()
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", tier2)
    consent = AsyncMock(return_value=True)
    monkeypatch.setattr(purge, "has_active_patterns_consent", consent)
    monkeypatch.setattr("utils.dashboard_webhooks.forward_revoke_reflection", lambda _p: None)

    result = await purge.purge_app_reflection_copy(
        pool,
        reflection_id="ref-1",
        discord_user_id=42,
        purge_patterns=True,
    )
    assert result["tier2_purged"] == 1
    tier2.assert_awaited_once()
    consent.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_app_reflections_for_user_deletes_and_revokes(monkeypatch) -> None:
    pool = _pool_with_user_delete(["ref-a", "ref-b"])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=True))
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", AsyncMock())
    revoke = AsyncMock(return_value=2)
    monkeypatch.setattr(purge, "revoke_alphapy_share_consent_for_user", revoke)

    result = await purge.purge_app_reflections_for_user(
        pool,
        discord_user_id=99,
        innersync_user_id="user-uuid",
    )
    assert result["deleted"] == 2
    assert result["reflection_ids"] == ["ref-a", "ref-b"]
    assert result["tier2_purged"] == 0
    assert result["consent_revoked"] == 2
    revoke.assert_awaited_once_with("user-uuid")
    sql = pool._conn.fetch.await_args.args[0]
    assert "DELETE FROM app_reflections" in sql
    assert "WHERE user_id = $1" in sql
    assert "plaintext_content" not in sql


@pytest.mark.asyncio
async def test_purge_app_reflections_for_user_fail_closed_on_consent(monkeypatch) -> None:
    pool = _pool_with_user_delete(["ref-a"])
    monkeypatch.setattr(purge, "get_innersync_id_for_discord", AsyncMock(return_value="user-uuid"))
    monkeypatch.setattr(purge, "has_active_patterns_consent", AsyncMock(return_value=False))
    monkeypatch.setattr("agents.memory.purge_tier2_for_reflection", AsyncMock())
    monkeypatch.setattr(
        purge,
        "revoke_alphapy_share_consent_for_user",
        AsyncMock(side_effect=RuntimeError("supabase down")),
    )
    with pytest.raises(RuntimeError, match="supabase down"):
        await purge.purge_app_reflections_for_user(
            pool,
            discord_user_id=99,
            innersync_user_id="user-uuid",
        )


@pytest.mark.asyncio
async def test_revoke_alphapy_share_consent_for_user(monkeypatch) -> None:
    patched = AsyncMock(return_value=[{"reflection_id": "a"}, {"reflection_id": "b"}])
    monkeypatch.setattr(purge, "_supabase_patch", patched)
    count = await purge.revoke_alphapy_share_consent_for_user("user-uuid")
    assert count == 2
    assert patched.await_args.args[0] == "reflection_alphapy_consent"
    assert patched.await_args.args[1]["revoked_at"]
    assert patched.await_args.args[2]["user_id"] == "eq.user-uuid"
    assert patched.await_args.args[2]["revoked_at"] == "is.null"
