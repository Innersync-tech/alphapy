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
}


# Explicit mapping of enable/disable command pairs
# Format: (base_path, (enable_full_path, disable_full_path))
# base_path is the parent path without enable/disable (e.g., "config invites")
# The tuple contains the full paths for enable and disable commands
ENABLE_DISABLE_PAIRS: list[tuple[str, tuple[str, str]]] = [
    # Configuration commands
    ("config invites", ("config invites enable", "config invites disable")),
    ("config reminders", ("config reminders enable", "config reminders disable")),
    ("config gdpr", ("config gdpr enable", "config gdpr disable")),
    ("config onboarding", ("config onboarding enable", "config onboarding disable")),
    ("config agents", ("config agents toggle",)),
    # Add more pairs here as needed
]


# Commands that are explicitly admin-only (by full path or name)
# These will be marked as admin even if other detection methods fail
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
}


# Commands that should be excluded from the command list
HIDDEN_COMMANDS: set[str] = set()


def get_category_for_cog(cog_name: str) -> str:
    """Get the friendly category name for a cog class name."""
    return COG_CATEGORY_MAP.get(cog_name, f"📦 {cog_name}")


def is_admin_command(command_name: str, full_path: str, has_checks: bool, 
                     default_permissions: Any = None,
                     description: str | None = None) -> bool:
    """
    Determine if a command is admin-only.
    
    Args:
        command_name: The command name (e.g., "enable")
        full_path: Full command path (e.g., "config invites enable")
        has_checks: Whether the command has permission checks
        default_permissions: Command's default_permissions attribute (discord.Permissions or None)
        description: Command description
        
    Returns:
        True if the command is admin-only, False otherwise
    """
    # Method 1: Check explicit admin commands list
    if command_name in ADMIN_COMMANDS or full_path in ADMIN_COMMANDS:
        return True
    
    # Method 2: Check if full path starts with admin command
    for admin_cmd in ADMIN_COMMANDS:
        if full_path.startswith(admin_cmd + " ") or full_path == admin_cmd:
            return True
    
    # Method 3: Check default_permissions
    if default_permissions is not None and hasattr(default_permissions, 'administrator'):
        if getattr(default_permissions, 'administrator', False):
            return True
    
    # Method 4: Check if command has checks (likely admin-only)
    if has_checks:
        return True
    
    # Method 5: Check description for admin keywords
    if description:
        desc_lower = description.lower()
        if "admin" in desc_lower or "owner" in desc_lower or "(admin" in desc_lower:
            return True
    
    # Method 6: Check command name for admin keywords
    cmd_name_lower = command_name.lower()
    admin_keywords = ["config", "clean", "sendto", "export", "migrate", "sync", "reload", "command_stats"]
    if any(keyword in cmd_name_lower for keyword in admin_keywords):
        return True
    
    return False


def find_enable_disable_pair(full_path: str, all_commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the matching enable/disable pair for a command.
    
    Args:
        full_path: Full command path (e.g., "config invites enable")
        all_commands: List of all command dictionaries with 'full_path' and 'name' keys
        
    Returns:
        The matching pair command dict, or None if no pair found
    """
    # Check if this is an enable or disable command
    is_enable = full_path.endswith(' enable') or full_path.split()[-1] == 'enable'
    is_disable = full_path.endswith(' disable') or full_path.split()[-1] == 'disable'
    
    if not (is_enable or is_disable):
        return None
    
    # Extract base path (everything except enable/disable)
    base_path = full_path.rsplit(' ', 1)[0] if ' ' in full_path else ''
    
    # First, try explicit mapping
    for pair_base, (enable_path, disable_path) in ENABLE_DISABLE_PAIRS:
        if base_path == pair_base:
            # Find the matching command
            target_path = disable_path if is_enable else enable_path
            for cmd in all_commands:
                if cmd.get('full_path') == target_path:
                    return cmd
            break
    
    # Fallback: Find by matching base path
    for cmd in all_commands:
        other_path = cmd.get('full_path', cmd.get('name', ''))
        other_name = cmd.get('name', '')
        
        # Check if it's the opposite type
        is_other_enable = other_path.endswith(' enable') or other_name == 'enable'
        is_other_disable = other_path.endswith(' disable') or other_name == 'disable'
        
        if (is_enable and is_other_disable) or (is_disable and is_other_enable):
            other_base = other_path.rsplit(' ', 1)[0] if ' ' in other_path else ''
            if base_path.lower() == other_base.lower():
                return cmd
    
    return None


def format_command_pair(enable_cmd: dict[str, Any], disable_cmd: dict[str, Any]) -> str:
    """
    Format an enable/disable command pair as a single line.
    
    Args:
        enable_cmd: Command dict for enable command
        disable_cmd: Command dict for disable command
        
    Returns:
        Formatted string for the command pair
    """
    enable_path = enable_cmd.get('full_path', enable_cmd.get('name', ''))
    disable_path = disable_cmd.get('full_path', disable_cmd.get('name', ''))
    
    # Clean up description
    desc = enable_cmd.get('description') or disable_cmd.get('description') or ''
    desc_lower = desc.lower()
    
    if desc_lower.startswith('enable/disable'):
        desc = desc[15:].strip()
    elif desc_lower.startswith('enable'):
        desc = desc[7:].strip()
    elif desc_lower.startswith('disable'):
        desc = desc[8:].strip()
    
    # Format paths
    enable_display = f"/{enable_path.replace(' ', ' ')}"
    disable_display = f"/{disable_path.replace(' ', ' ')}"
    
    return f"`{enable_display}` / `{disable_display}` — Enable/disable {desc[:50]}"
