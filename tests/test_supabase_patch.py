from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest


@pytest.mark.asyncio
async def test_supabase_patch_returns_rows(monkeypatch) -> None:
    import utils.supabase_client as sc

    monkeypatch.setattr(sc, "SUPABASE_URL", "https://sb.example")
    monkeypatch.setattr(sc, "SUPABASE_SERVICE_ROLE_KEY", "role-key")

    class _Resp:
        status_code = 200
        content = b'[{"id":1}]'
        text = '[{"id":1}]'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"id": 1}]

    patch_mock = AsyncMock(return_value=_Resp())

    class _Client:
        def __init__(self, *a, **k):
            self.patch = patch_mock

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(sc.httpx, "AsyncClient", _Client)
    rows = await sc._supabase_patch(
        "reflection_alphapy_consent",
        {"revoked_at": "2026-10-11T00:00:00+00:00"},
        {"user_id": "eq.user-uuid", "revoked_at": "is.null"},
    )
    assert rows == [{"id": 1}]
    patch_mock.assert_awaited_once()
    assert "reflection_alphapy_consent" in patch_mock.await_args.args[0]


@pytest.mark.asyncio
async def test_supabase_patch_raises_on_http_error(monkeypatch) -> None:
    import utils.supabase_client as sc

    monkeypatch.setattr(sc, "SUPABASE_URL", "https://sb.example")
    monkeypatch.setattr(sc, "SUPABASE_SERVICE_ROLE_KEY", "role-key")

    class _Resp:
        status_code = 400
        text = "bad"

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("PATCH", "https://x"), response=httpx.Response(400))

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def patch(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(sc.httpx, "AsyncClient", _Client)
    with pytest.raises(httpx.HTTPStatusError):
        await sc._supabase_patch("t", {"a": 1}, {"id": "eq.1"})
