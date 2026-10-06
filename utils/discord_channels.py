"""Helpers for Discord guild channels, including obfuscated (hidden) channels.

Discord channel obfuscation (mandatory 16 November 2026) sends channels the bot
cannot view over the Gateway with name ``___hidden___`` and flag
``CHANNEL_OBFUSCATED`` (``1 << 17``). HTTP ``GET /guilds/{id}/channels`` omits
those channels instead.

See: https://docs.discord.com/developers/change-log#channel-obfuscation-for-users-and-bots
"""

from typing import Any

# Official Gateway sentinel for obfuscated channel metadata.
OBFUSCATED_CHANNEL_NAME = "___hidden___"
# ChannelFlags.CHANNEL_OBFUSCATED — discord.py 2.8 may expose this as a named flag.
CHANNEL_OBFUSCATED_FLAG = 1 << 17


def is_obfuscated_channel(channel: Any) -> bool:
    """Return True if ``channel`` is a Discord obfuscated (unviewable) channel.

    Works on discord.py 2.7.x (name + flags) and later (``is_obfuscated`` helper
    or ``ChannelFlags`` named bit) without requiring a library upgrade.
    """
    if channel is None:
        return False

    marker = getattr(channel, "is_obfuscated", None)
    if callable(marker):
        try:
            if marker():
                return True
        except TypeError:
            pass
    elif marker is True:
        return True

    flags = getattr(channel, "flags", None)
    if flags is not None:
        if getattr(flags, "obfuscated", False) or getattr(flags, "is_obfuscated", False):
            return True
        value = getattr(flags, "value", flags)
        try:
            if int(value) & CHANNEL_OBFUSCATED_FLAG:
                return True
        except (TypeError, ValueError):
            pass

    return getattr(channel, "name", None) == OBFUSCATED_CHANNEL_NAME
