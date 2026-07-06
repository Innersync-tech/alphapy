"""
Auto-Moderation Rule Processing Engine

Handles rule evaluation, configuration, and execution logic for the auto-mod system.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import discord
from discord.ext import commands

from utils.db_helpers import acquire_safe, get_bot_db_pool

log = logging.getLogger(__name__)


class RuleType(Enum):
    """Types of auto-mod rules."""
    SPAM = "spam"
    CONTENT = "content"
    AI = "ai"
    REGEX = "regex"


class ActionType(Enum):
    """Types of actions that can be taken."""
    DELETE = "delete"
    WARN = "warn"
    MUTE = "mute"
    TIMEOUT = "timeout"
    BAN = "ban"


@dataclass
class RuleResult:
    """Result of rule evaluation."""
    triggered: bool
    confidence: float
    reason: str
    context: dict[str, Any]


class RuleProcessor:
    """Processes and evaluates auto-mod rules."""
    
    def __init__(self, bot: commands.Bot | None = None):
        self.bot = bot
        self._rules_cache: dict[int, list[dict]] = {}  # guild_id -> rules
        self._list_rules_cache: dict[int, list[dict]] = {}  # guild_id -> full rule list (enabled + disabled)
        self._cache_ttl = 300  # 5 minutes
        self._cache_updated: dict[int, float] = {}
        self._list_cache_updated: dict[int, float] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def get_cache_stats(self) -> dict[str, int]:
        """Expose rule cache metrics for API health dashboards."""
        return {
            "automod_rules_cache_size": len(self._rules_cache),
            "automod_rules_list_cache_size": len(self._list_rules_cache),
            "automod_rules_cache_hits": self._cache_hits,
            "automod_rules_cache_misses": self._cache_misses,
        }

    def invalidate_guild_cache(self, guild_id: int) -> None:
        """Drop cached rules for a guild (dashboard/API writes bypass create_rule cache hooks)."""
        self._rules_cache.pop(guild_id, None)
        self._cache_updated.pop(guild_id, None)
        self._list_rules_cache.pop(guild_id, None)
        self._list_cache_updated.pop(guild_id, None)

    @staticmethod
    def _coerce_word_list(words: Any) -> list[str]:
        """Normalize dashboard string or list word filters to lowercase tokens."""
        if isinstance(words, str):
            return [w.strip().lower() for w in words.replace(",", " ").split() if w.strip()]
        if isinstance(words, list):
            return [str(w).strip().lower() for w in words if str(w).strip()]
        return []
        
    async def load_rules(self):
        """Load all rules from database into cache."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                log.debug("No database pool available for rule loading - will load on first use")
                return
                
            async with acquire_safe(pool) as conn:
                rules = await conn.fetch("""
                    SELECT r.*, a.action_type, a.config as action_config, a.severity
                    FROM automod_rules r
                    LEFT JOIN automod_actions a ON r.action_id = a.id
                    WHERE r.enabled = true
                    ORDER BY a.severity DESC
                """)
                
            # Organize by guild
            self._rules_cache.clear()
            self._list_rules_cache.clear()
            self._list_cache_updated.clear()
            for rule in rules:
                guild_id = rule['guild_id']
                if guild_id not in self._rules_cache:
                    self._rules_cache[guild_id] = []
                self._rules_cache[guild_id].append(dict(rule))
                
            log.info(f"Loaded {len(rules)} auto-mod rules into cache")
            
        except Exception as e:
            log.error(f"Error loading auto-mod rules: {e}")
            
    async def get_active_rules(self, guild_id: int) -> list[dict]:
        """Get active rules for a guild, with cache refresh if needed."""
        current_time = time.time()
        
        # Check if cache needs refresh
        if (guild_id not in self._cache_updated or 
            current_time - self._cache_updated.get(guild_id, 0) > self._cache_ttl):
            self._cache_misses += 1
            await self._refresh_guild_rules(guild_id)
            self._cache_updated[guild_id] = current_time
        else:
            self._cache_hits += 1

        return self._rules_cache.get(guild_id, [])
        
    async def _refresh_guild_rules(self, guild_id: int):
        """Refresh rules for a specific guild."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                return
                
            async with acquire_safe(pool) as conn:
                rules = await conn.fetch("""
                    SELECT r.*, a.action_type, a.config as action_config, a.severity
                    FROM automod_rules r
                    LEFT JOIN automod_actions a ON r.action_id = a.id
                    WHERE r.guild_id = $1 AND r.enabled = true
                    ORDER BY a.severity DESC
                """, guild_id)
                
            # Deserialize JSON config data
            processed_rules = []
            for rule in rules:
                rule_dict = dict(rule)
                # Deserialize config JSON
                if rule_dict.get('config') and isinstance(rule_dict['config'], str):
                    try:
                        rule_dict['config'] = json.loads(rule_dict['config'])
                    except json.JSONDecodeError as e:
                        log.error(f"Failed to deserialize rule config: {e}")
                        rule_dict['config'] = {}
                
                # Deserialize action_config JSON
                if rule_dict.get('action_config') and isinstance(rule_dict['action_config'], str):
                    try:
                        rule_dict['action_config'] = json.loads(rule_dict['action_config'])
                    except json.JSONDecodeError as e:
                        log.error(f"Failed to deserialize action config: {e}")
                        rule_dict['action_config'] = {}
                
                processed_rules.append(rule_dict)
            
            self._rules_cache[guild_id] = processed_rules
            
        except Exception as e:
            log.error(f"Error refreshing rules for guild {guild_id}: {e}")
            
    async def evaluate_rule(self, rule: dict, message: discord.Message, user_context: dict[str, Any]) -> RuleResult:
        """Evaluate a single rule against a message."""
        rule_type = rule['rule_type']
        config = rule.get('config', {}) or {}

        try:
            # Dashboard may store mentions/caps/duplicate as top-level rule_type values.
            if rule_type == "mentions":
                rule = {
                    **rule,
                    "rule_type": RuleType.CONTENT.value,
                    "config": {
                        **config,
                        "content_type": "mentions",
                        "max_mentions": config.get("max_mentions", 5),
                    },
                }
                rule_type = RuleType.CONTENT.value
            elif rule_type == "caps":
                rule = {
                    **rule,
                    "rule_type": RuleType.SPAM.value,
                    "config": {
                        **config,
                        "spam_type": "caps",
                        "min_length": config.get("min_length", 10),
                        "max_caps_ratio": config.get("max_caps_ratio", 0.7),
                    },
                }
                rule_type = RuleType.SPAM.value
            elif rule_type == "duplicate":
                rule = {
                    **rule,
                    "rule_type": RuleType.SPAM.value,
                    "config": {
                        **config,
                        "spam_type": "duplicate",
                        "max_duplicates": config.get("max_duplicates", 3),
                    },
                }
                rule_type = RuleType.SPAM.value

            if rule_type == RuleType.SPAM.value:
                return await self._evaluate_spam_rule(rule, message, user_context)
            elif rule_type == RuleType.CONTENT.value:
                return await self._evaluate_content_rule(rule, message)
            elif rule_type == RuleType.REGEX.value:
                return await self._evaluate_regex_rule(rule, message)
            elif rule_type == RuleType.AI.value:
                return await self._evaluate_ai_rule(rule, message, user_context)
            else:
                log.warning(f"Unknown rule type: {rule_type}")
                return RuleResult(False, 0.0, "Unknown rule type", {})
                
        except Exception as e:
            log.error(f"Error evaluating rule {rule['id']}: {e}")
            return RuleResult(False, 0.0, f"Error: {e}", {})
            
    async def _evaluate_spam_rule(self, rule: dict, message: discord.Message, user_context: dict[str, Any]) -> RuleResult:
        """Evaluate spam-related rules."""
        config = rule.get('config', {})
        spam_type = config.get('spam_type', 'frequency')
        
        if spam_type == 'frequency':
            # Check message frequency
            max_messages = config.get('max_messages', 5)
            time_window = config.get('time_window', 60)  # seconds
            
            len(user_context.get('message_timestamps', []))
            recent_messages = [
                ts for ts in user_context.get('message_timestamps', [])
                if time.time() - ts < time_window
            ]
            
            if len(recent_messages) >= max_messages:
                return RuleResult(
                    True,
                    min(1.0, len(recent_messages) / max_messages),
                    f"Too many messages ({len(recent_messages)} in {time_window}s)",
                    {'message_count': len(recent_messages), 'time_window': time_window}
                )
                
        elif spam_type == 'duplicate':
            # Check for duplicate messages
            last_message = user_context.get('last_message', '')
            if message.content.lower() == last_message.lower():
                max_duplicates = config.get('max_duplicates', 3)
                
                # Count recent messages (approximate duplicate detection)
                recent_messages = len(user_context.get('message_timestamps', []))
                
                # If we have enough recent messages, consider it duplicate spam
                if recent_messages >= max_duplicates:
                    return RuleResult(
                        True,
                        0.9,
                        f"Duplicate message spam detected (sent {recent_messages} messages recently)",
                        {'duplicate_count': recent_messages, 'last_message': last_message[:50]}
                    )
                    
        elif spam_type == 'caps':
            # Check excessive caps usage
            min_length = config.get('min_length', 10)
            max_caps_ratio = config.get('max_caps_ratio', 0.7)
            
            if len(message.content) >= min_length:
                caps_count = sum(1 for c in message.content if c.isupper())
                caps_ratio = caps_count / len(message.content)
                
                if caps_ratio > max_caps_ratio:
                    return RuleResult(
                        True,
                        min(1.0, caps_ratio),
                        f"Excessive caps usage ({caps_ratio:.1%})",
                        {'caps_ratio': caps_ratio, 'caps_count': caps_count}
                    )
                    
        return RuleResult(False, 0.0, "No spam detected", {})
        
    async def _evaluate_content_rule(self, rule: dict, message: discord.Message) -> RuleResult:
        """Evaluate content-based rules."""
        config = rule.get('config', {})
        content_type = config.get('content_type', 'bad_words')
        content_lower = message.content.lower()
        
        if content_type == 'bad_words':
            # Check for bad words (dashboard may store a single string)
            bad_words = self._coerce_word_list(config.get('words', []))
            found_words = [word for word in bad_words if word in content_lower]
            
            if found_words:
                return RuleResult(
                    True,
                    min(1.0, len(found_words) / 5),
                    f"Bad words detected: {', '.join(found_words[:3])}",
                    {'found_words': found_words, 'word_count': len(found_words)}
                )
                
        elif content_type == 'links':
            # Check for unwanted links
            allow_links = config.get('allow_links', False)
            whitelist = config.get('whitelist', [])
            blacklist = config.get('blacklist', [])
            
            # Simple URL detection
            url_pattern = r'https?://[^\s]+'
            urls = re.findall(url_pattern, message.content, re.IGNORECASE)
            
            if urls and not allow_links:
                # Check against whitelist/blacklist
                for url in urls:
                    domain = re.search(r'https?://([^/]+)', url.lower())
                    if domain:
                        domain = domain.group(1)
                        
                        # If whitelist exists, only allow whitelisted domains
                        if whitelist:
                            if not any(allowed in domain for allowed in whitelist):
                                return RuleResult(
                                    True,
                                    0.8,
                                    f"Unwhitelisted link detected: {domain}",
                                    {'url': url, 'domain': domain}
                                )
                        # If blacklist exists, block blacklisted domains
                        elif blacklist:
                            if any(blocked in domain for blocked in blacklist):
                                return RuleResult(
                                    True,
                                    0.9,
                                    f"Blacklisted link detected: {domain}",
                                    {'url': url, 'domain': domain}
                                )
                        # No whitelist or blacklist - block all links
                        else:
                            return RuleResult(
                                True,
                                0.7,
                                f"Link detected: {domain}",
                                {'url': url, 'domain': domain}
                            )
                                
        elif content_type == 'mentions':
            # Check for mention spam
            max_mentions = config.get('max_mentions', 5)
            mention_count = len(message.mentions)
            
            if mention_count > max_mentions:
                return RuleResult(
                    True,
                    min(1.0, mention_count / (max_mentions * 2)),
                    f"Too many mentions ({mention_count})",
                    {'mention_count': mention_count, 'max_allowed': max_mentions}
                )
                
        return RuleResult(False, 0.0, "No content violation", {})
        
    async def _evaluate_regex_rule(self, rule: dict, message: discord.Message) -> RuleResult:
        """Evaluate regex-based rules."""
        config = rule.get('config', {})
        patterns = list(config.get('patterns', []) or [])
        if not patterns and config.get('pattern'):
            patterns = [config['pattern']]
        
        for pattern_str in patterns:
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                matches = pattern.findall(message.content)
                
                if matches:
                    return RuleResult(
                        True,
                        min(1.0, len(matches) / 3),
                        f"Regex pattern matched: {pattern_str}",
                        {'pattern': pattern_str, 'matches': matches}
                    )
                    
            except re.error as e:
                log.warning(f"Invalid regex pattern '{pattern_str}': {e}")
                
        return RuleResult(False, 0.0, "No regex matches", {})
        
    async def _evaluate_ai_rule(self, rule: dict, message: discord.Message, user_context: dict[str, Any]) -> RuleResult:
        """Evaluate AI-powered rules using Grok (premium feature)."""
        try:
            from gpt.helpers import ask_gpt
            
            config = rule.get('config', {})
            policy = config.get('policy', 'Detect toxic, harmful, or inappropriate content')
            threshold = config.get('threshold', 0.7)
            
            # Build AI prompt for content analysis
            analysis_prompt = f"""Analyze the following message for moderation purposes.

