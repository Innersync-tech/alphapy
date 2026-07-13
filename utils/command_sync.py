"""
Command Tree Sync Utility

Centralized command sync management with cooldown protection, rate limit handling,
and automatic detection of guild-only commands.

Features:
- Cooldown tracking (30 min per-guild, 60 min global)
- Automatic guild-only command detection
- Safe sync with error handling
- Rate limit protection
"""

import asyncio
import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from utils.logger import logger
from utils.operational_logs import EventType, log_operational_event

# Cooldown tracking: {key: last_sync_timestamp}
# Keys: "global" for global syncs, guild.id for per-guild syncs
_sync_cooldowns: dict[str, float] = {}

# Cooldown periods in seconds
GLOBAL_COOLDOWN = 60 * 60  # 60 minutes
GUILD_COOLDOWN = 30 * 60   # 30 minutes

# Max cooldown entries and cleanup
MAX_SYNC_COOLDOWNS = 500

# Track if we've done initial global sync
_initial_global_sync_done = False


@dataclass
class SyncResult:
    """Result of a sync operation"""
    success: bool
    command_count: int
    error: str | None = None
    cooldown_remaining: float | None = None
    sync_type: str = "unknown"  # "global" or "guild"


@dataclass
class GuildSyncBatchResult:
    """Aggregate result of syncing guild-only commands across all guilds."""

    synced_count: int
    skipped_count: int
    guild_count: int


def _get_cooldown_key(guild: discord.Guild | None) -> str:
    """Get cooldown tracking key for a sync operation"""
    if guild is None:
        return "global"
    return str(guild.id)


def _check_cooldown(guild: discord.Guild | None, force: bool = False) -> float | None:
    """
    Check if sync is on cooldown.
    
    Returns:
        None if not on cooldown, or seconds remaining if on cooldown
    """
    if force:
        return None
    
    key = _get_cooldown_key(guild)
    cooldown_period = GLOBAL_COOLDOWN if guild is None else GUILD_COOLDOWN
    
    if key not in _sync_cooldowns:
        return None
    
    last_sync = _sync_cooldowns[key]
    elapsed = time.time() - last_sync
    
    if elapsed < cooldown_period:
        remaining = cooldown_period - elapsed
        return remaining
    
    return None


def _update_cooldown(guild: discord.Guild | None) -> None:
    """Update cooldown timestamp after successful sync"""
    key = _get_cooldown_key(guild)
    _sync_cooldowns[key] = time.time()


def detect_guild_only_commands(bot: commands.Bot) -> bool:
    """
    Detect if the command tree contains any guild-only commands.
    
    Returns:
        True if any guild-only commands are found, False otherwise
    """
    try:
        for command in bot.tree.walk_commands():
            # Check if command or its parent group is guild-only
            if hasattr(command, 'guild_only') and command.guild_only:
                return True
            
            # Check parent groups
            parent = command.parent
            while parent is not None:
                if hasattr(parent, 'guild_only') and parent.guild_only:
                    return True
                parent = getattr(parent, 'parent', None)
        
        return False
    except Exception as e:
        logger.warning(f"Error detecting guild-only commands: {e}")
        return False


async def safe_sync(
    bot: commands.Bot,
    guild: discord.Guild | None = None,
    force: bool = False
) -> SyncResult:
    """
    Safely sync command tree with cooldown protection and error handling.
    
    Args:
        bot: Bot instance
        guild: Optional guild for per-guild sync. None for global sync.
        force: If True, bypass cooldown check
        
    Returns:
        SyncResult with sync status and details
    """
    sync_type = "guild" if guild else "global"
    start_time = time.time()
    
    # Check cooldown
    cooldown_remaining = _check_cooldown(guild, force)
    if cooldown_remaining is not None:
        minutes = int(cooldown_remaining / 60)
        seconds = int(cooldown_remaining % 60)
        message = f"Sync on cooldown. Wait {minutes}m {seconds}s before syncing again."
        logger.info(f"⏸️ {sync_type.capitalize()} sync skipped (cooldown): {message}")
        return SyncResult(
            success=False,
            command_count=0,
            error=message,
            cooldown_remaining=cooldown_remaining,
            sync_type=sync_type
        )
    
    # Perform sync
    try:
        if guild is None:
            # Global sync
            logger.info("🔄 Starting global command sync...")
            synced = await bot.tree.sync()
            command_count = len(synced)
            elapsed = time.time() - start_time
            logger.info(f"✅ Global sync completed: {command_count} commands synced in {elapsed:.2f}s")
            _update_cooldown(None)
            global _initial_global_sync_done
            _initial_global_sync_done = True
            return SyncResult(
                success=True,
                command_count=command_count,
                sync_type="global"
            )
        else:
            # Per-guild sync
            logger.info(f"🔄 Starting guild sync for {guild.name} (ID: {guild.id})...")
            # For guild-only commands, we don't need to copy global commands
            # Global commands are already available via global sync
            # Only sync the guild-specific command tree
            synced = await bot.tree.sync(guild=guild)
            command_count = len(synced)
            elapsed = time.time() - start_time
            logger.info(f"✅ Guild sync completed for {guild.name}: {command_count} commands synced in {elapsed:.2f}s")
            _update_cooldown(guild)
            return SyncResult(
                success=True,
                command_count=command_count,
                sync_type="guild"
            )
    
    except discord.HTTPException as e:
        elapsed = time.time() - start_time
        error_msg = f"Discord API error: {e}"
        
        # Check if it's a rate limit
        if e.status == 429:
            retry_after = getattr(e, 'retry_after', None)
            if retry_after:
                error_msg = f"Rate limited. Retry after {retry_after:.1f}s"
                # Update cooldown to retry_after time
                key = _get_cooldown_key(guild)
                _sync_cooldowns[key] = time.time() - (GUILD_COOLDOWN if guild else GLOBAL_COOLDOWN) + retry_after
        
        logger.error(f"❌ {sync_type.capitalize()} sync failed after {elapsed:.2f}s: {error_msg}")
        return SyncResult(
            success=False,
            command_count=0,
            error=error_msg,
            sync_type=sync_type
        )
    
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"❌ {sync_type.capitalize()} sync failed after {elapsed:.2f}s: {error_msg}", exc_info=True)
        return SyncResult(
            success=False,
            command_count=0,
            error=error_msg,
            sync_type=sync_type
        )


