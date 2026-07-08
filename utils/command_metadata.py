"""
Command Metadata Configuration

This module provides a centralized, maintainable system for:
- Command categorization
- Enable/disable command pairing
- Admin command detection
- Command display formatting

All command metadata is defined here in a clear, structured format.
"""

from typing import Any

# Mapping of cog class names to friendly category names with emojis
COG_CATEGORY_MAP: dict[str, str] = {
    "Status": "📊 Status & Info",
    "TicketBot": "🎫 Tickets",
    "ReminderCog": "⏰ Reminders",
    "LearnTopic": "📚 Learning",
    "LeaderHelp": "👥 Leadership",
    "ContentGen": "✍️ Content",
    "GrowthCheckin": "🌱 Growth",
    "InviteTracker": "📥 Invites",
    "CustomSlashCommands": "🛠️ Utilities",
    "Configuration": "⚙️ Configuration",
    "AgentsCog": "🤖 Agents",
    "FAQ": "❓ FAQ",
    "Exports": "📤 Exports",
    "EmbedReminderWatcher": "👀 Embed Watcher",
    "Clean": "🧹 Clean",
    "Migrations": "🔄 Migrations",
    "DataQuery": "📊 Data",
    "ReloadCommands": "🔄 Reload",
    "AILotQuiz": "🎲 Quiz",
    "GDPRAnnouncement": "🔐 GDPR",
    "PremiumCog": "💎 Premium",
    "EngagementCog": "🏆 Engagement",
    "VerificationCog": "✅ Verification",
    "CustomCommandsCog": "⚙️ Custom Commands",
    "DeleteMyDataCog": "🔒 Privacy",
    "AutoModeration": "🛡️ AutoMod",
    "Onboarding": "📋 Onboarding",
    "InnersyncIdentityCog": "🔗 Innersync",
}

# Override category by full command path (for module-level commands without cog binding)
COMMAND_CATEGORY_OVERRIDES: dict[str, str] = {
    "gptstatus": "📊 Status & Info",
    "version": "📊 Status & Info",
    "innersync": "📊 Status & Info",
    "release": "📊 Status & Info",
    "health": "📊 Status & Info",
    "commands": "📊 Status & Info",
    "help": "📊 Status & Info",
    "command_stats": "📊 Status & Info",
    "link": "🔗 Innersync",
    "unlink": "🔗 Innersync",
    "profile": "🔗 Innersync",
}


# Toggle commands use a single `toggle` subcommand with a bool — no enable/disable pairs.
ENABLE_DISABLE_PAIRS: list[tuple[str, tuple[str, str]]] = []


# Commands that are explicitly admin-only (by full path or name)
ADMIN_COMMANDS: set[str] = {
    "config",
    "clean",
    "sendto",
    "embed",
    "export_tickets",
    "export_faq",
    "migrate",
    "migrate_status",
    "reload",
    "command_stats",
    "ticket_stats",
    "ticket_status",
    "ticket_panel_post",
    "debug_parse_embed",
}


# Commands that should be excluded from the command list
HIDDEN_COMMANDS: set[str] = set()


def get_category_for_cog(cog_name: str) -> str:
    """Get the friendly category name for a cog class name."""
    return COG_CATEGORY_MAP.get(cog_name, f"📦 {cog_name}")


def get_category_for_command(cog_name: str, full_path: str) -> str:
    """Resolve display category using path overrides, then cog map."""
    if full_path in COMMAND_CATEGORY_OVERRIDES:
        return COMMAND_CATEGORY_OVERRIDES[full_path]
    return get_category_for_cog(cog_name)


def is_admin_command(
    command_name: str,
    full_path: str,
    has_checks: bool,
    default_permissions: Any = None,
    description: str | None = None,
    *,
    has_permission_checks: bool = False,
) -> bool:
    """
    Determine if a command is admin-only.

    Args:
        command_name: The command name (e.g., "enable")
        full_path: Full command path (e.g., "config invites enable")
        has_checks: Whether the command has any checks (including cooldown)
        default_permissions: Command's default_permissions attribute
        description: Command description
        has_permission_checks: True only for admin/owner permission checks (not cooldown)

    Returns:
        True if the command is admin-only, False otherwise
    """
    if command_name in ADMIN_COMMANDS or full_path in ADMIN_COMMANDS:
        return True

    for admin_cmd in ADMIN_COMMANDS:
        if full_path.startswith(admin_cmd + " ") or full_path == admin_cmd:
            return True

    if default_permissions is not None and hasattr(default_permissions, "administrator"):
        if getattr(default_permissions, "administrator", False):
            return True

    # Cooldown-only checks must not hide public commands from /commands
    if has_permission_checks:
        return True

    if description:
        desc_lower = description.lower()
        if "admin" in desc_lower or "owner" in desc_lower or "(admin" in desc_lower:
            return True

    cmd_name_lower = command_name.lower()
    admin_keywords = ["config", "clean", "sendto", "export", "migrate", "sync", "reload", "command_stats"]
    if any(keyword in cmd_name_lower for keyword in admin_keywords):
        return True

    # Legacy: has_checks without distinguishing cooldown — do not treat as admin
    _ = has_checks
    return False


def find_enable_disable_pair(full_path: str, all_commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the matching enable/disable pair for a command.
    """
    is_enable = full_path.endswith(" enable") or full_path.split()[-1] == "enable"
    is_disable = full_path.endswith(" disable") or full_path.split()[-1] == "disable"

    if not (is_enable or is_disable):
        return None

    base_path = full_path.rsplit(" ", 1)[0] if " " in full_path else ""

    for pair_base, pair_paths in ENABLE_DISABLE_PAIRS:
        if base_path == pair_base and len(pair_paths) >= 2:
            enable_path, disable_path = pair_paths[0], pair_paths[1]
            target_path = disable_path if is_enable else enable_path
            for cmd in all_commands:
                if cmd.get("full_path") == target_path:
                    return cmd
            break

    for cmd in all_commands:
        other_path = cmd.get("full_path", cmd.get("name", ""))
        other_name = cmd.get("name", "")

        is_other_enable = other_path.endswith(" enable") or other_name == "enable"
        is_other_disable = other_path.endswith(" disable") or other_name == "disable"

        if (is_enable and is_other_disable) or (is_disable and is_other_enable):
            other_base = other_path.rsplit(" ", 1)[0] if " " in other_path else ""
            if base_path.lower() == other_base.lower():
                return cmd

    return None


def format_command_pair(enable_cmd: dict[str, Any], disable_cmd: dict[str, Any]) -> str:
    """Format an enable/disable command pair as a single line."""
    enable_path = enable_cmd.get("full_path", enable_cmd.get("name", ""))
    disable_path = disable_cmd.get("full_path", disable_cmd.get("name", ""))

    desc = enable_cmd.get("description") or disable_cmd.get("description") or ""
    desc_lower = desc.lower()

    if desc_lower.startswith("enable/disable"):
        desc = desc[15:].strip()
    elif desc_lower.startswith("enable"):
        desc = desc[7:].strip()
    elif desc_lower.startswith("disable"):
        desc = desc[8:].strip()

    enable_display = f"/{enable_path.replace(' ', ' ')}"
    disable_display = f"/{disable_path.replace(' ', ' ')}"

    return f"`{enable_display}` / `{disable_display}` — Enable/disable {desc[:50]}"
