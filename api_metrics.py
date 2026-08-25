"""Dashboard / Mind metrics routes (split from api.py — ballast cut P1)."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from asyncpg import exceptions as pg_exceptions
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

import config
from utils import core_ingress as core_ingress_module
from utils.logger import get_gpt_status_logs, logger
from utils.runtime_metrics import get_bot_snapshot, serialize_snapshot
from utils.supabase_client import SupabaseConfigurationError, _supabase_post
from utils.timezone import BRUSSELS_TZ
from version import CODENAME, __version__

metrics_router = APIRouter()

# Shared with api.py — set via bind_shared_state()
db_pool = None  # live pool via _get_pool()
_telemetry_queue: list[dict[str, Any]] = []
_command_stats_cache: dict[tuple[int | None, int, int], tuple[Any, datetime]] = {}
_ip_rate_limits: dict[str, list[float]] = {}
MAX_TELEMETRY_QUEUE_SIZE = 100
MAX_TELEMETRY_RETRIES = 5
MAX_COMMAND_STATS_CACHE_SIZE = 64
COMMAND_STATS_CACHE_TTL = 30  # seconds


def bind_shared_state(
    *,
    get_pool,
    telemetry_queue: list[dict[str, Any]],
    command_stats_cache: dict,
    ip_rate_limits: dict[str, list[float]],
    max_telemetry_queue_size: int,
    max_telemetry_retries: int,
    max_command_stats_cache_size: int,
    command_stats_cache_ttl: int | float,
) -> None:
    """Bind live api.py state. get_pool is a callable returning the current pool."""
    global _telemetry_queue, _command_stats_cache, _ip_rate_limits
    global MAX_TELEMETRY_QUEUE_SIZE, MAX_TELEMETRY_RETRIES, MAX_COMMAND_STATS_CACHE_SIZE
    global COMMAND_STATS_CACHE_TTL, _get_pool
    _telemetry_queue = telemetry_queue
    _command_stats_cache = command_stats_cache
    _ip_rate_limits = ip_rate_limits
    MAX_TELEMETRY_QUEUE_SIZE = max_telemetry_queue_size
    MAX_TELEMETRY_RETRIES = max_telemetry_retries
    MAX_COMMAND_STATS_CACHE_SIZE = max_command_stats_cache_size
    COMMAND_STATS_CACHE_TTL = command_stats_cache_ttl
    _get_pool = get_pool


_get_pool = lambda: None  # noqa: E731


def _pool():
    return _get_pool()


async def _lazy_get_authenticated_user_id(
    request: Request,
    authorization: str | None = Header(None),
) -> str:
    from api import get_authenticated_user_id as _get

    return await _get(request, authorization)


def _datetime_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(BRUSSELS_TZ).isoformat()


class GuildInfo(BaseModel):
    id: int
    name: str
    member_count: int | None
    owner_id: int | None


class CommandInfo(BaseModel):
    qualified_name: str
    description: str | None
    type: str


class BotMetrics(BaseModel):
    online: bool
    latency_ms: float | None
    uptime_seconds: int | None
    uptime_human: str | None
    commands_loaded: int
    version: str
    codename: str
    guilds: list[GuildInfo]
    commands: list[CommandInfo]


class GPTLogEvent(BaseModel):
    timestamp: str | None
    user_id: int | None
    tokens_used: int | None = None
    latency_ms: int | None = None
    error_type: str | None = None


class GPTMetrics(BaseModel):
    last_success_time: str | None
    last_error_type: str | None
    last_error_time: str | None
    average_latency_ms: int
    total_tokens_session: int
    current_model: str
    last_user_id: int | None
    success_count: int
    error_count: int
    rate_limit_hits: int
    last_rate_limit_time: str | None
    last_success_latency_ms: int | None
    recent_successes: list[GPTLogEvent]
    recent_errors: list[GPTLogEvent]


class UpcomingReminder(BaseModel):
    id: int
    name: str
    channel_id: int
    scheduled_time: str | None
    is_recurring: bool


class ReminderStats(BaseModel):
    total: int
    recurring: int
    one_off: int
    next_event_time: str | None
    per_channel: dict[str, int]
    upcoming: list[UpcomingReminder]


class TicketListItem(BaseModel):
    id: int
    username: str | None
    status: str | None
    channel_id: int | None
    created_at: str | None


class TicketStats(BaseModel):
    total: int
    per_status: dict[str, int]
    open_count: int
    last_ticket_created_at: str | None
    average_close_seconds: int | None
    average_close_human: str | None
    open_items: list[TicketListItem]
    open_ticket_ids: list[int]  # List of IDs for easy access


class SettingOverride(BaseModel):
    scope: str
    key: str
    value: str


class InfrastructureMetrics(BaseModel):
    database_up: bool
    pool_size: int | None
    checked_at: str


class CacheMetrics(BaseModel):
    """Cache size metrics for monitoring."""
    command_tracker_queue_size: int
    command_stats_cache_size: int
    ip_rate_limits_size: int
    sync_cooldowns_size: int
    ticket_cooldowns_size: int
    automod_rules_cache_size: int = 0
    automod_rules_list_cache_size: int = 0
    automod_rules_cache_hits: int = 0
    automod_rules_cache_misses: int = 0
    engagement_feature_flag_cache_size: int = 0
    engagement_food_channels_cache_size: int = 0
    engagement_feature_flag_cache_hits: int = 0
    engagement_feature_flag_cache_misses: int = 0
    engagement_food_channels_cache_hits: int = 0
    engagement_food_channels_cache_misses: int = 0


class PremiumMetrics(BaseModel):
    """Premium guard observability metrics (same process only)."""
    premium_checks_total: int
    premium_checks_core_api: int
    premium_checks_local: int
    premium_cache_hits: int
    premium_transfers_count: int
    premium_cache_size: int
    premium_guild_cache_size: int = 0
    premium_guild_cache_hits: int = 0
    premium_guild_cache_misses: int = 0


class IdentityMetrics(BaseModel):
    """Discord link resolve observability (same process only)."""

    identity_resolve_total: int = 0
    identity_resolve_hit_links: int = 0
    identity_resolve_miss: int = 0
    identity_resolve_db_error: int = 0
    identity_profile_fallback_used: int = 0
    identity_jwt_unlinked_403: int = 0
    identity_link_webhook_ok: int = 0
    identity_link_webhook_conflict: int = 0
    identity_link_webhook_503: int = 0


class CommandUsage(BaseModel):
    command_name: str
    usage_count: int


class CommandStats(BaseModel):
    top_commands: list[CommandUsage]
    total_commands_24h: int
    period_days: int


class AgentSessionMetricsPayload(BaseModel):
    """Aggregate /agent session counts for Mind observability (no user content)."""

    enabled: bool
    active_sessions: int = 0
    started_24h: int = 0
    completed_24h: int = 0
    active_origin_discord: int = 0
    active_origin_app: int = 0


class DashboardMetrics(BaseModel):
    bot: BotMetrics
    gpt: GPTMetrics
    reminders: ReminderStats
    tickets: TicketStats
    settings_overrides: list[SettingOverride]
    infrastructure: InfrastructureMetrics
    command_usage: CommandStats | None = None
    cache_metrics: CacheMetrics | None = None
    premium_metrics: PremiumMetrics | None = None
    identity_metrics: IdentityMetrics | None = None
    agent_sessions: AgentSessionMetricsPayload | None = None


def _serialize_gpt_events(raw_events) -> list[GPTLogEvent]:
    events: list[GPTLogEvent] = []
    for evt in raw_events:
        events.append(
            GPTLogEvent(
                timestamp=_datetime_to_iso(evt.get("timestamp")),
                user_id=evt.get("user_id"),
                tokens_used=evt.get("tokens_used"),
                latency_ms=evt.get("latency_ms"),
                error_type=evt.get("error_type"),
            )
        )
    return events


def _collect_gpt_metrics() -> GPTMetrics:
    logs = get_gpt_status_logs()
    return GPTMetrics(
        last_success_time=_datetime_to_iso(logs.last_success_time),
        last_error_type=logs.last_error_type,
        last_error_time=_datetime_to_iso(logs.last_error_time),
        average_latency_ms=int(logs.average_latency_ms or 0),
        total_tokens_session=int(logs.total_tokens_session or 0),
        current_model=logs.current_model,
        last_user_id=logs.last_user,
        success_count=int(logs.success_count or 0),
        error_count=int(logs.error_count or 0),
        rate_limit_hits=int(logs.rate_limit_hits or 0),
        last_rate_limit_time=_datetime_to_iso(logs.last_rate_limit_time),
        last_success_latency_ms=logs.last_success_latency_ms,
        recent_successes=_serialize_gpt_events(logs.success_events),
        recent_errors=_serialize_gpt_events(logs.error_events),
    )


async def _fetch_reminder_stats(guild_id: int | None = None) -> ReminderStats:
    """Fetch reminder statistics for dashboard."""
    default = ReminderStats(
        total=0,
        recurring=0,
        one_off=0,
        next_event_time=None,
        per_channel={},
        upcoming=[],
    )
    # db pool via _pool()
    if _pool() is None:
        return default
    try:
        async with _pool().acquire() as conn:
            where_clause = "WHERE guild_id = $1" if guild_id is not None else ""
            params = [guild_id] if guild_id is not None else []
            counts_row = await conn.fetchrow(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE COALESCE(array_length(days, 1), 0) > 0) AS recurring,
                    COUNT(*) FILTER (WHERE COALESCE(array_length(days, 1), 0) = 0) AS one_off
                FROM reminders
                {where_clause};
                """,
                *params
            )
            next_event_query = """
                SELECT event_time
                FROM reminders
                WHERE event_time IS NOT NULL AND event_time >= NOW()
                """
            if guild_id is not None:
                next_event_query += " AND guild_id = $1"
                next_event_params = [guild_id]
            else:
                next_event_params = []
            next_event_query += " ORDER BY event_time ASC LIMIT 1;"

            next_event_row = await conn.fetchrow(next_event_query, *next_event_params)
            per_channel_query = "SELECT channel_id, COUNT(*) AS c FROM reminders"
            upcoming_query = """
                SELECT id, name, channel_id, event_time
                FROM reminders
                WHERE event_time IS NOT NULL AND event_time >= NOW()
                """

            if guild_id is not None:
                per_channel_query += " WHERE guild_id = $1 GROUP BY channel_id;"
                per_channel_params = [guild_id]
                upcoming_query += " AND guild_id = $1 ORDER BY event_time ASC LIMIT 3;"
                upcoming_params = [guild_id]
            else:
                per_channel_query += " GROUP BY channel_id;"
                per_channel_params = []
                upcoming_query += " ORDER BY event_time ASC LIMIT 3;"
                upcoming_params = []

            per_channel_rows = await conn.fetch(per_channel_query, *per_channel_params)
            upcoming_rows = await conn.fetch(upcoming_query, *upcoming_params)
    except pg_exceptions.UndefinedTableError:
        return default
    except Exception as exc:
        logger.warning(f"[WARN] reminder stats failed: {exc}")
        return default

    if counts_row is None:
        return default

    per_channel = {
        str(row["channel_id"]): int(row["c"] or 0)
        for row in per_channel_rows or []
    }

    upcoming = [
        UpcomingReminder(
            id=int(row["id"]),
            name=row["name"],
            channel_id=int(row["channel_id"]),
            scheduled_time=_datetime_to_iso(row["event_time"]),
            is_recurring=False,
        )
        for row in upcoming_rows or []
    ]

    next_event_iso = (
        _datetime_to_iso(next_event_row["event_time"])
        if next_event_row and next_event_row["event_time"]
        else None
    )

    return ReminderStats(
        total=int(counts_row["total"] or 0),
        recurring=int(counts_row["recurring"] or 0),
        one_off=int(counts_row["one_off"] or 0),
        next_event_time=next_event_iso,
        per_channel=per_channel,
        upcoming=upcoming,
    )


