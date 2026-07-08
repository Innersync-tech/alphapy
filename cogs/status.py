import asyncio
import os
from datetime import UTC, datetime
from typing import Any, cast

import aiohttp
import asyncpg
import discord
from asyncpg import exceptions as pg_exceptions
from discord import app_commands
from discord.ext import commands

import config
from utils.command_metadata import (
    HIDDEN_COMMANDS,
    find_enable_disable_pair,
    format_command_pair,
    get_category_for_command,
    is_admin_command,
)
from utils.db_helpers import acquire_safe, get_bot_db_pool, is_pool_healthy
from utils.embed_builder import EmbedBuilder
from utils.logger import get_gpt_status_logs, logger
from utils.timezone import BRUSSELS_TZ
from utils.validators import validate_admin
from version import CODENAME, __version__

# Database for command_stats (shared bot pool via interaction.client)
BOOT_TIME = datetime.now(BRUSSELS_TZ)

# ------------------ SLASH COMMAND ------------------ #

@app_commands.command(name="gptstatus", description="Check the status of the Grok/LLM API.")
async def gptstatus(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    embed = await get_gptstatus_embed(guild_id=guild_id, bot=interaction.client)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="version", description="Show bot version")
async def version_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Innersync • Alphapy version: v{__version__} — {CODENAME}", ephemeral=True
    )

@app_commands.command(name="innersync", description="Show Innersync info and official links")
async def innersync_cmd(interaction: discord.Interaction):
    embed = EmbedBuilder.info(
        title="🌱 Innersync",
        description="Innersync is the community and platform behind this bot. **Alphapy** is the Discord bot that powers reminders, onboarding, verification, and premium here."
    )
    
    embed.add_field(
        name="Core",
        value="[core.innersync.tech](https://core.innersync.tech)",
        inline=True
    )
    embed.add_field(
        name="App", 
        value="[app.innersync.tech](https://app.innersync.tech)",
        inline=True
    )
    embed.add_field(
        name="Pricing",
        value="[pricing.alphapy.innersync.tech](https://pricing.alphapy.innersync.tech)",
        inline=True
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="release", description="Show release notes for the current version")
async def release_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        notes, releases_url = await _get_release_notes(__version__)
        if not notes:
            await interaction.followup.send(f"No notes found for v{__version__}.")
            return
        footer_link = ""
        if releases_url:
            footer_link = f"\n\n*Read full release notes on [GitHub]({releases_url}).*"
        max_desc = 4096 - len(footer_link)
        description = _truncate_release_notes_md(notes, max_desc) + footer_link
        embed = EmbedBuilder.info(title=f"Release notes v{__version__}", description=description)
        embed.set_footer(text=f"{CODENAME}")
        await interaction.followup.send(embed=embed)
    except Exception:
        logger.exception("release_cmd failed")
        await interaction.followup.send("Failed to read release notes. Please try again later.")

@app_commands.command(name="health", description="Show configuration and system status")
async def health_cmd(interaction: discord.Interaction):
    embed = await _build_health_embed(interaction)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="help", description="Quick guide to the most useful commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Alphapy — quick help",
        description="Raw and direct. Here is what most members use.",
        color=discord.Color.dark_grey(),
    )
    embed.add_field(
        name="Get started",
        value=(
            "`/link` — connect Discord to your Innersync profile\n"
            "Complete onboarding via the server panel when you join"
        ),
        inline=False,
    )
    embed.add_field(
        name="Daily",
        value=(
            "`/growthcheckin` — goal, blocker, feeling → Grok responds\n"
            "`/add_reminder` — schedule one-off or recurring reminders\n"
            "`/agent start` — reflection agent (requires `/link`)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Support & premium",
        value=(
            "`/ticket` — open a support ticket\n"
            "`/premium` — plans, limits, checkout"
        ),
        inline=False,
    )
    embed.add_field(
        name="More",
        value="`/commands` — full command list · `/innersync` — platform links",
        inline=False,
    )
    embed.set_footer(text="docs.innersync.tech · /innersync for links")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="commands", description="List all available bot commands")