Policy: {policy}

Message content: \"{message.content}\"

Respond with a JSON object containing:
- "violates": true/false
- "confidence": 0.0-1.0 (how confident you are)
- "reason": brief explanation
- "category": type of violation (e.g., "toxicity", "spam", "harassment", "none")

Be conservative - only flag clear violations."""
            
            messages = [
                {"role": "system", "content": "You are a content moderation assistant. Analyze messages objectively and respond only with valid JSON."},
                {"role": "user", "content": analysis_prompt}
            ]
            
            # Call Grok (without reflections for privacy)
            response = await ask_gpt(
                messages,
                user_id=message.author.id,
                model="grok-beta",
                guild_id=message.guild.id if message.guild else None,
                include_reflections=False
            )
            
            if not response:
                log.warning("AI moderation: empty response from Grok")
                return RuleResult(False, 0.0, "AI analysis unavailable", {})
            
            # Parse JSON response
            import json
            try:
                # Extract JSON from response (handle markdown code blocks)
                response_text = response.strip()
                if response_text.startswith('```'):
                    # Remove markdown code block markers
                    lines = response_text.split('\n')
                    response_text = '\n'.join(lines[1:-1]) if len(lines) > 2 else response_text
                    response_text = response_text.replace('```json', '').replace('```', '').strip()
                
                analysis = json.loads(response_text)
                violates = analysis.get('violates', False)
                confidence = float(analysis.get('confidence', 0.0))
                reason = analysis.get('reason', 'AI flagged content')
                category = analysis.get('category', 'unknown')
                
                if violates and confidence >= threshold:
                    return RuleResult(
                        True,
                        confidence,
                        f"AI detected {category}: {reason}",
                        {'category': category, 'ai_reason': reason, 'confidence': confidence}
                    )
                else:
                    return RuleResult(False, confidence, f"AI analysis: {reason}", {'category': category})
                    
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                log.error(f"AI moderation: failed to parse response: {e}")
                return RuleResult(False, 0.0, "AI analysis parse error", {'error': str(e)})
                
        except Exception as e:
            log.error(f"AI moderation error: {e}")
            # Fail open - don't block on AI errors
            return RuleResult(False, 0.0, f"AI error: {str(e)}", {'error': str(e)})
        
    async def create_rule(self, guild_id: int, rule_type: str, name: str, config: dict, 
                         action_type: str, action_config: dict, created_by: int, is_premium: bool = False) -> int:
        """Create a new auto-mod rule."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                raise ValueError("Database temporarily unavailable. Please try again later.")
                
            async with acquire_safe(pool) as conn:
                # First create the action
                action_id = await conn.fetchval("""
                    INSERT INTO automod_actions (guild_id, action_type, config, is_premium, created_by)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                """, guild_id, action_type, json.dumps(action_config), is_premium, created_by)
                
                # Then create the rule
                rule_id = await conn.fetchval("""
                    INSERT INTO automod_rules (guild_id, rule_type, name, config, action_id, created_by, is_premium)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                """, guild_id, rule_type, name, json.dumps(config), action_id, created_by, is_premium)
                
                self.invalidate_guild_cache(guild_id)
                    
                return rule_id
                
        except Exception as e:
            log.error(f"Error creating auto-mod rule: {e}")
            raise

    async def list_rules(self, guild_id: int) -> list[dict[str, Any]]:
        """List all auto-mod rules for a guild."""
        try:
            current_time = time.time()
            if (
                guild_id in self._list_cache_updated
                and current_time - self._list_cache_updated.get(guild_id, 0) <= self._cache_ttl
            ):
                self._cache_hits += 1
                return [dict(row) for row in self._list_rules_cache.get(guild_id, [])]

            self._cache_misses += 1
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                raise ValueError("Database not available")

            async with acquire_safe(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        r.id,
                        r.guild_id,
                        r.rule_type,
                        r.name,
                        r.enabled,
                        r.config,
                        r.is_premium,
                        r.created_at,
                        a.action_type
                    FROM automod_rules r
                    LEFT JOIN automod_actions a ON r.action_id = a.id
                    WHERE r.guild_id = $1
                    ORDER BY r.created_at DESC
                    """,
                    guild_id,
                )

            result = [dict(row) for row in rows]
            self._list_rules_cache[guild_id] = result
            self._list_cache_updated[guild_id] = current_time
            return result
        except Exception as e:
            log.error(f"Error listing auto-mod rules for guild {guild_id}: {e}")
            raise

    async def get_rule(self, guild_id: int, rule_id: int) -> dict[str, Any] | None:
        """Get one auto-mod rule for a guild."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                raise ValueError("Database not available")

            async with acquire_safe(pool) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        r.id,
                        r.guild_id,
                        r.rule_type,
                        r.name,
                        r.enabled,
                        r.config,
                        r.is_premium,
                        r.created_at,
                        a.action_type,
                        a.config AS action_config
                    FROM automod_rules r
                    LEFT JOIN automod_actions a ON r.action_id = a.id
                    WHERE r.guild_id = $1 AND r.id = $2
                    """,
                    guild_id,
                    rule_id,
                )
            return dict(row) if row else None
        except Exception as e:
            log.error(f"Error getting auto-mod rule {rule_id} for guild {guild_id}: {e}")
            raise

    async def delete_rule(self, guild_id: int, rule_id: int) -> bool:
        """Delete an auto-mod rule (and its linked action) from a guild."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                raise ValueError("Database not available")

            async with acquire_safe(pool) as conn:
                row = await conn.fetchrow(
                    "SELECT action_id FROM automod_rules WHERE id = $1 AND guild_id = $2",
                    rule_id,
                    guild_id,
                )
                if not row:
                    return False

                action_id = row["action_id"]
                await conn.execute(
                    "DELETE FROM automod_rules WHERE id = $1 AND guild_id = $2",
                    rule_id,
                    guild_id,
                )
                if action_id:
                    await conn.execute(
                        "DELETE FROM automod_actions WHERE id = $1 AND guild_id = $2",
                        action_id,
                        guild_id,
                    )

            self.invalidate_guild_cache(guild_id)
            return True
        except Exception as e:
            log.error(f"Error deleting auto-mod rule {rule_id} for guild {guild_id}: {e}")
            raise

    async def update_rule(
        self,
        guild_id: int,
        rule_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        action_type: str | None = None,
        config: dict[str, Any] | None = None,
        action_config: dict[str, Any] | None = None,
    ) -> bool:
        """Update core properties of an auto-mod rule."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                raise ValueError("Database temporarily unavailable. Please try again later.")

            async with acquire_safe(pool) as conn:
                row = await conn.fetchrow(
                    "SELECT id, action_id FROM automod_rules WHERE id = $1 AND guild_id = $2",
                    rule_id,
                    guild_id,
                )
                if not row:
                    return False

                if name is not None:
                    await conn.execute(
                        "UPDATE automod_rules SET name = $1, updated_at = NOW() WHERE id = $2 AND guild_id = $3",
                        name,
                        rule_id,
                        guild_id,
                    )

                if enabled is not None:
                    await conn.execute(
                        "UPDATE automod_rules SET enabled = $1, updated_at = NOW() WHERE id = $2 AND guild_id = $3",
                        enabled,
                        rule_id,
                        guild_id,
                    )

                if action_type is not None and row["action_id"]:
                    await conn.execute(
                        "UPDATE automod_actions SET action_type = $1 WHERE id = $2 AND guild_id = $3",
                        action_type,
                        row["action_id"],
                        guild_id,
                    )

                if config is not None:
                    await conn.execute(
                        "UPDATE automod_rules SET config = $1, updated_at = NOW() WHERE id = $2 AND guild_id = $3",
                        json.dumps(config),
                        rule_id,
                        guild_id,
                    )

                if action_config is not None and row["action_id"]:
                    await conn.execute(
                        "UPDATE automod_actions SET config = $1 WHERE id = $2 AND guild_id = $3",
                        json.dumps(action_config),
                        row["action_id"],
                        guild_id,
                    )

            self.invalidate_guild_cache(guild_id)
            return True
        except Exception as e:
            log.error(f"Error updating auto-mod rule {rule_id} for guild {guild_id}: {e}")
            raise
    
    async def check_database_health(self) -> bool:
        """Check if database is available."""
        try:
            pool = get_bot_db_pool(self.bot) if self.bot else None
            if not pool:
                return False
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            log.error(f"Database health check failed: {e}")
            return False