def _format_duration_seconds(seconds: int) -> str:
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


async def _fetch_ticket_stats(guild_id: int | None = None) -> TicketStats:
    """Fetch ticket statistics from database. Returns empty stats if database unavailable."""
    default = TicketStats(
        total=0,
        per_status={},
        open_count=0,
        last_ticket_created_at=None,
        average_close_seconds=None,
        average_close_human=None,
        open_items=[],
        open_ticket_ids=[],
    )
    # db pool via _pool()
    if _pool() is None or _pool().is_closing():
        return default
    try:
        async with _pool().acquire() as conn:
            where_clause = "WHERE guild_id = $1" if guild_id is not None else ""
            params = [guild_id] if guild_id is not None else []

            status_rows = await conn.fetch(
                f"SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS c FROM support_tickets {where_clause} GROUP BY status;",
                *params
            )

            last_query = f"SELECT created_at FROM support_tickets {where_clause} ORDER BY created_at DESC LIMIT 1;"
            last_row = await conn.fetchrow(last_query, *params)

            avg_where = where_clause + (" AND " if where_clause else " WHERE ") + "status = 'closed' AND updated_at IS NOT NULL"
            avg_row = await conn.fetchrow(
                f"SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) AS avg_s FROM support_tickets {avg_where};",
                *params
            )

            open_where = where_clause + (" AND " if where_clause else " WHERE ") + "status IS DISTINCT FROM 'closed'"
            open_rows = await conn.fetch(
                f"""
                SELECT id, username, status, channel_id, created_at
                FROM support_tickets
                {open_where}
                ORDER BY created_at ASC
                LIMIT 10;
                """,
                *params
            )
    except pg_exceptions.UndefinedTableError:
        return default
    except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
        # Pool is closing or connection was lost - this is expected during shutdown
        logger.debug(f"Ticket stats: Database connection unavailable (pool closing?): {conn_err.__class__.__name__}")
        return default
    except Exception as exc:
        logger.warning(f"[WARN] ticket stats failed: {exc}")
        return default

    per_status = {str(row["status"]): int(row["c"] or 0) for row in status_rows}
    total = sum(per_status.values())
    open_count = per_status.get("open", 0)
    last_created_iso = (
        _datetime_to_iso(last_row["created_at"])
        if last_row and last_row["created_at"]
        else None
    )

    avg_seconds = None
    avg_human = None
    if avg_row and avg_row["avg_s"] is not None:
        try:
            avg_seconds = int(float(avg_row["avg_s"]))
        except (TypeError, ValueError):
            avg_seconds = None
        if avg_seconds is not None:
            avg_human = _format_duration_seconds(avg_seconds)

    open_items = [
        TicketListItem(
            id=int(row["id"]),
            username=row["username"],
            status=row["status"],
            channel_id=int(row["channel_id"]) if row["channel_id"] else None,
            created_at=_datetime_to_iso(row["created_at"]),
        )
        for row in open_rows or []
    ]
    
    # Extract IDs for easy access
    open_ticket_ids = [item.id for item in open_items]

    return TicketStats(
        total=total,
        per_status=per_status,
        open_count=open_count,
        last_ticket_created_at=last_created_iso,
        average_close_seconds=avg_seconds,
        average_close_human=avg_human,
        open_items=open_items,
        open_ticket_ids=open_ticket_ids,
    )


