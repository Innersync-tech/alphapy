import logging
import sys
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

# Configure logging with rotation
log_handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3)  # 5MB per file, keep 3 backups
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        log_handler,
        logging.StreamHandler(sys.stdout)
    ]
)


MAX_EVENT_HISTORY = 25


class GPTStatusLogs:
    def __init__(self) -> None:
        self.last_success_time: datetime | None = None
        self.last_error_time: datetime | None = None
        self.last_error_type: str | None = None
        self.average_latency_ms: int = 0
        self.total_tokens_session: int = 0
        self.current_model: str = "grok-3"  # Default, will be updated from actual usage
        self.last_user: int | None = None
        self.success_count: int = 0
        self.error_count: int = 0
        self.rate_limit_hits: int = 0
        self.last_rate_limit_time: datetime | None = None
        self.last_failure_kind: str | None = None
        self.last_failure_detail: str | None = None
        self.last_success_latency_ms: int | None = None
        self.success_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_HISTORY)
        self.error_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_HISTORY)


gpt_logs = GPTStatusLogs()


def get_gpt_status_logs() -> GPTStatusLogs:
    return gpt_logs


logger = logging.getLogger("bot")


def log_with_guild(message: str, guild_id: int = None, level: str = "info", extra: dict = None) -> None:
    """Enhanced logging function with guild context"""
    if guild_id:
        message = f"[Guild:{guild_id}] {message}"

    if extra:
        message = f"{message} {extra}"

    if level == "debug":
        logger.debug(message)
    elif level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "critical":
        logger.critical(message)
    else:
        logger.info(message)


def log_guild_action(guild_id: int, action: str, user: str = None, details: str = None, level: str = "info") -> None:
    """Log guild-specific actions with structured format"""
    parts = [f"GUILD:{guild_id}"]
    if user:
        parts.append(f"USER:{user}")
    parts.append(f"ACTION:{action}")
    if details:
        parts.append(f"DETAILS:{details}")

    message = " | ".join(parts)
    log_with_guild(message, guild_id, level)


def log_database_event(event: str, guild_id: int = None, details: str = None, level: str = "info") -> None:
    """Log database-related events"""
    message = f"DATABASE: {event}"
    if details:
        message += f" - {details}"
    log_with_guild(message, guild_id, level)


def should_log_to_discord(level: str, guild_id: int | None = None) -> bool:
    """
    Check if a log level should be sent to Discord based on guild's log level setting.
    
    Args:
        level: Log level (debug, info, warning, error, critical)
        guild_id: Guild ID to check settings for (None = always log)
    
    Returns:
        True if log should be sent to Discord, False otherwise
    """
    if guild_id is None:
        # Always log if no guild context (backwards compatibility)
        return True
    
    # Try to get bot instance and settings
    try:
        from gpt.helpers import bot_instance
        if bot_instance is None:
            return True  # Fallback: log if bot not available
        
        settings = getattr(bot_instance, "settings", None)
        if not settings:
            return True  # Fallback: log if settings not available
        
        log_level = settings.get("system", "log_level", guild_id)
        if not isinstance(log_level, str):
            log_level = "verbose"  # Default to verbose
        
        log_level = log_level.lower()
        
        # Map log levels
        if log_level == "verbose":
            return True  # Log everything
        elif log_level == "normal":
            # Exclude debug, include everything else
            return level != "debug"
        elif log_level == "critical":
            # Only error and critical
            return level in ["error", "critical"]
        else:
            # Unknown level, default to verbose
            return True
    except Exception:
        # If anything fails, default to logging (safe fallback)
        return True
