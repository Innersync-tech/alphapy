"""Tests for Discord obfuscated-channel detection (Nov 2026 Gateway change)."""

from types import SimpleNamespace

from utils.discord_channels import (
    CHANNEL_OBFUSCATED_FLAG,
    OBFUSCATED_CHANNEL_NAME,
    is_obfuscated_channel,
)


def test_plain_text_channel_is_not_obfuscated() -> None:
    channel = SimpleNamespace(name="general", flags=None)
    assert is_obfuscated_channel(channel) is False


def test_hidden_name_is_obfuscated() -> None:
    channel = SimpleNamespace(name=OBFUSCATED_CHANNEL_NAME, flags=None)
    assert is_obfuscated_channel(channel) is True


def test_obfuscated_flag_bit_is_detected() -> None:
    flags = SimpleNamespace(value=CHANNEL_OBFUSCATED_FLAG)
    channel = SimpleNamespace(name="staff-only", flags=flags)
    assert is_obfuscated_channel(channel) is True


def test_named_flag_attribute_is_detected() -> None:
    flags = SimpleNamespace(obfuscated=True, value=0)
    channel = SimpleNamespace(name="staff-only", flags=flags)
    assert is_obfuscated_channel(channel) is True


def test_discord_py_28_is_obfuscated_method() -> None:
    channel = SimpleNamespace(name="staff-only", flags=None, is_obfuscated=lambda: True)
    assert is_obfuscated_channel(channel) is True


def test_discord_py_28_is_obfuscated_property() -> None:
    channel = SimpleNamespace(name="staff-only", flags=None, is_obfuscated=True)
    assert is_obfuscated_channel(channel) is True


def test_none_channel_is_not_obfuscated() -> None:
    assert is_obfuscated_channel(None) is False


def test_integer_flags_without_value_attr() -> None:
    channel = SimpleNamespace(name="voice-log", flags=CHANNEL_OBFUSCATED_FLAG)
    assert is_obfuscated_channel(channel) is True


def test_listing_skips_hidden_and_flagged_channels() -> None:
    channels = [
        SimpleNamespace(name="general", flags=None),
        SimpleNamespace(name=OBFUSCATED_CHANNEL_NAME, flags=None),
        SimpleNamespace(name="staff", flags=SimpleNamespace(value=CHANNEL_OBFUSCATED_FLAG)),
    ]
    visible = [ch.name for ch in channels if not is_obfuscated_channel(ch)]
    assert visible == ["general"]