async def _fetch_settings_overrides(guild_id: int | None = None) -> list[SettingOverride]:
    """Fetch settings overrides for dashboard."""
    # db pool via _pool()
    if _pool() is None:
        return []
    try:
        async with _pool().acquire() as conn:
            if guild_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT scope, key, value
                    FROM bot_settings
                    WHERE guild_id = $1
                    ORDER BY scope, key;
                    """,
                    guild_id
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT scope, key, value
                    FROM bot_settings
                    ORDER BY scope, key;
                    """
                )
    except pg_exceptions.UndefinedTableError:
        return []
    except Exception as exc:
        logger.warning(f"[WARN] settings overrides fetch failed: {exc}")
        return []

    return [
        SettingOverride(scope=row["scope"], key=row["key"], value=str(row["value"]))
        for row in rows or []
    ]


async def _collect_infrastructure_metrics() -> InfrastructureMetrics:
    """Collect infrastructure metrics for dashboard."""
    checked_at = _datetime_to_iso(datetime.now(UTC)) or ""
    # db pool via _pool()
    if _pool() is None:
        return InfrastructureMetrics(database_up=False, pool_size=None, checked_at=checked_at)

    database_up = False
    try:
        async with _pool().acquire() as conn:
            await conn.execute("SELECT 1;")
            database_up = True
    except Exception as exc:
        logger.warning(f"[WARN] db health check failed: {exc}")

    pool_size: int | None
    try:
        pool_size = _pool().get_size()
    except Exception:
        pool_size = None

    return InfrastructureMetrics(
        database_up=database_up,
        pool_size=pool_size,
        checked_at=checked_at,
    )