@app_commands.describe(
    include_admin="Include admin-only commands (default: False)",
    public="Post in channel instead of ephemeral (default: False)"
)
async def commands_list_cmd(
    interaction: discord.Interaction,
    include_admin: bool = False,
    public: bool = False
):
    """List all available bot commands in a nicely formatted embed."""
    await interaction.response.defer(ephemeral=not public)
    
    try:
        
        # Build a mapping of commands to their cogs
        command_to_cog = {}
        
        # Method 1: Walk through all cogs and their commands
        bot = cast(commands.Bot, interaction.client)
        for cog_name, cog in bot.cogs.items():
            # Get all app commands from this cog
            for attr_name in dir(cog):
                attr = getattr(cog, attr_name, None)
                if isinstance(attr, app_commands.Command):
                    command_to_cog[attr.name] = cog_name
                elif isinstance(attr, app_commands.Group):
                    # Also check subcommands in groups
                    for subcommand in attr.walk_commands():
                        if isinstance(subcommand, app_commands.Command):
                            command_to_cog[subcommand.name] = cog_name
        
        # Method 2: Walk through command tree and find cog via binding
        for command in bot.tree.walk_commands():
            if isinstance(command, app_commands.Command) and command.name not in command_to_cog:
                # Try to find the cog by checking command binding
                cog_name = None
                
                # Check if command has a direct cog reference
                if hasattr(command, 'binding') and command.binding:  # type: ignore
                    cog_name = command.binding.__class__.__name__  # type: ignore
                elif hasattr(command, 'cog') and command.cog:  # type: ignore
                    cog_name = command.cog.__class__.__name__  # type: ignore
                else:
                    # Check parent group for cog reference
                    if hasattr(command, 'parent') and command.parent:
                        parent = command.parent
                        if hasattr(parent, 'binding') and parent.binding:  # type: ignore
                            cog_name = parent.binding.__class__.__name__  # type: ignore
                        elif hasattr(parent, 'cog') and parent.cog:  # type: ignore
                            cog_name = parent.cog.__class__.__name__  # type: ignore
                
                if cog_name:
                    command_to_cog[command.name] = cog_name
        
        # Collect all commands grouped by category
        commands_by_category: dict[str, list[dict[str, Any]]] = {}
        admin_commands_by_category: dict[str, list[dict[str, Any]]] = {}
        
        # Walk through all commands in the tree (bot is already cast to commands.Bot above)
        for command in bot.tree.walk_commands():
            if isinstance(command, app_commands.Command):
                # Skip command groups (they're not executable commands)
                if isinstance(command, app_commands.Group):
                    continue
                
                # Skip hidden commands
                if command.name in HIDDEN_COMMANDS:
                    continue
                
                # Get full command path (including parent groups)
                full_path = command.name
                if hasattr(command, 'parent') and command.parent:
                    parent = command.parent
                    path_parts = [command.name]
                    while parent:
                        if hasattr(parent, 'name'):
                            path_parts.insert(0, parent.name)
                        # Get next parent
                        parent = getattr(parent, 'parent', None)
                    full_path = ' '.join(path_parts)
                
                # Skip if full path is hidden
                if full_path in HIDDEN_COMMANDS:
                    continue
                
                # Check if command is admin-only using centralized function
                default_perms = getattr(command, 'default_permissions', None)
                is_admin = is_admin_command(
                    command_name=command.name,
                    full_path=full_path,
                    has_checks=bool(command.checks),
                    default_permissions=default_perms,
                    description=command.description,
                    has_permission_checks=_command_has_permission_checks(command),
                )

                # Get category name using centralized function
                cog_name = command_to_cog.get(command.name, "Other")
                category = get_category_for_command(cog_name, full_path)
                
                cmd_info = {
                    "name": command.name,
                    "full_path": full_path,
                    "description": command.description or "No description",
                    "is_admin": is_admin
                }
                
                # Add to appropriate category
                if is_admin:
                    if category not in admin_commands_by_category:
                        admin_commands_by_category[category] = []
                    admin_commands_by_category[category].append(cmd_info)
                else:
                    if category not in commands_by_category:
                        commands_by_category[category] = []
                    commands_by_category[category].append(cmd_info)
        
        # Sort commands within each category
        for category in commands_by_category:
            commands_by_category[category].sort(key=lambda x: x["name"])
        for category in admin_commands_by_category:
            admin_commands_by_category[category].sort(key=lambda x: x["name"])
        
        # Build embed
        embed = EmbedBuilder.info(
            title="📋 Available Commands"
        )
        
        # Add public commands by category
        if commands_by_category:
            # Sort categories alphabetically
            sorted_categories = sorted(commands_by_category.keys())
            
            for category in sorted_categories:
                commands_list = commands_by_category[category]
                
                # Group enable/disable commands together using centralized function
                formatted_lines = []
                processed_commands = set()
                
                for cmd in commands_list:
                    cmd_name = cmd['name']
                    full_path = cmd.get('full_path', cmd_name)
                    
                    if cmd_name in processed_commands:
                        continue
                    
                    # Try to find enable/disable pair using centralized function
                    pair_cmd = find_enable_disable_pair(full_path, commands_list)
                    
                    if pair_cmd and pair_cmd['name'] not in processed_commands:
                        # Format as pair using centralized function
                        enable_cmd = cmd if full_path.endswith(' enable') or cmd_name == 'enable' else pair_cmd
                        disable_cmd = pair_cmd if enable_cmd == cmd else cmd
                        formatted_lines.append(format_command_pair(enable_cmd, disable_cmd))
                        processed_commands.add(cmd_name)
                        processed_commands.add(pair_cmd['name'])
                        continue
                    
                    # Regular command (not enable/disable pair)
                    cmd_display = f"/{full_path.replace(' ', ' ')}" if ' ' in full_path else f"/{cmd_name}"
                    formatted_lines.append(f"`{cmd_display}` — {cmd['description'][:70]}")
                    processed_commands.add(cmd_name)
                
                commands_text = "\n".join(formatted_lines)
                
                # Discord embed field value limit is 1024 characters
                if len(commands_text) > 1024:
                    # Split into multiple fields if needed
                    chunks = []
                    current_chunk = ""
                    for line in commands_text.split("\n"):
                        if len(current_chunk) + len(line) + 1 > 1024:
                            chunks.append(current_chunk)
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                    if current_chunk:
                        chunks.append(current_chunk)
                    
                    for idx, chunk in enumerate(chunks[:3]):  # Max 3 chunks per category
                        field_name = category if idx == 0 else f"{category} (continued)"
                        embed.add_field(
                            name=field_name,
                            value=chunk[:1024],
                            inline=False
                        )
                else:
                    embed.add_field(
                        name=category,
                        value=commands_text,
                        inline=False
                    )
        else:
            embed.add_field(
                name="📦 Commands",
                value="No public commands found.",
                inline=False
            )
        
        # Add admin commands by category if requested
        if include_admin and admin_commands_by_category:
            sorted_admin_categories = sorted(admin_commands_by_category.keys())
            
            for category in sorted_admin_categories:
                commands_list = admin_commands_by_category[category]
                
                # Group enable/disable commands together using centralized function
                formatted_lines = []
                processed_commands = set()
                
                for cmd in commands_list:
                    cmd_name = cmd['name']
                    full_path = cmd.get('full_path', cmd_name)
                    
                    if cmd_name in processed_commands:
                        continue
                    
                    # Try to find enable/disable pair using centralized function
                    pair_cmd = find_enable_disable_pair(full_path, commands_list)
                    
                    if pair_cmd and pair_cmd['name'] not in processed_commands:
                        # Format as pair using centralized function
                        enable_cmd = cmd if full_path.endswith(' enable') or cmd_name == 'enable' else pair_cmd
                        disable_cmd = pair_cmd if enable_cmd == cmd else cmd
                        formatted_lines.append(format_command_pair(enable_cmd, disable_cmd))
                        processed_commands.add(cmd_name)
                        processed_commands.add(pair_cmd['name'])
                        continue
                    
                    # Regular command (not enable/disable pair)
                    cmd_display = f"/{full_path.replace(' ', ' ')}" if ' ' in full_path else f"/{cmd_name}"
                    formatted_lines.append(f"`{cmd_display}` — {cmd['description'][:70]}")
                    processed_commands.add(cmd_name)
                
                admin_text = "\n".join(formatted_lines)
                
                if len(admin_text) > 1024:
                    admin_chunks = []
                    current_chunk = ""
                    for line in admin_text.split("\n"):
                        if len(current_chunk) + len(line) + 1 > 1024:
                            admin_chunks.append(current_chunk)
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                    if current_chunk:
                        admin_chunks.append(current_chunk)
                    
                    for idx, chunk in enumerate(admin_chunks[:3]):
                        field_name = f"🔐 {category}" if idx == 0 else f"🔐 {category} (continued)"
                        embed.add_field(
                            name=field_name,
                            value=chunk[:1024],
                            inline=False
                        )
                else:
                    embed.add_field(
                        name=f"🔐 {category}",
                        value=admin_text,
                        inline=False
                    )
        
        # Add summary
        total_public = sum(len(cmds) for cmds in commands_by_category.values())
        total_admin = sum(len(cmds) for cmds in admin_commands_by_category.values()) if include_admin else 0
        total_categories = len(commands_by_category)
        if include_admin:
            total_categories += len(admin_commands_by_category)
        
        summary = f"**{total_public}** public command{'s' if total_public != 1 else ''} in **{len(commands_by_category)}** categor{'ies' if len(commands_by_category) != 1 else 'y'}"
        if include_admin:
            summary += f"\n**{total_admin}** admin command{'s' if total_admin != 1 else ''} in **{len(admin_commands_by_category)}** categor{'ies' if len(admin_commands_by_category) != 1 else 'y'}"
        
        embed.add_field(
            name="📊 Summary",
            value=summary,
            inline=False
        )
        
        embed.set_footer(text=f"v{__version__} — {CODENAME}")
        
        await interaction.followup.send(embed=embed, ephemeral=not public)
        
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to retrieve commands list: {e}",
            ephemeral=not public
        )

