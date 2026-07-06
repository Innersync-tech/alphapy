"""
Auto-Moderation System for Alphapy

Provides automated content moderation with configurable rules and actions.
Integrates with premium system for advanced features.
"""

import time
from datetime import timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.automod_logging import AutoModLogger
from utils.automod_rules import ActionType, RuleProcessor
from utils.cog_base import AlphaCog
from utils.db_helpers import acquire_safe
from utils.logger import logger
from utils.operational_logs import EventType, log_operational_event
from utils.validators import validate_admin


def requires_admin():
    """Check decorator for admin permissions."""
    async def predicate(interaction: discord.Interaction) -> bool:
        is_admin, _ = await validate_admin(interaction, raise_on_fail=False)
        if is_admin:
            return True
        raise app_commands.CheckFailure("You need administrator permissions for this command.")
    return app_commands.check(predicate)


class AutoModeration(AlphaCog):
    """Main auto-moderation cog with rule processing and enforcement."""

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.rule_processor = RuleProcessor(bot)
        self.mod_logger = AutoModLogger(bot)
        self._spam_tracker: dict[int, dict[int, list[float]]] = {}  # guild_id -> user_id -> timestamps
        self._message_cache: dict[int, dict[int, str]] = {}  # guild_id -> user_id -> last_message
        
    async def cog_load(self):
        """Initialize the auto-mod system."""
        logger.info("Loading AutoModeration cog...")
        # Don't load rules immediately - wait for database pool to be ready
        # Rules will be loaded on first use via get_active_rules method
        
    async def cog_unload(self):
        """Cleanup when cog is unloaded."""
        try:
            # Flush any buffered log entries before shutdown
            await self.mod_logger.flush_logs()
            logger.info("AutoModeration cog unloaded - log buffer flushed")
        except Exception as e:
            logger.error(f"Error flushing logs during cog unload: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle incoming messages for auto-moderation."""
        # Skip bot messages
        if message.author.bot:
            return
            
        # Skip DMs
        if not message.guild:
            return
            
        guild_id = message.guild.id
            
        # Check if auto-mod is enabled for this guild
        automod_enabled = self.settings.get("automod", "enabled", guild_id=guild_id)
        if not automod_enabled:
            return
            
        # Skip if user has administrator permissions
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
            return
            
        try:
            await self._process_message(message)
        except Exception as e:
            logger.error(f"Error in auto-mod processing for message {message.id}: {e}")
            
    async def _process_message(self, message: discord.Message):
        """Process a single message through all active rules."""
        if not message.guild:
            return
            
        guild_id = message.guild.id
        user_id = message.author.id
        
        # Get active rules for this guild
        rules = await self.rule_processor.get_active_rules(guild_id)
        if not rules:
            return
            
        # Update spam tracking
        self._update_spam_tracker(guild_id, user_id, time.time())

        # Process each rule
        for rule in rules:
            try:
                result = await self.rule_processor.evaluate_rule(rule, message, self._get_user_context(guild_id, user_id))
                if result.triggered:
                    await self._handle_violation(message, rule, result)
                    break  # Stop after first violation to avoid multiple actions
            except Exception as e:
                logger.error(f"Error evaluating rule {rule.get('id', 'unknown')}: {e}")

        # Update message cache for duplicate detection after evaluation,
        # so user context reflects the previous message when rules run.
        if guild_id not in self._message_cache:
            self._message_cache[guild_id] = {}
        self._message_cache[guild_id][user_id] = message.content
                
    def _update_spam_tracker(self, guild_id: int, user_id: int, timestamp: float):
        """Update spam tracking data for a user."""
        if guild_id not in self._spam_tracker:
            self._spam_tracker[guild_id] = {}
            
        if user_id not in self._spam_tracker[guild_id]:
            self._spam_tracker[guild_id][user_id] = []
            
        # Add current timestamp and clean old ones (older than 5 minutes)
        self._spam_tracker[guild_id][user_id].append(timestamp)
        self._spam_tracker[guild_id][user_id] = [
            ts for ts in self._spam_tracker[guild_id][user_id] 
            if timestamp - ts < 300  # 5 minutes
        ]
        
    def _get_user_context(self, guild_id: int, user_id: int) -> dict[str, Any]:
        """Get context data for a user in a guild."""
        return {
            'spam_count': len(self._spam_tracker.get(guild_id, {}).get(user_id, [])),
            'last_message': self._message_cache.get(guild_id, {}).get(user_id, ''),
            'message_timestamps': self._spam_tracker.get(guild_id, {}).get(user_id, [])
        }
        
    async def _handle_violation(self, message: discord.Message, rule: dict[str, Any], result):
        """Handle a rule violation by executing the configured action."""
        if not message.guild:
            return
            
        guild_id = message.guild.id
        user_id = message.author.id
        
        # Log the violation
        await self.mod_logger.log_violation(
            guild_id=guild_id,
            user_id=user_id,
            message_id=message.id,
            channel_id=message.channel.id,
            rule_id=rule.get('id') or 0,  # Default to 0 if None
            rule_name=rule.get('name'),
            action_type=rule.get('action_type') or 'unknown',  # Default to 'unknown' if None
            message_content=message.content,
            context=result.context
        )
        
        # Log operational event for auto-mod action
        log_operational_event(
            EventType.SETTINGS_CHANGED,
            f"Auto-mod violation: {rule.get('action_type', 'unknown')} action taken",
            guild_id=guild_id,
            details={
                'user_id': user_id,
                'rule_id': rule.get('id'),
                'rule_type': rule.get('rule_type'),
                'action_type': rule.get('action_type'),
                'channel_id': message.channel.id,
                'message_id': message.id
            }
        )
        
        # Execute the action
        action_config = rule.get('action_config', {})
        action_type = rule.get('action_type')
        
        if action_type == ActionType.DELETE.value:
            await self._action_delete_message(message)
        elif action_type == ActionType.WARN.value:
            await self._action_warn_user(message, action_config)
        elif action_type == ActionType.MUTE.value:
            await self._action_mute_user(message, action_config)
        elif action_type == ActionType.TIMEOUT.value:
            await self._action_timeout_user(message, action_config)
        elif action_type == ActionType.BAN.value:
            await self._action_ban_user(message, action_config)
        else:
            logger.warning(
                f"Unknown auto-mod action type '{action_type}' "
                f"for rule {rule.get('id')}"
            )
            
        # Update user history
        await self._update_user_history(guild_id, user_id, rule.get('rule_type'))
        
    async def _action_delete_message(self, message: discord.Message):
        """Delete the offending message."""
        try:
            await message.delete()
        except discord.Forbidden:
            logger.warning(f"Cannot delete message {message.id} - missing permissions")
        except Exception as e:
            logger.error(f"Error deleting message {message.id}: {e}")
            
    async def _action_warn_user(self, message: discord.Message, config: dict):
        """Send a warning to the user."""
        try:
            warning_message = None
            if message.guild:
                warning_message = self.settings.get(
                    "automod", "warn_message", guild_id=message.guild.id
                )
            if not warning_message:
                warning_message = config.get(
                    'message', "⚠️ Your message has been flagged for violating server rules."
                )
            
            # Send DM to user
            try:
                await message.author.send(warning_message)
            except discord.Forbidden:
                # Can't send DM, send in channel instead
                await message.channel.send(
                    f"{message.author.mention} {warning_message}",
                    delete_after=10
                )
                
        except Exception as e:
            logger.error(f"Error sending warning to user {message.author.id}: {e}")
            
    async def _action_mute_user(self, message: discord.Message, config: dict):
        """Mute the user (requires configured mute role)."""
        try:
            guild = message.guild
            if not guild:
                logger.warning("Guild is None in _action_mute_user")
                return
                
            mute_role_id = config.get('mute_role_id')
            if not mute_role_id:
                logger.warning(f"No mute role configured for guild {guild.id}")
                return
                
            mute_role = guild.get_role(mute_role_id)
            if not mute_role:
                logger.warning(f"Mute role {mute_role_id} not found in guild {guild.id}")
                return
                
            member = message.author
            if isinstance(member, discord.Member):
                await member.add_roles(mute_role, reason="Auto-moderation mute")
            else:
                logger.warning(f"Cannot mute user {member.id} - not a guild member")
                
        except discord.Forbidden:
            logger.warning(f"Cannot mute user {message.author.id} - missing permissions")
        except Exception as e:
            logger.error(f"Error muting user {message.author.id}: {e}")
            
    async def _action_timeout_user(self, message: discord.Message, config: dict):
        """Timeout the user for a specified duration."""
        try:
            duration_minutes = config.get('duration_minutes', 10)
            duration = timedelta(minutes=duration_minutes)
            
            member = message.author
            if isinstance(member, discord.Member):
                await member.timeout(duration, reason="Auto-moderation timeout")
            else:
                logger.warning(f"Cannot timeout user {member.id} - not a guild member")
                
        except discord.Forbidden:
            logger.warning(f"Cannot timeout user {message.author.id} - missing permissions")
        except Exception as e:
            logger.error(f"Error timing out user {message.author.id}: {e}")
            
    async def _action_ban_user(self, message: discord.Message, config: dict):
        """Ban the user from the guild."""
        try:
            guild = message.guild
            if not guild:
                logger.warning("Guild is None in _action_ban_user")
                return
            
            member = message.author
            if not isinstance(member, discord.Member):
                logger.warning(f"Cannot ban user {member.id} - not a guild member")
                return
            
            # Optional configuration
            delete_message_seconds = config.get('delete_message_seconds')
            reason = config.get('reason', 'Auto-moderation ban')
            
            ban_kwargs: dict[str, Any] = {'reason': reason}
            if isinstance(delete_message_seconds, int) and delete_message_seconds > 0:
                ban_kwargs['delete_message_seconds'] = delete_message_seconds
            
            await guild.ban(member, **ban_kwargs)
        except discord.Forbidden:
            logger.warning(f"Cannot ban user {message.author.id} - missing permissions")
        except Exception as e:
            logger.error(f"Error banning user {message.author.id}: {e}")
            
    async def _update_user_history(self, guild_id: int, user_id: int, rule_type: str | None):
        """Update user's violation history."""
        try:
            from utils.db_helpers import get_bot_db_pool
            pool = get_bot_db_pool(self.bot)
            if not pool:
                return
                
            async with acquire_safe(pool) as conn:
                await conn.execute("""
                    INSERT INTO automod_user_history (guild_id, user_id, rule_type, violation_count, last_violation)
                    VALUES ($1, $2, $3, 1, NOW())
                    ON CONFLICT (guild_id, user_id, rule_type) 
                    DO UPDATE SET 
                        violation_count = automod_user_history.violation_count + 1,
                        last_violation = NOW(),
                        updated_at = NOW()
                """, guild_id, user_id, rule_type or 'unknown')
                
        except Exception as e:
            logger.error(f"Error updating user history: {e}")
            
    # automod status command lives in cogs/configuration.py under automod_group


async def setup(bot: commands.Bot):
    """Setup the AutoModeration cog."""
    await bot.add_cog(AutoModeration(bot))