def _count_recent_events(events: list[GPTLogEvent], hours: int = 24) -> int:
    if not events:
        return 0
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    count = 0
    for evt in events:
        ts = getattr(evt, "timestamp", None)
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        if dt >= cutoff:
            count += 1
    return count


def _calculate_status(bot_metrics: BotMetrics, gpt_errors_1h: int) -> str:
    """
    Calculate system status based on bot health (online status + Grok/LLM errors).
    This function is used consistently in both debug logs and persisted telemetry.
    
    Status logic:
    - outage: Bot offline (after startup grace period) OR >5 Grok/LLM errors/hour
    - degraded: Bot starting up (<2min) OR 1-5 Grok/LLM errors/hour
    - operational: Bot online and no recent Grok/LLM errors
    """
    if not bot_metrics.online:
        # If bot has been running for more than 2 minutes and goes offline, it's a real outage
        # (could be Railway/host issue, bot crash, etc.)
        if bot_metrics.uptime_seconds and bot_metrics.uptime_seconds >= 120:
            return "outage"  # Bot was running but went offline - real outage (host issue, crash, etc.)
        elif bot_metrics.uptime_seconds and bot_metrics.uptime_seconds < 120:
            return "degraded"  # Still starting up, give it time
        else:
            # No uptime data - bot might not have started yet, or truly offline
            # If we have guilds, bot was connected before, so likely an outage
            return "outage" if len(bot_metrics.guilds) > 0 else "degraded"
    elif gpt_errors_1h > 5:
        return "outage"  # Too many recent Grok/LLM errors
    elif gpt_errors_1h > 0:
        return "degraded"  # Some Grok/LLM errors, but not critical
    else:
        return "operational"  # Bot is online and no recent errors


async def _telemetry_ingest_loop(interval: int = 45) -> None:
    """
    Background task that periodically ingests telemetry data to Supabase.
    
    This function runs continuously, collecting metrics and writing them to
    telemetry.subsystem_snapshots every `interval` seconds.
    """
    logger.info(f"🚀 Telemetry ingest loop started (interval: {interval}s)")
    
    # Wait a bit before first run to allow app to fully start
    await asyncio.sleep(5)
    
    # Track if we've seen the bot online at least once
    
    while True:
        try:
            # Check if pool is closing before starting operations
            # db pool via _pool()
            if _pool() is None or _pool().is_closing():
                logger.debug("Telemetry loop: Database pool is closing, skipping iteration")
                await asyncio.sleep(interval)
                continue
            
            # Collect metrics
            snapshot = await get_bot_snapshot()
            bot_payload = serialize_snapshot(snapshot)
            bot_metrics = BotMetrics(
                version=__version__,
                codename=CODENAME,
                **bot_payload,
            )
            
            # Track if bot has been online at least once
            if bot_metrics.online:
                pass
            gpt_metrics = _collect_gpt_metrics()
            
            # Use MAIN_GUILD_ID for telemetry if configured
            main_guild_id = None
            if hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
                main_guild_id = config.MAIN_GUILD_ID
                logger.debug(f"📡 Telemetry ingest: Using MAIN_GUILD_ID ({main_guild_id}) for metrics collection")
            
            try:
                ticket_stats = await _fetch_ticket_stats(main_guild_id)
            except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
                # Pool is closing - use default stats
                logger.debug(f"Telemetry loop: Database unavailable (pool closing?): {conn_err.__class__.__name__}")
                ticket_stats = TicketStats(total=0, per_status={}, open_count=0, last_ticket_created_at=None, average_close_seconds=None, average_close_human=None, open_items=[], open_ticket_ids=[])
            except Exception as exc:
                logger.debug(f"Telemetry loop: Failed to fetch ticket stats: {exc}")
                ticket_stats = TicketStats(total=0, per_status={}, open_count=0, last_ticket_created_at=None, average_close_seconds=None, average_close_human=None, open_items=[], open_ticket_ids=[])
            
            # Persist to Supabase
            try:
                await _persist_telemetry_snapshot(bot_metrics, gpt_metrics, ticket_stats)
            except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
                # Pool is closing - skip this snapshot
                logger.debug(f"Telemetry loop: Database unavailable during persist (pool closing?): {conn_err.__class__.__name__}")
                # Continue loop - don't raise
            except Exception as exc:
                logger.debug(f"Telemetry loop: Failed to persist snapshot: {exc}")
                # Continue loop - don't raise
            
            # Flush retry queue after successful write
            try:
                await _flush_telemetry_queue()
            except Exception as exc:
                logger.debug(f"Telemetry loop: Failed to flush queue: {exc}")
                # Continue loop - don't raise

            # Drain operational events queue to Core-API
            try:
                await core_ingress_module.flush_operational_events_queue()
            except Exception as exc:
                logger.debug(f"Telemetry loop: Failed to flush operational events: {exc}")
            
            # Log the calculated status for debugging (using same logic as _persist_telemetry_snapshot)
            # Status is based ONLY on bot health (online status + Grok/LLM errors), NOT on open tickets
            gpt_errors_1h = _count_recent_events(gpt_metrics.recent_errors, hours=1)
            calculated_status = _calculate_status(bot_metrics, gpt_errors_1h)
            
            logger.debug(
                f"✅ Telemetry snapshot ingested: bot_online={bot_metrics.online}, "
                f"status={calculated_status}, uptime={bot_metrics.uptime_seconds}s, "
                f"guilds={len(bot_metrics.guilds)}, gpt_errors_1h={gpt_errors_1h}, "
                f"open_tickets={ticket_stats.open_count} (tickets excluded from status)"
            )
            
        except asyncio.CancelledError:
            logger.info("🛑 Telemetry ingest loop cancelled")
            raise
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            # Pool is closing or connection was lost - this is expected during shutdown
            logger.debug(f"Telemetry loop: Database connection unavailable (pool closing?): {conn_err.__class__.__name__}")
            # Continue loop - don't raise
        except Exception as exc:
            logger.warning(
                f"⚠️ Telemetry ingest failed (will retry): {exc.__class__.__name__}: {exc}",
                exc_info=True
            )
            # Continue loop even on error
        
        # Sleep for the configured interval before next iteration
        await asyncio.sleep(interval)