@app_commands.command(name="command_stats", description="Show command usage statistics (admin only)")
@app_commands.describe(
    days="Number of days to look back (default: 7)",
    limit="Maximum number of commands to show (default: 10)",
    guild_only="Show stats for this server only (default: True)"
)
async def command_stats_cmd(
    interaction: discord.Interaction,
    days: int = 7,
    limit: int = 10,
    guild_only: bool = True
):
    """Show command usage statistics in a rich embed."""
    # Check admin permissions
    is_admin, error_msg = await validate_admin(interaction, raise_on_fail=False)
    if not is_admin:
        await interaction.response.send_message(
            error_msg or "❌ You don't have permission to use this command. Administrator access required.",
            ephemeral=True
        )
        return
    
    # If guild_only=False, only allow bot owners
    if not guild_only:
        if interaction.user.id not in config.OWNER_IDS:
            await interaction.response.send_message(
                "❌ Only bot owners can view stats for all servers. Use `guild_only: True` for server-specific stats.",
                ephemeral=True
            )
            return
    
    await interaction.response.defer(ephemeral=True)

    pool = get_bot_db_pool(interaction.client)
    if not is_pool_healthy(pool):
        await interaction.followup.send(
            "❌ Database not available.",
            ephemeral=True,
        )
        return

    try:
        async with acquire_safe(pool) as conn:
            # Build query
            where_clause = "WHERE created_at >= NOW() - ($1 || ' days')::INTERVAL"
            params: list[Any] = [str(days)]
            param_index = 2
            
            guild_id: int | None = None
            if guild_only and interaction.guild:
                guild_id = interaction.guild.id
                where_clause += f" AND guild_id = ${param_index}"
                params.append(guild_id)
                param_index += 1
            
            # Execute query for top commands
            limit_param_index = param_index
            query = f"""
                SELECT command_name, COUNT(*) as usage_count
                FROM audit_logs
                {where_clause}
                GROUP BY command_name
                ORDER BY usage_count DESC
                LIMIT ${limit_param_index}
            """
            rows = await conn.fetch(query, *params, limit)
            
            # Get total commands executed in period (same where clause, no limit)
            total_query = f"""
                SELECT COUNT(*) as total
                FROM audit_logs
                {where_clause}
            """
            total_rows = await conn.fetch(total_query, *params)
            total_commands = total_rows[0]["total"] if total_rows else 0
            
            # Build embed
            embed = EmbedBuilder.info(
                title="📊 Command Usage Statistics"
            )
            
            # Add period and scope info
            scope_text = f"This server ({interaction.guild.name})" if guild_only and interaction.guild else "All servers"
            embed.add_field(
                name="📅 Period",
                value=f"Last {days} day{'s' if days != 1 else ''}",
                inline=True
            )
            embed.add_field(
                name="🌐 Scope",
                value=scope_text,
                inline=True
            )
            embed.add_field(
                name="📈 Total Commands",
                value=f"{total_commands:,}",
                inline=True
            )
            
            # Add top commands
            if rows:
                commands_list = []
                for idx, row in enumerate(rows, 1):
                    command_name = row["command_name"]
                    usage_count = row["usage_count"]
                    commands_list.append(f"`{idx}.` **{command_name}** — {usage_count:,} uses")
                
                commands_text = "\n".join(commands_list)
                # Discord embed field value limit is 1024 characters
                if len(commands_text) > 1024:
                    commands_text = commands_text[:1021] + "..."
                
                embed.add_field(
                    name=f"🏆 Top {len(rows)} Commands",
                    value=commands_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏆 Top Commands",
                    value="No commands executed in this period.",
                    inline=False
                )
            
            embed.set_footer(text=f"v{__version__} — {CODENAME}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
    except pg_exceptions.UndefinedTableError:
        await interaction.followup.send(
            "❌ Command analytics not available. The `audit_logs` table has not been initialized yet.",
            ephemeral=True
        )
    except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError):
        await interaction.followup.send(
            "❌ Database connection error. Please try again in a moment.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to retrieve command statistics: {e}",
            ephemeral=True
        )

