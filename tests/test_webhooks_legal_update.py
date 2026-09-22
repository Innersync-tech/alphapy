"""Tests for POST /webhooks/legal-update.

Uses a minimal FastAPI app so the suite does not boot the Discord bot.
"""

from concurrent.futures import Future
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webhooks.legal_update import router as legal_update_router

app = FastAPI()
app.include_router(legal_update_router)
client = TestClient(app)

_TOS_BODY = '{"documents": ["tos"], "tos_version": "2026-09-22"}'


def _ready(monkeypatch, channel_id: int = 99):
    monkeypatch.setattr(
        "webhooks.legal_update.get_legal_update_webhook_secret", lambda: None
    )
    monkeypatch.setattr("config.MAIN_GUILD_ID", 1)
    monkeypatch.setattr("config.LEGAL_UPDATES_CHANNEL_ID", channel_id)


class TestLegalUpdateWebhook:
    def test_missing_documents_returns_400(self, monkeypatch):
        _ready(monkeypatch)
        response = client.post(
            "/webhooks/legal-update",
            content="{}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert "documents" in response.json().get("detail", "").lower()

    def test_returns_503_when_bot_not_available(self, monkeypatch):
        _ready(monkeypatch)
        monkeypatch.setattr("gpt.helpers.bot_instance", None)
        response = client.post(
            "/webhooks/legal-update",
            content=_TOS_BODY,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 503

    def test_posts_on_the_bot_loop(self, monkeypatch):
        _ready(monkeypatch)
        future = Future()
        future.set_result(["tos"])
        mock_bot = MagicMock()
        mock_bot.loop = MagicMock()
        monkeypatch.setattr("gpt.helpers.bot_instance", mock_bot)

        def _threadsafe(coro, loop):
            coro.close()
            assert loop is mock_bot.loop
            return future

        monkeypatch.setattr("webhooks.legal_update.asyncio.run_coroutine_threadsafe", _threadsafe)
        response = client.post(
            "/webhooks/legal-update",
            content=_TOS_BODY,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "acknowledged", "sent": "tos"}

    def test_send_failure_is_not_a_success(self, monkeypatch):
        _ready(monkeypatch)
        future = Future()
        future.set_exception(RuntimeError("timeout context"))
        mock_bot = MagicMock()
        mock_bot.loop = MagicMock()
        monkeypatch.setattr("gpt.helpers.bot_instance", mock_bot)

        def _threadsafe(coro, loop):
            coro.close()
            return future

        monkeypatch.setattr("webhooks.legal_update.asyncio.run_coroutine_threadsafe", _threadsafe)
        response = client.post(
            "/webhooks/legal-update",
            content=_TOS_BODY,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 500