async def _flush_telemetry_queue() -> None:
    """Flush telemetry queue with exponential backoff retry."""
    global _telemetry_queue
    
    if not _telemetry_queue:
        return
    
    # Process queue (copy to avoid modification during iteration)
    queue_copy = _telemetry_queue.copy()
    _telemetry_queue.clear()
    
    for item in queue_copy:
        retry_count = item.get("retry_count", 0)
        if retry_count >= MAX_TELEMETRY_RETRIES:
            logger.debug(f"⚠️ Dropping telemetry snapshot after {MAX_TELEMETRY_RETRIES} retries")
            continue

        # Exponential backoff: 1s, 2s, 4s, 8s, 16s
        backoff_seconds = 2 ** retry_count
        await asyncio.sleep(backoff_seconds)

        try:
            if core_ingress_module._is_ingress_configured():
                ok = await core_ingress_module.post_telemetry(item["payload"])
                if ok:
                    logger.debug(f"✅ Telemetry snapshot retry succeeded via Core (attempt {retry_count + 1})")
                    continue
            # Fallback: direct Supabase write when Core not configured or Core failed
            await _supabase_post(
                "subsystem_snapshots",
                item["payload"],
                upsert=True,
                schema="telemetry"
            )
            logger.debug(f"✅ Telemetry snapshot retry succeeded (attempt {retry_count + 1})")
        except Exception as retry_error:
            item["retry_count"] = retry_count + 1
            if len(_telemetry_queue) < MAX_TELEMETRY_QUEUE_SIZE:
                _telemetry_queue.append(item)
            else:
                _telemetry_queue.pop(0)
                _telemetry_queue.append(item)
            logger.debug(f"⚠️ Telemetry retry {retry_count + 1}/{MAX_TELEMETRY_RETRIES} failed: {retry_error}")


