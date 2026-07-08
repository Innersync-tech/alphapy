"""Tests for live-session preset helpers."""

from cogs.reminders import LIVE_SESSION_MESSAGE, LIVE_SESSION_NAME, is_live_session_row


def test_is_live_session_row_valid():
    row = {"name": LIVE_SESSION_NAME, "message": LIVE_SESSION_MESSAGE}
    assert is_live_session_row(row) is True


def test_is_live_session_row_wrong_name():
    row = {"name": "Weekly standup", "message": LIVE_SESSION_MESSAGE}
    assert is_live_session_row(row) is False


def test_is_live_session_row_wrong_message():
    row = {"name": LIVE_SESSION_NAME, "message": "Other message"}
    assert is_live_session_row(row) is False


def test_is_live_session_row_none():
    assert is_live_session_row(None) is False