async def sync_guild_only_commands_for_all_guilds(
    bot: commands.Bot,
    *,
    sync_type: str = "post_connect",
    force: bool = False,
) -> GuildSyncBatchResult:
    """
    Sync guild-only slash commands for every guild the bot is in.

    Must run after the gateway connects (``on_ready``), when ``bot.guilds`` is populated.
    """
    guilds = list(bot.guilds)
    if not detect_guild_only_commands(bot):
        logger.debug("ℹ️ No guild-only commands detected")
        return GuildSyncBatchResult(0, 0, len(guilds))

    if not guilds:
        logger.info(
            "⏸️ Guild-only command sync deferred: bot.guilds is empty (sync_type=%s)",
            sync_type,
        )
        return GuildSyncBatchResult(0, 0, 0)

    logger.info(
        "🔄 Syncing guild-only commands for %s guild(s) (sync_type=%s)...",
        len(guilds),
        sync_type,
    )
    sync_tasks = [safe_sync(bot, guild=guild, force=force) for guild in guilds]
    results = await asyncio.gather(*sync_tasks, return_exceptions=True)

    synced_count = 0
    skipped_count = 0
    for guild, result in zip(guilds, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("  ⚠️ Sync error for %s: %s", guild.name, result)
            skipped_count += 1
            log_operational_event(
                EventType.GUILD_SYNC,
                f"Sync failed: {str(result)[:200]}",
                guild_id=guild.id,
                details={"error": str(result)[:500], "sync_type": sync_type},
            )
            continue
        if not isinstance(result, SyncResult):
            logger.warning("  ⚠️ Unexpected result type for %s: %s", guild.name, type(result))
            skipped_count += 1
            continue
        if result.success:
            synced_count += 1
            log_operational_event(
                EventType.GUILD_SYNC,
                f"Commands synced: {result.command_count} commands",
                guild_id=guild.id,
                details={"command_count": result.command_count, "sync_type": sync_type},
            )
            continue
        skipped_count += 1
        if result.cooldown_remaining:
            logger.debug("  ⏸️ Skipped sync for %s (cooldown)", guild.name)
            log_operational_event(
                EventType.GUILD_SYNC,
                "Sync skipped: cooldown active",
                guild_id=guild.id,
                details={"cooldown_remaining": result.cooldown_remaining, "sync_type": sync_type},
            )
        else:
            log_operational_event(
                EventType.GUILD_SYNC,
                f"Sync failed: {result.error}",
                guild_id=guild.id,
                details={"error": result.error, "sync_type": sync_type},
            )

    logger.info(
        "  ✅ Guild syncs completed: %s synced, %s skipped (%s guilds)",
        synced_count,
        skipped_count,
        len(guilds),
    )
    return GuildSyncBatchResult(synced_count, skipped_count, len(guilds))


def should_sync_global() -> bool:
    """
    Check if global sync should be performed.
    
    Returns:
        True if global sync hasn't been done yet, False otherwise
    """
    return not _initial_global_sync_done


def should_sync_guild(guild: discord.Guild) -> bool:
    """
    Check if guild sync should be performed.
    
    Args:
        guild: Guild to check
        
    Returns:
        True if guild has guild-only commands and sync is needed
    """
    # Always sync guild if we have guild-only commands
    # The cooldown check will prevent excessive syncs
    return True


def format_cooldown_message(remaining: float) -> str:
    """Format cooldown remaining time as human-readable message"""
    if remaining < 60:
        return f"{int(remaining)}s"
    elif remaining < 3600:
        minutes = int(remaining / 60)
        seconds = int(remaining % 60)
        return f"{minutes}m {seconds}s"
    else:
        hours = int(remaining / 3600)
        minutes = int((remaining % 3600) / 60)
        return f"{hours}h {minutes}m"


def cleanup_sync_cooldowns() -> None:
    """Remove expired cooldown entries and enforce max size."""
    global _sync_cooldowns
    current_time = time.time()
    initial_size = len(_sync_cooldowns)
    keys_to_remove = []
    
    for key, last_sync in list(_sync_cooldowns.items()):
        cooldown_period = GLOBAL_COOLDOWN if key == "global" else GUILD_COOLDOWN
        # Remove if expired (with 1 hour buffer)
        if current_time - last_sync > cooldown_period + 3600:
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del _sync_cooldowns[key]
    
    # Enforce max size
    if len(_sync_cooldowns) > MAX_SYNC_COOLDOWNS:
        sorted_by_age = sorted(_sync_cooldowns.items(), key=lambda x: x[1])
        excess = len(_sync_cooldowns) - MAX_SYNC_COOLDOWNS
        for key, _ in sorted_by_age[:excess]:
            del _sync_cooldowns[key]
    
    final_size = len(_sync_cooldowns)
    if initial_size != final_size or final_size > MAX_SYNC_COOLDOWNS * 0.8:
        logger.debug(f"Sync cooldowns cleanup: {initial_size} -> {final_size} active entries (max: {MAX_SYNC_COOLDOWNS})")