async def _persist_telemetry_snapshot(
    bot_metrics: BotMetrics,
    gpt_metrics: GPTMetrics,
    ticket_stats: TicketStats,
) -> None:
    """
    Persist telemetry snapshot to Supabase using REST API.
    
    Note: Telemetry data MUST go to Supabase, not to the local PostgreSQL database.
    The local PostgreSQL on Railway is only for reminders, tickets, etc.
    """
    # Collect metrics
    command_events_24h = 0
    # db pool via _pool()
    
    # Use MAIN_GUILD_ID for telemetry if configured
    main_guild_id = None
    if hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        main_guild_id = config.MAIN_GUILD_ID
    
    if _pool():
        try:
            # Check if pool is closed before acquiring
            if _pool().is_closing():
                command_events_24h = 0
            else:
                async with _pool().acquire() as conn:
                    try:
                        query = """
                            SELECT COUNT(*)
                            FROM audit_logs
                            WHERE created_at >= timezone('utc', now()) - interval '24 hours'
                        """
                        params: list[Any] = []
                        if main_guild_id:
                            query += " AND guild_id = $1"
                            params.append(main_guild_id)
                        
                        command_events_24h = await conn.fetchval(query, *params) if params else await conn.fetchval(query)
                        if command_events_24h is None:
                            command_events_24h = 0
                    except pg_exceptions.UndefinedTableError:
                        command_events_24h = 0
                    except Exception as exc:
                        logger.debug(f"Telemetry audit count failed (non-critical): {exc}")
                        command_events_24h = 0
        except (pg_exceptions.ConnectionDoesNotExistError, pg_exceptions.InterfaceError, ConnectionResetError) as conn_err:
            # Pool is closing or connection was lost - this is expected during shutdown
            logger.debug(f"Telemetry: Database connection unavailable (pool closing?): {conn_err.__class__.__name__}")
            command_events_24h = 0
        except Exception:
            command_events_24h = 0

    gpt_successes_24h = _count_recent_events(gpt_metrics.recent_successes)
    gpt_errors_24h = _count_recent_events(gpt_metrics.recent_errors)

    total_activity_24h = int(command_events_24h + gpt_successes_24h + gpt_errors_24h)

    error_rate = 0.0
    if gpt_successes_24h + gpt_errors_24h > 0:
        error_rate = round(
            gpt_errors_24h / float(gpt_successes_24h + gpt_errors_24h), 2
        )

    # Safely handle latency_ms - check for NaN and None
    latency_ms_raw = bot_metrics.latency_ms
    if latency_ms_raw is None or (isinstance(latency_ms_raw, float) and math.isnan(latency_ms_raw)):
        latency_ms = 0.0
    else:
        latency_ms = float(latency_ms_raw)
    
    latency_p50 = int(latency_ms) if not math.isnan(latency_ms) else 0
    latency_p95 = int(round(latency_ms * 1.5)) if not math.isnan(latency_ms) else 0

    # throughput_per_minute should be integer according to schema
    throughput_per_minute = 0
    if total_activity_24h:
        throughput_per_minute = int(round(total_activity_24h / (24 * 60)))

    queue_depth = ticket_stats.open_count
    active_bots = len(bot_metrics.guilds) or None

    # Only count recent Grok/LLM errors (last hour) for status, not all 24h errors
    # Old errors shouldn't keep the system in degraded state
    # NOTE: Open tickets are NOT an indicator of bot health - they're normal business operations
    gpt_errors_1h = _count_recent_events(gpt_metrics.recent_errors, hours=1)
    
    # Status is based ONLY on technical health: bot online status and Grok/LLM errors
    # Open tickets are excluded - they're a normal part of ticket bot functionality
    # Use the same calculation function as debug logs for consistency
    status = _calculate_status(bot_metrics, gpt_errors_1h)

    notes = (
        f"{total_activity_24h} events/24h · {ticket_stats.open_count} open tickets · "
        f"Grok/LLM errors 24h: {gpt_errors_24h}"
    )

    try:
        from agents.telemetry import (
            collect_agent_session_metrics,
            format_agent_session_telemetry_notes,
        )

        agent_metrics = await collect_agent_session_metrics()
        agent_notes = format_agent_session_telemetry_notes(agent_metrics)
        notes = f"{notes} · {agent_notes}"
    except Exception as exc:
        logger.debug("Agent telemetry notes skipped: %s", exc)

    try:
        from utils.innersync_identity import format_identity_telemetry_notes

        notes = f"{notes} · {format_identity_telemetry_notes()}"
    except Exception as exc:
        logger.debug("Identity telemetry notes skipped: %s", exc)

    # Use Supabase REST API - this is the ONLY way to write telemetry
    # Note: Make sure 'telemetry' schema is exposed in Supabase Studio → Settings → API → Exposed Schemas
    # Send only the essential fields that we have data for, matching the database schema types:
    # - int4: uptime_seconds, throughput_per_minute, latency_p50, latency_p95, queue_depth, active_bots
    # - numeric: error_rate
    # - text: subsystem, label, status, notes
    # - timestamptz: last_updated, computed_at
    payload: dict[str, Any] = {
        "subsystem": "alphapy",
        "label": "Alphapy Agents",
        "status": status,
        "uptime_seconds": int(bot_metrics.uptime_seconds or 0),
        "throughput_per_minute": int(throughput_per_minute),
        "error_rate": float(error_rate),
        "latency_p50": int(latency_p50),
        "latency_p95": int(latency_p95),
        "last_updated": datetime.now(UTC).isoformat(),
        "computed_at": datetime.now(UTC).isoformat(),
    }
    
    # Add optional fields only if we have values
    if queue_depth is not None:
        payload["queue_depth"] = int(queue_depth)
    if active_bots is not None:
        payload["active_bots"] = int(active_bots)
    if notes:
        payload["notes"] = notes
    
    # Prefer Core-API ingress when configured; fallback to direct Supabase
    if core_ingress_module._is_ingress_configured():
        ok = await core_ingress_module.post_telemetry(payload)
        if ok:
            logger.debug("✅ Telemetry snapshot written via Core-API ingress")
            return
        logger.debug("Core ingress telemetry failed, adding to retry queue")
    else:
        try:
            await _supabase_post("subsystem_snapshots", payload, upsert=True, schema="telemetry")
            logger.debug("✅ Telemetry snapshot written to Supabase via REST API")
            return
        except SupabaseConfigurationError as exc:
            logger.warning(
                f"⚠️ Cannot write telemetry to Supabase: {exc}. "
                "Ensure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are configured."
            )
            return
        except Exception as exc:
            logger.warning(
                f"⚠️ Failed to write telemetry to Supabase: {exc.__class__.__name__}: {exc}. "
                "Adding to retry queue."
            )

    global _telemetry_queue
    if len(_telemetry_queue) < MAX_TELEMETRY_QUEUE_SIZE:
        _telemetry_queue.append({
            "payload": payload,
            "retry_count": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        })
    else:
        _telemetry_queue.pop(0)
        _telemetry_queue.append({
            "payload": payload,
            "retry_count": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        })


