"""
Auto-Moderation Logging System

Provides comprehensive logging for auto-mod actions, violations, and statistics.
"""

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg
import discord
from discord.ext import commands

from utils.db_helpers import acquire_safe, get_bot_db_pool
from utils.sanitizer import safe_embed_text

log = logging.getLogger(__name__)


class AutoModLogger:
    """Specialized logging system for auto-moderation events."""
    
    def __init__(self, bot: commands.Bot | None = None):
        self.bot = bot
        self._buffer: list[dict] = []
        self._buffer_size = 50
        self._last_flush = 0
    
    def _get_pool(self) -> asyncpg.Pool | None:
        if not self.bot:
            return None
        pool = get_bot_db_pool(self.bot)
        return pool
        
    async def log_violation(self, guild_id: int, user_id: int, message_id: int | None,
                           channel_id: int | None, rule_id: int, action_type: str,
                           message_content: str | None = None, ai_analysis: dict | None = None,
                           context: dict | None = None, rule_name: str | None = None):
        """Log a rule violation and action taken."""
        try:
            log_entry = {
                'guild_id': guild_id,
                'user_id': user_id,
                'message_id': message_id,
                'channel_id': channel_id,
                'rule_id': rule_id,
                'action_taken': action_type,
                'message_content': message_content[:1000] if message_content else None,  # Limit length
                'ai_analysis': ai_analysis,
                'context': context,
                'timestamp': datetime.utcnow()
            }

            pool = self._get_pool()
            if pool:
                # Try to flush any buffered entries first
                await self._flush_buffer()
                # Then insert current entry directly
                await self._insert_log_entries([log_entry], pool)
            else:
                # Add to buffer for later processing if pool is not ready yet
                self._buffer.append(log_entry)
                log.warning(f"Auto-mod log buffered (pool unavailable): Guild {guild_id}, User {user_id}")
                
            # Also log to standard logger for immediate visibility
            log.info(f"Auto-mod violation: Guild {guild_id}, User {user_id}, Action: {action_type}, Rule: {rule_id}")
            
            # Log to Discord channel if configured
            await self._log_to_discord_channel(
                guild_id, user_id, action_type, rule_id, message_content, channel_id, rule_name
            )
            
        except Exception as e:
            log.error(f"Error logging auto-mod violation: {e}")
    
    async def _log_to_discord_channel(self, guild_id: int, user_id: int, action_type: str, rule_id: int, 
                                    message_content: str | None, channel_id: int | None,
                                    rule_name: str | None = None):
        """Log auto-mod violation to Discord log channel."""
        try:
            if not self.bot:
                return
                
            # Get log channel from settings
            settings = getattr(self.bot, "settings", None)
            if not settings:
                return
                
            log_channel_id = settings.get("automod", "log_channel_id", guild_id)
            if not log_channel_id or log_channel_id == 0:
                return
                
            log_channel = self.bot.get_channel(log_channel_id)
            if not isinstance(log_channel, (discord.TextChannel, discord.Thread)):
                return
                
            # Create embed for log entry
            embed = discord.Embed(
                title="⚠️ Auto-Moderation Violation",
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            
            # Get user info
            user = self.bot.get_user(user_id)
            user_mention = user.mention if user else f"<@{user_id}>"
            
            # Get channel info
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                channel_mention = channel.mention
            else:
                channel_mention = "Unknown"
            
            embed.add_field(name="User", value=user_mention, inline=True)
            embed.add_field(name="Action", value=action_type.upper(), inline=True)
            rule_display = safe_embed_text(rule_name, 100) if rule_name else f"Rule #{rule_id}"
            embed.add_field(name="Rule", value=rule_display, inline=True)
            embed.add_field(name="Channel", value=channel_mention, inline=True)
            
            if message_content:
                content = safe_embed_text(message_content, 200)
                embed.add_field(name="Message Content", value=f"```{content}```", inline=False)
                
            embed.set_footer(text=f"Guild ID: {guild_id} · db #{rule_id}")
            
            await log_channel.send(embed=embed)
            
        except Exception as e:
            log.error(f"Error logging to Discord channel: {e}")
            
    async def _flush_buffer(self):
        """Flush the log buffer to database."""
        if not self._buffer:
            return
            
        try:
            pool = self._get_pool()
            if not pool:
                log.warning(f"Cannot flush {len(self._buffer)} buffered log entries - pool unavailable")
                return

            entries = self._buffer.copy()
            self._buffer.clear()
            await self._insert_log_entries(entries, pool)
            log.info(f"Flushed {len(entries)} buffered auto-mod log entries to database")
            
        except Exception as e:
            log.error(f"Error flushing auto-mod log buffer: {e}")
    
    async def flush_logs(self):
        """Public method to manually flush buffered logs."""
        await self._flush_buffer()
    
    async def _insert_log_entries(self, entries: list[dict[str, Any]], pool: asyncpg.Pool) -> None:
        """Insert buffered log entries into automod_logs."""
        if not entries:
            return

        async with acquire_safe(pool) as conn:
            for entry in entries:
                ai_analysis = entry.get("ai_analysis")
                if isinstance(ai_analysis, (dict, list)):
                    ai_analysis = json.dumps(ai_analysis)

                context = entry.get("context")
                if isinstance(context, (dict, list)):
                    context = json.dumps(context)

                await conn.execute(
                    """
                    INSERT INTO automod_logs
                    (guild_id, user_id, message_id, channel_id, rule_id, action_taken, message_content, ai_analysis, context, timestamp)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    entry.get("guild_id"),
                    entry.get("user_id"),
                    entry.get("message_id"),
                    entry.get("channel_id"),
                    entry.get("rule_id"),
                    entry.get("action_taken"),
                    entry.get("message_content"),
                    ai_analysis,
                    context,
                    entry.get("timestamp") or datetime.utcnow(),
                )
            
    async def get_violation_history(self, guild_id: int, user_id: int | None = None,
                                  limit: int = 100, days: int = 30) -> list[dict]:
        """Get violation history for a guild or specific user."""
        try:
            pool = self._get_pool()
            if not pool:
                return []

            async with acquire_safe(pool) as conn:
                if user_id is not None:
                    rows = await conn.fetch(
                        """
                        SELECT *
                        FROM automod_logs
                        WHERE guild_id = $1 AND user_id = $2
                          AND timestamp > NOW() - ($3::text || ' days')::interval
                        ORDER BY timestamp DESC
                        LIMIT $4
                        """,
                        guild_id,
                        user_id,
                        days,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT *
                        FROM automod_logs
                        WHERE guild_id = $1
                          AND timestamp > NOW() - ($2::text || ' days')::interval
                        ORDER BY timestamp DESC
                        LIMIT $3
                        """,
                        guild_id,
                        days,
                        limit,
                    )
            return [dict(row) for row in rows]
            
        except Exception as e:
            log.error(f"Error getting violation history: {e}")
            return []
            
    async def get_statistics(self, guild_id: int, days: int = 7) -> dict[str, Any]:
        """Get moderation statistics for a guild."""
        try:
            pool = self._get_pool()
            if not pool:
                return {
                    'total_violations': 0,
                    'unique_users': 0,
                    'top_rules': [],
                    'daily_breakdown': {}
                }

            async with acquire_safe(pool) as conn:
                total_violations = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM automod_logs
                    WHERE guild_id = $1
                      AND timestamp > NOW() - ($2::text || ' days')::interval
                    """,
                    guild_id,
                    days,
                ) or 0

                unique_users = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT user_id)
                    FROM automod_logs
                    WHERE guild_id = $1
                      AND timestamp > NOW() - ($2::text || ' days')::interval
                    """,
                    guild_id,
                    days,
                ) or 0

                top_rules_rows = await conn.fetch(
                    """
                    SELECT COALESCE(rule_id, 0) AS rule_id, COUNT(*) AS count
                    FROM automod_logs
                    WHERE guild_id = $1
                      AND timestamp > NOW() - ($2::text || ' days')::interval
                    GROUP BY rule_id
                    ORDER BY count DESC
                    LIMIT 5
                    """,
                    guild_id,
                    days,
                )

                daily_rows = await conn.fetch(
                    """
                    SELECT DATE(timestamp) AS day, COUNT(*) AS count
                    FROM automod_logs
                    WHERE guild_id = $1
                      AND timestamp > NOW() - ($2::text || ' days')::interval
                    GROUP BY day
                    ORDER BY day ASC
                    """,
                    guild_id,
                    days,
                )

            return {
                'total_violations': int(total_violations),
                'unique_users': int(unique_users),
                'top_rules': [
                    {"rule_id": int(row["rule_id"]), "count": int(row["count"])}
                    for row in top_rules_rows
                ],
                'daily_breakdown': {
                    str(row["day"]): int(row["count"]) for row in daily_rows
                }
            }
            
        except Exception as e:
            log.error(f"Error getting moderation statistics: {e}")
            return {}
            
    async def create_appeal(self, guild_id: int, user_id: int, log_id: int, reason: str) -> bool:
        """Create an appeal for a moderation action."""
        try:
            pool = self._get_pool()
            if not pool:
                log.warning("No pool available for appeal creation")
                return False

            async with acquire_safe(pool) as conn:
                await conn.execute(
                    """
                    UPDATE automod_logs
                    SET appeal_status = 'pending'
                    WHERE id = $1 AND guild_id = $2 AND user_id = $3
                    """,
                    log_id,
                    guild_id,
                    user_id,
                )
            log.info(f"Appeal created: Guild {guild_id}, User {user_id}, Log {log_id}")
            return True
        except Exception as e:
            log.error(f"Error creating appeal: {e}")
            return False
    
    async def get_pending_appeals(self, guild_id: int) -> list[dict[str, Any]]:
        """Get all pending appeals for a guild (placeholder for future implementation)."""
        try:
            pool = self._get_pool()
            if not pool:
                return []

            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, rule_id, action_taken, timestamp, appeal_status
                    FROM automod_logs
                    WHERE guild_id = $1 AND appeal_status = 'pending'
                    ORDER BY timestamp DESC
                    LIMIT 25
                    """,
                    guild_id,
                )
            return [dict(row) for row in rows]
        except Exception as e:
            log.error(f"Error getting pending appeals: {e}")
            return []
            
    def format_log_entry(self, entry: dict) -> str:
        """Format a log entry for display."""
        timestamp = entry.get('timestamp', datetime.utcnow()).strftime('%Y-%m-%d %H:%M:%S')
        user_id = entry.get('user_id', 'Unknown')
        action = entry.get('action_taken', 'Unknown')
        rule_id = entry.get('rule_id', 'Unknown')
        
        return f"[{timestamp}] User {user_id} - {action} (Rule {rule_id})"
        
    async def export_logs(self, guild_id: int, days: int = 30, format: str = 'csv') -> str:
        """Export violation logs for a guild as CSV."""
        try:
            pool = self._get_pool()
            if not pool:
                return ""
            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, guild_id, user_id, channel_id, rule_id,
                           action_taken, appeal_status, timestamp
                    FROM automod_logs
                    WHERE guild_id = $1
                      AND timestamp > NOW() - ($2::text || ' days')::interval
                    ORDER BY timestamp DESC
                    """,
                    guild_id,
                    days,
                )
            if not rows:
                return ""
            import csv
            import io
            fieldnames = ["id", "guild_id", "user_id", "channel_id", "rule_id",
                          "action_taken", "appeal_status", "timestamp"]
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
            return buf.getvalue()
        except Exception as e:
            log.error(f"Error exporting logs for guild {guild_id}: {e}")
            return ""
