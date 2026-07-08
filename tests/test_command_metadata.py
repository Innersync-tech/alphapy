"""Tests for command list metadata helpers."""

from types import SimpleNamespace

import discord

from utils.command_metadata import (
    find_enable_disable_pair,
    format_command_pair,
    get_category_for_command,
    get_category_for_cog,
    is_admin_command,
)


class TestGetCategory:
    def test_override_help(self):
        assert get_category_for_command("Other", "help") == "📊 Status & Info"

    def test_override_link(self):
        assert get_category_for_command("Other", "link") == "🔗 Innersync"

    def test_cog_fallback(self):
        assert get_category_for_command("ReminderCog", "add_reminder") == "⏰ Reminders"

    def test_unknown_cog(self):
        assert get_category_for_cog("UnknownCog") == "📦 UnknownCog"


class TestIsAdminCommand:
    def test_explicit_admin_path(self):
        assert is_admin_command("command_stats", "command_stats", False) is True

    def test_debug_parse_embed_admin(self):
        assert is_admin_command("debug_parse_embed", "debug_parse_embed", False) is True

    def test_cooldown_checks_not_admin(self):
        assert (
            is_admin_command(
                "growthcheckin",
                "growthcheckin",
                has_checks=True,
                has_permission_checks=False,
            )
            is False
        )

    def test_permission_checks_are_admin(self):
        assert (
            is_admin_command(
                "ticket_stats",
                "ticket_stats",
                has_checks=True,
                has_permission_checks=True,
            )
            is True
        )

    def test_administrator_default_permissions(self):
        perms = discord.Permissions(administrator=True)
        assert (
            is_admin_command(
                "reminders",
                "reminders show",
                False,
                default_permissions=perms,
            )
            is True
        )

    def test_description_admin_keyword(self):
        assert (
            is_admin_command(
                "stats",
                "stats",
                False,
                description="Show ticket stats (admin only)",
            )
            is True
        )


class TestEnableDisablePairs:
    def test_find_pair_returns_none_for_toggle_commands(self):
        assert find_enable_disable_pair("onboarding toggle", []) is None

    def test_find_pair_fallback_matching_disable(self):
        all_cmds = [
            {"full_path": "invites enable", "name": "enable", "description": "Enable invites"},
            {"full_path": "invites disable", "name": "disable", "description": "Disable invites"},
        ]
        pair = find_enable_disable_pair("invites enable", all_cmds)
        assert pair is not None
        assert pair["full_path"] == "invites disable"

    def test_format_command_pair_strips_enable_prefix(self):
        enable = {"full_path": "gdpr enable", "description": "Enable GDPR module"}
        disable = {"full_path": "gdpr disable", "description": "Disable GDPR module"}
        line = format_command_pair(enable, disable)
        assert "`/gdpr enable`" in line
        assert "`/gdpr disable`" in line
        assert "GDPR module" in line


class TestStatusPermissionCheckHelper:
    def test_cooldown_check_skipped(self):
        from cogs.status import _command_has_permission_checks

        cooldown = SimpleNamespace(rate=1, per=30.0)
        cmd = SimpleNamespace(checks=[cooldown])
        assert _command_has_permission_checks(cmd) is False

    def test_non_cooldown_check_counts(self):
        from cogs.status import _command_has_permission_checks

        other = SimpleNamespace(callback=lambda: None)
        cmd = SimpleNamespace(checks=[other])
        assert _command_has_permission_checks(cmd) is True