def _cleanup_command_stats_cache() -> None:
    """Clean up expired entries from command stats cache."""
    global _command_stats_cache
    now = datetime.now(UTC)
    expired_keys = [
        k for k, (_, cached_at) in _command_stats_cache.items()
        if (now - cached_at).total_seconds() >= COMMAND_STATS_CACHE_TTL
    ]
    for key in expired_keys:
        del _command_stats_cache[key]
    
    # Enforce max size
    if len(_command_stats_cache) > MAX_COMMAND_STATS_CACHE_SIZE:
        # Remove oldest entries
        sorted_by_age = sorted(
            _command_stats_cache.items(),
            key=lambda x: x[1][1]  # Sort by cached_at timestamp
        )
        excess = len(_command_stats_cache) - MAX_COMMAND_STATS_CACHE_SIZE
        for key, _ in sorted_by_age[:excess]:
            del _command_stats_cache[key]
        logger.debug(f"Command stats cache: Evicted {excess} oldest entries, size now: {len(_command_stats_cache)}")


async def _fetch_command_stats(guild_id: int | None = None, days: int = 7, limit: int = 10) -> CommandStats | None:
    """Fetch command usage statistics for dashboard (with TTL cache)."""
    global _command_stats_cache
    
    cache_key = (guild_id, days, limit)
    now = datetime.now(UTC)
    
    # Check cache
    if cache_key in _command_stats_cache:
        stats, cached_at = _command_stats_cache[cache_key]
        if (now - cached_at).total_seconds() < COMMAND_STATS_CACHE_TTL:
            logger.debug(f"Command stats cache HIT: {cache_key}")
            return stats
    
    # Cache miss or expired - fetch from DB
    if _pool() is None:
        return None
    
    try:
        async with _pool().acquire() as conn:
            where_clause = "WHERE created_at >= NOW() - ($1 || ' days')::INTERVAL"
            params: list[Any] = [str(days)]
            
            if guild_id is not None:
                where_clause += " AND guild_id = $2"
                params.append(guild_id)
            
            # Get top commands
            command_rows = await conn.fetch(
                f"""
                SELECT command_name, COUNT(*) as usage_count
                FROM audit_logs
                {where_clause}
                GROUP BY command_name
                ORDER BY usage_count DESC
                LIMIT ${len(params) + 1}
                """,
                *params,
                limit
            )
            
            # Get total count for 24h
            total_24h_params: list[Any] = ["1"]
            total_24h_where = "WHERE created_at >= NOW() - interval '24 hours'"
            if guild_id is not None:
                total_24h_where += " AND guild_id = $2"
                total_24h_params.append(guild_id)
            
            total_24h = await conn.fetchval(
                f"SELECT COUNT(*) FROM audit_logs {total_24h_where}",
                *total_24h_params
            ) or 0
            
            top_commands = [
                CommandUsage(command_name=row["command_name"], usage_count=row["usage_count"])
                for row in command_rows
            ]
            
            result = CommandStats(
                top_commands=top_commands,
                total_commands_24h=int(total_24h),
                period_days=days
            )
            
            # Clean expired entries and enforce size limit before adding new entry
            _cleanup_command_stats_cache()
            _command_stats_cache[cache_key] = (result, now)
            logger.debug(f"Command stats cache MISS: {cache_key}, size={len(_command_stats_cache)}")
            
            return result
    except pg_exceptions.UndefinedTableError:
        # Table doesn't exist yet - return None (non-critical)
        return None
    except Exception as exc:
        logger.debug(f"Command stats fetch failed (non-critical): {exc}")
        return None


def _collect_cache_metrics() -> CacheMetrics:
    """Collect cache size metrics for monitoring."""
    # Command tracker queue size
    try:
        from utils.command_tracker import _command_queue
        command_tracker_size = len(_command_queue)
    except Exception:
        command_tracker_size = 0
    
    # Command stats cache size
    command_stats_size = len(_command_stats_cache)
    
    # IP rate limits size
    ip_rate_limits_size = len(_ip_rate_limits)
    
    # Sync cooldowns size
    try:
        from utils.command_sync import _sync_cooldowns
        sync_cooldowns_size = len(_sync_cooldowns)
    except Exception:
        sync_cooldowns_size = 0
    
    # Ticket cooldowns size (need to get from bot instance or track globally)
    # For now, we'll track this separately if needed
    ticket_cooldowns_size = 0  # TODO: Add global tracking if needed

    automod_cache_stats: dict[str, int] = {}
    try:
        from gpt.helpers import bot_instance
    except Exception:
        bot_instance = None
    bot = bot_instance
    if bot:
        try:
            cog = bot.get_cog("Configuration")
            rule_processor = getattr(cog, "rule_processor", None) if cog else None
            if rule_processor and hasattr(rule_processor, "get_cache_stats"):
                automod_cache_stats = rule_processor.get_cache_stats()
        except Exception:
            automod_cache_stats = {}

    try:
        from cogs.engagement import get_engagement_cache_stats

        engagement_cache_stats = get_engagement_cache_stats()
    except Exception:
        engagement_cache_stats = {}
    
    return CacheMetrics(
        command_tracker_queue_size=command_tracker_size,
        command_stats_cache_size=command_stats_size,
        ip_rate_limits_size=ip_rate_limits_size,
        sync_cooldowns_size=sync_cooldowns_size,
        ticket_cooldowns_size=ticket_cooldowns_size,
        automod_rules_cache_size=automod_cache_stats.get("automod_rules_cache_size", 0),
        automod_rules_list_cache_size=automod_cache_stats.get("automod_rules_list_cache_size", 0),
        automod_rules_cache_hits=automod_cache_stats.get("automod_rules_cache_hits", 0),
        automod_rules_cache_misses=automod_cache_stats.get("automod_rules_cache_misses", 0),
        engagement_feature_flag_cache_size=engagement_cache_stats.get("engagement_feature_flag_cache_size", 0),
        engagement_food_channels_cache_size=engagement_cache_stats.get("engagement_food_channels_cache_size", 0),
        engagement_feature_flag_cache_hits=engagement_cache_stats.get("engagement_feature_flag_cache_hits", 0),
        engagement_feature_flag_cache_misses=engagement_cache_stats.get("engagement_feature_flag_cache_misses", 0),
        engagement_food_channels_cache_hits=engagement_cache_stats.get("engagement_food_channels_cache_hits", 0),
        engagement_food_channels_cache_misses=engagement_cache_stats.get("engagement_food_channels_cache_misses", 0),
    )