# ------------------ SETUP FUNCTION ------------------ #

async def setup(bot: commands.Bot):
    bot.tree.add_command(gptstatus)
    bot.tree.add_command(version_cmd)
    bot.tree.add_command(innersync_cmd)
    bot.tree.add_command(release_cmd)
    bot.tree.add_command(health_cmd)
    bot.tree.add_command(help_cmd)
    bot.tree.add_command(commands_list_cmd)
    bot.tree.add_command(command_stats_cmd)

# ------------------ HELPER FUNCTIONS ------------------ #

def _command_has_permission_checks(command: app_commands.Command) -> bool:
    """True when checks include admin/owner gates — not rate-limit cooldowns."""
    for check in command.checks:
        if hasattr(check, "rate") and hasattr(check, "per"):
            continue
        return True
    return False


def _format_timedelta(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(BRUSSELS_TZ) - ts
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s ago"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m ago" if mins else f"{hours}h ago"


def _derive_api_health(logs) -> str:
    """Return a health string derived from our own success/error logs."""
    if logs.last_success_time is None:
        return "❓ No activity this session"
    ts = logs.last_success_time
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age_minutes = (datetime.now(UTC) - ts).total_seconds() / 60
    if age_minutes <= 30:
        return "✅ Operational"
    if age_minutes <= 120:
        return f"⚠️ Last success {_format_timedelta(logs.last_success_time)} — may be degraded"
    return f"❌ No recent activity (last: {_format_timedelta(logs.last_success_time)})"


def _get_configured_model(bot, guild_id: int | None) -> str:
    """Read the currently configured Grok model from guild settings, falling back to logs then default."""
    try:
        settings = getattr(bot, "settings", None) if bot else None
        if settings:
            fetched = settings.get("gpt", "model", guild_id) if guild_id else settings.get("gpt", "model")
            if isinstance(fetched, str) and fetched.strip():
                return fetched.strip()
    except Exception:
        pass
    logs = get_gpt_status_logs()
    return logs.current_model or "grok-3"


async def get_gptstatus_embed(guild_id: int | None = None, bot=None):
    from gpt.helpers import _gpt_retry_queue
    logs = get_gpt_status_logs()

    health = _derive_api_health(logs)
    model = _get_configured_model(bot, guild_id)
    latency = logs.average_latency_ms or 0
    token_usage = logs.total_tokens_session or 0
    success_count = logs.success_count or 0
    error_count = logs.error_count or 0
    rate_limit_hits = logs.rate_limit_hits or 0
    retry_queue_size = len(_gpt_retry_queue)

    last_success_str = (
        _format_timedelta(logs.last_success_time) if logs.last_success_time else "—"
    )
    last_error_str = logs.last_error_type or "None"
    last_error_time_str = (
        _format_timedelta(logs.last_error_time) if logs.last_error_time else "—"
    )
    last_user_str = f"<@{logs.last_user}>" if logs.last_user else "—"

    if rate_limit_hits == 0:
        rate_limit_str = "✅ None this session"
    else:
        last_rl = _format_timedelta(logs.last_rate_limit_time) if logs.last_rate_limit_time else "—"
        rate_limit_str = f"⚠️ {rate_limit_hits}x (last: {last_rl})"

    retry_queue_str = f"{retry_queue_size} pending" if retry_queue_size > 0 else "—"

    embed = EmbedBuilder.status(title="🧠 Grok API Status")
    embed.add_field(name="Health", value=health, inline=False)
    embed.add_field(name="Model", value=f"`{model}`", inline=True)
    embed.add_field(name="Uptime", value=_format_uptime(BOOT_TIME), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
    embed.add_field(name="Last success", value=last_success_str, inline=True)
    embed.add_field(name="Avg latency", value=f"{latency} ms", inline=True)
    embed.add_field(name="Last user", value=last_user_str, inline=True)
    embed.add_field(name="Last error", value=last_error_str, inline=True)
    embed.add_field(name="Error time", value=last_error_time_str, inline=True)
    embed.add_field(name="Interactions", value=f"✅ {success_count}  ❌ {error_count}", inline=True)
    embed.add_field(name="Rate limits (session)", value=rate_limit_str, inline=True)
    embed.add_field(name="Retry queue", value=retry_queue_str, inline=True)
    embed.add_field(name="Tokens (session)", value=f"{token_usage:,}", inline=True)
    embed.set_footer(text=f"📦 Grok Status • v{__version__} — {CODENAME} • Updated just now")

    return embed


async def _build_health_embed(interaction: discord.Interaction) -> discord.Embed:
    bot = interaction.client
    settings = getattr(bot, "settings", None)

    reminders_enabled = invites_enabled = gdpr_enabled = "?"
    if settings:
        try:
            reminders_enabled = "✅" if settings.get("reminders", "enabled") else "🛑"
            invites_enabled = "✅" if settings.get("invites", "enabled") else "🛑"
            gdpr_enabled = "✅" if settings.get("gdpr", "enabled") else "🛑"
        except KeyError:
            pass

    db_ok = "✅"
    pool = get_bot_db_pool(bot)
    if is_pool_healthy(pool):
        try:
            async with acquire_safe(pool) as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            db_ok = "🛑"
    else:
        # Fallback: quick direct connection check (acceptable for health check)
        try:
            conn = await asyncio.wait_for(asyncpg.connect(config.DATABASE_URL), timeout=3)
        except Exception:
            db_ok = "🛑"
        else:
            await conn.close()

    embed = EmbedBuilder.success(title="🩺 Bot Health")
    embed.add_field(name="Database", value=db_ok, inline=True)
    embed.add_field(name="Reminders", value=reminders_enabled, inline=True)
    embed.add_field(name="Invites", value=invites_enabled, inline=True)
    embed.add_field(name="GDPR", value=gdpr_enabled, inline=True)
    embed.add_field(name="Uptime", value=_format_uptime(BOOT_TIME), inline=True)

    # System and cache metrics (optional)
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = process.cpu_percent(interval=None)
        embed.add_field(name="Memory", value=f"{memory_mb:.1f} MB", inline=True)
        embed.add_field(name="CPU", value=f"{cpu_percent:.1f}%", inline=True)
    except Exception:
        pass
    try:
        from utils.premium_guard import get_premium_cache_size
        cache_size = get_premium_cache_size()
        embed.add_field(name="Premium cache", value=f"{cache_size} entries (TTL 5 min)", inline=True)
    except Exception:
        pass

    return embed

def _drop_dangling_last_header(text: str) -> str:
    """If text ends with a bare '## ...' section header (no content after it), drop that header."""
    lines = text.split("\n")
    # Strip trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    last = lines[-1].strip()
    if last.startswith("## "):
        # Drop the header and any new trailing blanks
        lines = lines[:-1]
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines).rstrip()
    return "\n".join(lines).rstrip()


def _truncate_release_notes_md(text: str, max_len: int) -> str:
    """Truncate markdown to max_len by whole sections (##) or paragraphs, never mid-sentence."""
    if not text:
        return ""
    # Special-case: when GitHub body ends with a tiny "## Improved\nRead full release notes on GitHub."
    # section, drop that block so the command's own footer link is the only one shown.
    cleaned = text.rstrip()
    # Normalise level-3 headings to level-2 so both '## ' and '### ' work with the same logic.
    if cleaned.startswith("### "):
        cleaned = "## " + cleaned[4:]
    cleaned = cleaned.replace("\n### ", "\n## ")
    marker_header = "\n## Improved"
    marker_tail = "Read full release notes on GitHub."
    if cleaned.endswith(marker_tail):
        header_idx = cleaned.rfind(marker_header)
        if header_idx != -1:
            text = cleaned[:header_idx].rstrip()
        else:
            text = cleaned
    else:
        text = cleaned

    if len(text) <= max_len:
        # Even when not truncating for length, avoid ending on an empty '## ...' section.
        return _drop_dangling_last_header(text).strip()
    # Split by level-2 headers (## ) so we keep full sections
    parts = text.split("\n## ")
    if len(parts) == 1:
        # No ## headers: try paragraphs
        paras = text.split("\n\n")
        out: list[str] = []
        for p in paras:
            if len("\n\n".join(out) + ("\n\n" if out else "") + p) <= max_len:
                out.append(p)
            else:
                break
        if out:
            return "\n\n".join(out).strip()
        # Single paragraph or very long line: cut at last newline before limit
        if len(text) <= max_len:
            return text.strip()
        cut = text[: max_len + 1].rfind("\n")
        if cut > max_len // 2:
            return text[:cut].strip()
        return text[:max_len - 3].rstrip() + "..."
    # Rebuild sections: first part may be intro (no ## prefix), rest get "## " back
    built: list[str] = []
    for i, block in enumerate(parts):
        seg = ("## " + block).strip() if i > 0 else block.strip()
        if not seg:
            continue
        combined = "\n\n".join(built) + ("\n\n" + seg if built else seg)
        if len(combined) <= max_len:
            built.append(seg)
        else:
            break
    if not built:
        return text[: max_len - 3].rstrip() + "..."
    result = "\n\n".join(built).strip()
    # Also clean up any dangling final '## ...' header after truncation
    return _drop_dangling_last_header(result).strip()


async def _fetch_github_release_notes(repo: str, version: str) -> str | None:
    """Fetch release notes body from GitHub API for tag v{version}. Returns None on failure."""
    tag = f"v{version}" if not version.startswith("v") else version
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (data.get("body") or "").strip() or None
    except Exception as e:
        logger.debug("GitHub release fetch failed: %s", e)
        return None


async def _get_release_notes(version: str) -> tuple[str, str | None]:
    """Return (notes_text, releases_url). Uses GitHub if configured, else local changelog.md."""
    repo = getattr(config, "GITHUB_REPO", "").strip() or None
    releases_url = f"https://github.com/{repo}/releases/tag/v{version}" if repo else None
    if repo:
        logger.info("Release notes: fetching from GitHub repo=%s tag=v%s", repo, version)
        notes = await _fetch_github_release_notes(repo, version)
        if notes:
            logger.info("Release notes: using GitHub (v%s)", version)
            return notes, releases_url
        logger.info("Release notes: GitHub fetch failed or empty, falling back to local changelog")
    else:
        logger.info("Release notes: GITHUB_REPO not set, using local changelog")
    # Fallback: local changelog
    base = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(base, "changelog.md")
    local = await _read_release_notes(path, version)
    if local:
        logger.info("Release notes: using local changelog %s", path)
    return local or "", releases_url


async def _read_release_notes(changelog_path: str, version: str) -> str:
    try:
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(None, lambda: open(changelog_path, encoding="utf-8").read())
        # very simple parse: find header '## [version]' and capture until next '##'
        start_marker = f"## [{version}]"
        if start_marker not in content:
            return ""
        start = content.index(start_marker) + len(start_marker)
        rest = content[start:]
        end_idx = rest.find("\n## ")
        section = rest[:end_idx] if end_idx != -1 else rest
        return section.strip()
    except Exception:
        return ""

def _format_uptime(start_dt: datetime) -> str:
    delta = datetime.now(BRUSSELS_TZ) - start_dt
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)
