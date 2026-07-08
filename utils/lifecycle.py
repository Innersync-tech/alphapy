"""
Lifecycle Manager

Centralized startup and shutdown management for the Discord bot with phased initialization
and graceful shutdown. Ensures proper dependency ordering and complete resource cleanup.
"""

import asyncio
import time

from discord.ext import commands

import config
from utils.command_sync import SyncResult
from utils.db_helpers import close_all_pools
from utils.logger import logger
from utils.operational_logs import EventType, log_operational_event

# Track if this is the first startup
_first_startup = True

# Track if we saw a disconnect (on_disconnect fired). Used to distinguish actual reconnects
# from duplicate on_ready calls during initial connection.
_saw_disconnect = False


class StartupManager:
    """Manages phased bot startup with dependency tracking."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings_service = None
    
    @staticmethod
    def is_first_startup() -> bool:
        """Check if this is the first startup (not a reconnect)."""
        global _first_startup
        return _first_startup
    
    @staticmethod
    def _mark_startup_complete() -> None:
        """Mark that initial startup has completed."""
        global _first_startup
        _first_startup = False

    @staticmethod
    def set_disconnect_seen() -> None:
        """Call from on_disconnect. Marks that we saw a disconnect before the next on_ready."""
        global _saw_disconnect
        _saw_disconnect = True

    @staticmethod
    def consume_disconnect_seen() -> bool:
        """Returns True if we saw a disconnect since last check, and resets the flag.
        Used in on_ready to only run reconnect_phase after an actual disconnect."""
        global _saw_disconnect
        seen = _saw_disconnect
        _saw_disconnect = False
        return seen

    async def startup(self) -> None:
        """
        Main startup entry point - runs all phases sequentially.
        
        Phases:
        1. Database Infrastructure
        2. Settings Service
        3. Cog Loading
        4. Command Sync
        5. Background Tasks
        6. Ready
        """
        logger.info("🚀 Starting bot initialization...")
        
        try:
            await self._phase_database()
            await self._phase_settings()
            await self._phase_cogs()
            await self._phase_sync()
            await self._phase_background_tasks()
            await self._phase_ready()
            
            # NOTE: _mark_startup_complete() is called from on_ready() in bot.py, not here.
            # setup_hook runs before Discord connection; on_ready fires after. Marking complete
            # here would make is_first_startup() return False when on_ready() runs, causing
            # the first startup to be incorrectly treated as a reconnect.
            logger.info("✅ Bot initialization complete!")
        except Exception as e:
            logger.error(f"❌ Startup failed: {e}", exc_info=True)
            raise
    
    async def _phase_database(self) -> None:
        """Phase 1: Initialize database infrastructure."""
        logger.info("📊 Phase 1: Database Infrastructure...")
        
        # Database connectivity is verified when SettingsService creates its pool
        # No additional setup needed here
        logger.info("✅ Phase 1 complete: Database infrastructure ready")
    
    async def _phase_settings(self) -> None:
        """Phase 2: Initialize SettingsService."""
        logger.info("⚙️ Phase 2: Settings Service...")
        
        # SettingsService is created at module level in bot.py and passed via startup_manager.settings_service
        # If not provided, get it from bot instance
        if self.settings_service is None:
            self.settings_service = getattr(self.bot, "settings", None)
        
        # Run setup (creates DB pool and loads settings)
        if self.settings_service:
            await self.settings_service.setup()
            self.bot.settings = self.settings_service
        else:
            logger.warning("⚠️ SettingsService not available")
        
        logger.info("✅ Phase 2 complete: SettingsService initialized")
    
    async def _phase_cogs(self) -> None:
        """Phase 3: Load all cogs sequentially."""
        logger.info("🔌 Phase 3: Loading cogs...")
        
        cog_extensions = [
            "cogs.onboarding",
            "cogs.join_roles",
            "cogs.reaction_roles",
            "cogs.slash_utils",
            "cogs.dataquery",
            "cogs.reload_commands",
            "cogs.gdpr",
            "cogs.inviteboard",
            "cogs.clean",
            "cogs.lotquiz",
            "cogs.leadership",
            "cogs.status",
            "cogs.growth",
            "cogs.learn",
            "cogs.contentgen",
            "cogs.configuration",
            "cogs.premium",
            "cogs.reminders",
            "cogs.embed_watcher",
            "cogs.ticketbot",
            "cogs.faq",
            "cogs.exports",
            "cogs.delete_my_data",
            "cogs.innersync_identity",
            "cogs.migrations",
            "cogs.verification",
            "cogs.automod",
            "cogs.custom_commands",
            "cogs.retention_cleanup",
            "cogs.engagement",
            "cogs.agents",
        ]
        
        loaded: list[str] = []
        for extension in cog_extensions:
            try:
                await self.bot.load_extension(extension)
                loaded.append(extension)
            except Exception as e:
                logger.error(f"  Failed to load {extension}: {e}")
                # Continue loading other cogs even if one fails
        
        logger.info(f"✅ Phase 3 complete: Loaded {len(loaded)}/{len(cog_extensions)} cogs")
        if len(loaded) < len(cog_extensions):
            failed = [e for e in cog_extensions if e not in loaded]
            logger.warning(f"  Not loaded: {', '.join(failed)}")
    
    async def _phase_sync(self) -> None:
        """Phase 4: Sync command tree."""
        logger.info("🔄 Phase 4: Command Sync...")
        
        from utils.command_sync import detect_guild_only_commands, safe_sync, should_sync_global
        
        # Sync global commands once (if needed)
        if should_sync_global():
            global_result = await safe_sync(self.bot, guild=None, force=False)
            if global_result.success:
                logger.info(f"  ✅ Global commands synced: {global_result.command_count} commands")
            else:
                logger.warning(f"  ⚠️ Global sync skipped: {global_result.error}")
        else:
            logger.debug("  ⏸️ Global sync skipped (cooldown)")
        
        # Sync guild-only commands for existing guilds (parallel for speed)
        has_guild_only = detect_guild_only_commands(self.bot)
        if has_guild_only:
            logger.info("  🔄 Syncing guild-only commands for existing guilds...")
            # Run guild syncs in parallel for faster startup
            sync_tasks = [safe_sync(self.bot, guild=guild, force=False) for guild in self.bot.guilds]
            results = await asyncio.gather(*sync_tasks, return_exceptions=True)
            
            synced_count = 0
            skipped_count = 0
            for i, result in enumerate(results):
                guild = self.bot.guilds[i]
                if isinstance(result, Exception):
                    logger.error(f"  ❌ Sync error for {guild.name}: {result}")
                    skipped_count += 1
                    log_operational_event(
                        EventType.GUILD_SYNC,
                        f"Sync failed: {str(result)[:200]}",
                        guild_id=guild.id,
                        details={"error": str(result)[:500], "sync_type": "startup"}
                    )
                elif isinstance(result, SyncResult):
                    # Type narrowing: result is SyncResult here
                    if result.success:
                        synced_count += 1
                        log_operational_event(
                            EventType.GUILD_SYNC,
                            f"Commands synced: {result.command_count} commands",
                            guild_id=guild.id,
                            details={"command_count": result.command_count, "sync_type": "startup"}
                        )
                    else:
                        skipped_count += 1
                        if result.cooldown_remaining:
                            logger.debug(f"  ⏸️ Skipped sync for {guild.name} (cooldown)")
                            log_operational_event(
                                EventType.GUILD_SYNC,
                                "Sync skipped: cooldown active",
                                guild_id=guild.id,
                                details={"cooldown_remaining": result.cooldown_remaining, "sync_type": "startup"}
                            )
                        else:
                            log_operational_event(
                                EventType.GUILD_SYNC,
                                f"Sync failed: {result.error}",
                                guild_id=guild.id,
                                details={"error": result.error, "sync_type": "startup"}
                            )
                else:
                    # Unexpected type
                    logger.warning(f"  ⚠️ Unexpected result type for {guild.name}: {type(result)}")
                    skipped_count += 1
            logger.info(f"  ✅ Guild syncs completed: {synced_count} synced, {skipped_count} skipped")
        else:
            logger.debug("  ℹ️ No guild-only commands detected")
        
        logger.info("✅ Phase 4 complete: Command sync finished")
    
    async def _phase_background_tasks(self) -> None:
        """Phase 5: Start background tasks."""
        logger.info("🔄 Phase 5: Background Tasks...")
        
        # Optional: Verify Google Drive configuration
        await self._verify_drive_config()
        
        # Initialize command tracker with database pool in bot's event loop
        try:
            from utils.command_tracker import _db_pool, set_db_pool, start_flush_task
            from utils.db_helpers import create_db_pool
            
            # Only create new pool if we don't have one or it's closing
            if _db_pool is None or _db_pool.is_closing():
                database_url = getattr(config, "DATABASE_URL", None)
                if not database_url:
                    raise RuntimeError("DATABASE_URL is not set in config")
                command_tracker_pool = await create_db_pool(
                    database_url,
                    name="command_tracker",
                    min_size=1,
                    max_size=5,
                    command_timeout=10.0
                )
                set_db_pool(command_tracker_pool)
                logger.info("  ✅ Command tracker: Database pool initialized")
            else:
                logger.debug("  ℹ️ Command tracker: Database pool already initialized")
            
            # Start periodic flush task for command usage batching
            start_flush_task()
            logger.info("  ✅ Command tracker flush task started")
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to initialize command tracker pool: {e}")
        
        # Start Grok retry queue task
        try:
            import gpt.helpers as gpt_helpers
            from gpt.helpers import _retry_gpt_requests
            if gpt_helpers._retry_task is None or gpt_helpers._retry_task.done():
                gpt_helpers._retry_task = asyncio.create_task(_retry_gpt_requests())
                logger.info("  ✅ Grok retry queue task started")
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to start Grok retry task: {e}")
        
        # Start sync cooldowns cleanup task
        try:
            from utils.background_tasks import BackgroundTask
            from utils.command_sync import cleanup_sync_cooldowns
            
            async def cleanup_sync_cooldowns_async():
                cleanup_sync_cooldowns()
            
            if not hasattr(self.bot, '_sync_cooldown_cleanup_task'):
                cleanup_task = BackgroundTask(
                    name="Sync Cooldowns Cleanup",
                    interval=600,  # 10 minutes
                    task_func=cleanup_sync_cooldowns_async
                )
                self.bot._sync_cooldown_cleanup_task = cleanup_task
                await cleanup_task.start()
                logger.info("  ✅ Sync cooldowns cleanup task started")
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to start sync cooldowns cleanup task: {e}")
        
        logger.info("✅ Phase 5 complete: Background tasks started")
    
    async def _verify_drive_config(self) -> None:
        """Optional: Verify Google Drive configuration during startup."""
        try:
            import config
            from utils.drive_sync import _ensure_drive

            if config.GOOGLE_CREDENTIALS_JSON:
                logger.info("🔍 Verifying Google Drive configuration...")
                drive_client = await asyncio.to_thread(_ensure_drive)
                if drive_client:
                    logger.info("✅ Google Drive configuration verified and ready")
                else:
                    logger.warning("⚠️ Google Drive configuration found but initialization failed (check logs above)")
            else:
                logger.debug("ℹ️ Google Drive not configured (GOOGLE_CREDENTIALS_JSON not set)")
        except Exception as e:
            logger.debug(f"ℹ️ Google Drive verification skipped: {e}")
    
    async def _phase_ready(self) -> None:
        """Phase 6: Mark bot as ready."""
        logger.info("✅ Phase 6: Ready...")
        
        # Set start_time for uptime tracking
        if not hasattr(self.bot, "start_time"):
            self.bot.start_time = time.time()
        
        # Add GDPR view
        from cogs.gdpr import GDPRView
        self.bot.add_view(GDPRView(self.bot))
        
        logger.info(f"  ✅ {self.bot.user} is ready! Intents actief: {self.bot.intents}")
        
        # Guilds may not be fully loaded yet at this point (they load after connect)
        # Log what we have, but don't worry if it's 0 - they'll be available after connect
        guild_count = len(self.bot.guilds)
        if guild_count > 0:
            logger.info(f"  📡 Known guilds: {guild_count}")
            for guild in self.bot.guilds:
                logger.info(f"    🔹 {guild.name} (ID: {guild.id})")
        else:
            logger.debug("  📡 Guilds will be loaded after Discord connection (currently 0)")
        
        # Log shard info (None is normal for single-shard bots)
        shard_id = getattr(self.bot, 'shard_id', None)
        if shard_id is not None:
            logger.info(f"  🔀 Shard ID: {shard_id}")
        else:
            logger.debug("  🔀 Single-shard bot (no shard ID)")
        
        logger.info("✅ Phase 6 complete: Bot is ready")
    
    async def reconnect_phase(self, bot: commands.Bot) -> None:
        """
        Light resync phase for reconnects.
        
        After a disconnect/reconnect, commands may not be available until synced again.
        This phase ensures commands are synced so they work immediately after reconnect.
        """
        logger.info("🔄 Reconnect phase: Resyncing commands...")
        logger.info("  😄 haha bot dropped the call, morgen lachen we er weer mee")
        
        from utils.command_sync import detect_guild_only_commands, safe_sync
        
        # After a disconnect, commands may not be available until synced
        # Sync global commands first (if needed and not on cooldown)
        logger.info("  🔄 Checking if global commands need resync...")
        global_result = await safe_sync(bot, guild=None, force=False)
        if global_result.success:
            logger.info(f"  ✅ Global commands resynced: {global_result.command_count} commands")
        elif global_result.cooldown_remaining:
            logger.debug(f"  ⏸️ Global sync on cooldown (wait {global_result.cooldown_remaining:.0f}s)")
        else:
            logger.warning(f"  ⚠️ Global sync failed: {global_result.error}")
        
        # Sync guild-only commands for all guilds (if we have them)
        synced_count = 0
        skipped_count = 0
        has_guild_only = detect_guild_only_commands(bot)
        if has_guild_only:
            logger.info(f"  🔄 Resyncing guild-only commands for {len(bot.guilds)} guilds...")
            sync_tasks = [safe_sync(bot, guild=guild, force=False) for guild in bot.guilds]
            results = await asyncio.gather(*sync_tasks, return_exceptions=True)

            for i, result in enumerate(results):
                guild = bot.guilds[i]
                if isinstance(result, Exception):
                    logger.warning(f"  ⚠️ Sync error for {guild.name}: {result}")
                    skipped_count += 1
                    log_operational_event(
                        EventType.GUILD_SYNC,
                        f"Sync failed: {str(result)[:200]}",
                        guild_id=guild.id,
                        details={"error": str(result)[:500], "sync_type": "reconnect"}
                    )
                elif isinstance(result, SyncResult):
                    # Type narrowing: result is SyncResult here
                    if result.success:
                        synced_count += 1
                        log_operational_event(
                            EventType.GUILD_SYNC,
                            f"Commands synced: {result.command_count} commands",
                            guild_id=guild.id,
                            details={"command_count": result.command_count, "sync_type": "reconnect"}
                        )
                    else:
                        skipped_count += 1
                        if result.cooldown_remaining:
                            logger.debug(f"  ⏸️ Skipped sync for {guild.name} (cooldown)")
                            log_operational_event(
                                EventType.GUILD_SYNC,
                                "Sync skipped: cooldown active",
                                guild_id=guild.id,
                                details={"cooldown_remaining": result.cooldown_remaining, "sync_type": "reconnect"}
                            )
                        else:
                            log_operational_event(
                                EventType.GUILD_SYNC,
                                f"Sync failed: {result.error}",
                                guild_id=guild.id,
                                details={"error": result.error, "sync_type": "reconnect"}
                            )
                else:
                    # Unexpected type
                    logger.warning(f"  ⚠️ Unexpected result type for {guild.name}: {type(result)}")
                    skipped_count += 1

            logger.info(f"  ✅ Guild syncs completed: {synced_count} synced, {skipped_count} skipped")
        else:
            logger.debug("  ℹ️ No guild-only commands detected")

        logger.info("✅ Reconnect phase complete: Commands should be available now")

        log_operational_event(
            EventType.BOT_RECONNECT,
            "Reconnect phase complete: commands synced",
            guild_id=None,
            details={"synced": synced_count, "skipped": skipped_count},
        )


class ShutdownManager:
    """Manages graceful bot shutdown with proper cleanup order."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    async def shutdown(self) -> None:
        """
        Main shutdown entry point - runs all phases in reverse order.
        
        Phases:
        1. Cancel Background Tasks
        2. Unload Cogs
        3. Close Shared Resources
        4. Final Cleanup
        """
        logger.info("🛑 Starting bot shutdown...")
        
        try:
            await self._phase_cancel_tasks()
            await self._phase_unload_cogs()
            await self._phase_close_pools()
            await self._phase_final_cleanup()
            
            logger.info("✅ Bot shutdown complete!")
        except Exception as e:
            logger.error(f"❌ Shutdown error: {e}", exc_info=True)
    
    async def _phase_cancel_tasks(self) -> None:
        """Phase 1: Cancel all background tasks."""
        logger.info("🛑 Phase 1: Cancelling background tasks...")
        
        # Stop command tracker flush task
        try:
            from utils.command_tracker import stop_flush_task
            stop_flush_task()
            logger.info("  ✅ Command tracker flush task stopped")
        except Exception as e:
            logger.debug(f"  ⚠️ Error stopping command tracker: {e}")
        
        # Cancel Grok retry task
        try:
            import gpt.helpers as gpt_helpers
            if gpt_helpers._retry_task and not gpt_helpers._retry_task.done():
                gpt_helpers._retry_task.cancel()
                try:
                    await asyncio.wait_for(gpt_helpers._retry_task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
                logger.info("  ✅ Grok retry task cancelled")
        except Exception as e:
            logger.debug(f"  ⚠️ Error cancelling Grok retry task: {e}")
        
        # Stop sync cooldowns cleanup task
        try:
            cleanup_task = getattr(self.bot, '_sync_cooldown_cleanup_task', None)
            if cleanup_task:
                await cleanup_task.stop()
                logger.info("  ✅ Sync cooldowns cleanup task stopped")
        except Exception as e:
            logger.debug(f"  ⚠️ Error stopping sync cooldowns cleanup: {e}")
        
        logger.info("✅ Phase 1 complete: Background tasks cancelled")
    
    async def _phase_unload_cogs(self) -> None:
        """Phase 2: Unload all cogs in reverse order."""
        logger.info("🔌 Phase 2: Unloading cogs...")
        
        # Get list of loaded extensions in reverse order
        extensions = list(self.bot.extensions.keys())
        extensions.reverse()  # Unload in reverse order
        
        unloaded_count = 0
        for extension in extensions:
            try:
                await self.bot.unload_extension(extension)
                unloaded_count += 1
                logger.debug(f"  ✅ Unloaded {extension}")
            except Exception as e:
                logger.warning(f"  ⚠️ Failed to unload {extension}: {e}")
        
        logger.info(f"✅ Phase 2 complete: Unloaded {unloaded_count}/{len(extensions)} cogs")
    
    async def _phase_close_pools(self) -> None:
        """Phase 3: Close all database pools."""
        logger.info("🔌 Phase 3: Closing database pools...")
        
        # Close SettingsService pool
        try:
            settings = getattr(self.bot, "settings", None)
            if settings and hasattr(settings, "_pool") and settings._pool:
                await settings._pool.close()
                logger.info("  ✅ SettingsService pool closed")
        except Exception as e:
            logger.debug(f"  ⚠️ Error closing SettingsService pool: {e}")
        
        # Close command tracker pool
        try:
            from utils.command_tracker import _db_pool
            if _db_pool and not _db_pool.is_closing():
                await _db_pool.close()
                logger.info("  ✅ Command tracker pool closed")
        except Exception as e:
            logger.debug(f"  ⚠️ Error closing command tracker pool: {e}")
        
        # Close all registered pools (from db_helpers)
        try:
            await close_all_pools()
            logger.info("  ✅ All registered pools closed")
        except Exception as e:
            logger.debug(f"  ⚠️ Error closing registered pools: {e}")
        
        logger.info("✅ Phase 3 complete: Database pools closed")
    
    async def _phase_final_cleanup(self) -> None:
        """Phase 4: Final cleanup."""
        logger.info("🧹 Phase 4: Final cleanup...")

        # Close shared HTTP client (premium guard Core-API calls)
        try:
            from utils.premium_guard import close_http_client
            await close_http_client()
            logger.info("  ✅ Premium guard HTTP client closed")
        except Exception as e:
            logger.debug(f"  ⚠️ Error closing premium guard HTTP client: {e}")

        logger.info("✅ Phase 4 complete: Final cleanup done")