def _collect_premium_metrics() -> PremiumMetrics | None:
    """Collect premium guard stats when running in same process (e.g. bot + API)."""
    try:
        from utils.premium_guard import get_premium_guard_stats
        stats = get_premium_guard_stats()
        return PremiumMetrics(
            premium_checks_total=stats.get("premium_checks_total", 0),
            premium_checks_core_api=stats.get("premium_checks_core_api", 0),
            premium_checks_local=stats.get("premium_checks_local", 0),
            premium_cache_hits=stats.get("premium_cache_hits", 0),
            premium_transfers_count=stats.get("premium_transfers_count", 0),
            premium_cache_size=stats.get("premium_cache_size", 0),
            premium_guild_cache_size=stats.get("premium_guild_cache_size", 0),
            premium_guild_cache_hits=stats.get("premium_guild_cache_hits", 0),
            premium_guild_cache_misses=stats.get("premium_guild_cache_misses", 0),
        )
    except Exception:
        return None


def _collect_identity_metrics() -> IdentityMetrics | None:
    try:
        from utils.innersync_identity import get_identity_stats

        stats = get_identity_stats()
        return IdentityMetrics(**{k: int(stats.get(k, 0)) for k in IdentityMetrics.model_fields})
    except Exception:
        return None


@metrics_router.get("/dashboard/metrics", response_model=DashboardMetrics)
async def get_dashboard_metrics(
    guild_id: int | None = None,
    auth_user_id: str = Depends(_lazy_get_authenticated_user_id)
):
    snapshot = await get_bot_snapshot()
    bot_payload = serialize_snapshot(snapshot)
    bot_metrics = BotMetrics(
        version=__version__,
        codename=CODENAME,
        **bot_payload,
    )
    gpt_metrics = _collect_gpt_metrics()
    
    # Use MAIN_GUILD_ID as default if no guild_id is specified
    effective_guild_id = guild_id
    if effective_guild_id is None and hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        effective_guild_id = config.MAIN_GUILD_ID
        logger.debug(f"📊 Dashboard metrics: Using MAIN_GUILD_ID ({effective_guild_id}) as default (no guild_id provided)")
    elif effective_guild_id is not None:
        logger.debug(f"📊 Dashboard metrics: Using provided guild_id ({effective_guild_id})")
    
    # Guild filtering implemented for security - only shows data for specified guild (or main guild by default)
    reminder_stats = await _fetch_reminder_stats(effective_guild_id)
    ticket_stats = await _fetch_ticket_stats(effective_guild_id)
    infrastructure = await _collect_infrastructure_metrics()
    command_stats = await _fetch_command_stats(effective_guild_id)
    cache_metrics = _collect_cache_metrics()
    premium_metrics = _collect_premium_metrics()
    identity_metrics = _collect_identity_metrics()
    agent_sessions_payload: AgentSessionMetricsPayload | None = None
    try:
        from agents.telemetry import collect_agent_session_metrics

        agent_metrics = await collect_agent_session_metrics()
        agent_sessions_payload = AgentSessionMetricsPayload(
            enabled=agent_metrics.agents_enabled,
            active_sessions=agent_metrics.active_sessions,
            started_24h=agent_metrics.started_24h,
            completed_24h=agent_metrics.completed_24h,
            active_origin_discord=agent_metrics.active_origin_discord,
            active_origin_app=agent_metrics.active_origin_app,
        )
    except Exception as exc:
        logger.debug("Agent session metrics for dashboard skipped: %s", exc)

    # Persist a telemetry snapshot asynchronously; ignore failures.
    asyncio.create_task(_persist_telemetry_snapshot(bot_metrics, gpt_metrics, ticket_stats))

    return DashboardMetrics(
        bot=bot_metrics,
        gpt=gpt_metrics,
        reminders=reminder_stats,
        tickets=ticket_stats,
        # Guild filtering implemented for security - only shows settings for specified guild (or main guild by default)
        settings_overrides=await _fetch_settings_overrides(effective_guild_id),
        infrastructure=infrastructure,
        command_usage=command_stats,
        cache_metrics=cache_metrics,
        premium_metrics=premium_metrics,
        identity_metrics=identity_metrics,
        agent_sessions=agent_sessions_payload,
    )


# Alias for Mind monitoring system - expects /api/metrics
@metrics_router.get("/metrics", response_model=DashboardMetrics)
async def get_metrics(
    guild_id: int | None = None,
    auth_user_id: str = Depends(_lazy_get_authenticated_user_id)
):
    """Alias endpoint for Mind monitoring system."""
    return await get_dashboard_metrics(guild_id, auth_user_id)


