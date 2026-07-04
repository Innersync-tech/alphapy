import asyncio
import math
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import asyncpg
from asyncpg import exceptions as pg_exceptions
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

import config
from agents.http_routes import include_agent_routes
from cogs.reminders import (
    create_reminder,
    delete_reminder,
    get_reminders_for_user,
    update_reminder,
)
from utils import core_ingress as core_ingress_module
from utils.hermit_context import get_hermit_context_stats
from utils.logger import get_gpt_status_logs, logger
from utils.operational_logs import EventType, get_operational_events, log_operational_event
from utils.runtime_metrics import get_bot_snapshot, serialize_snapshot
from utils.supabase_auth import verify_supabase_token
from utils.supabase_client import SupabaseConfigurationError, _supabase_post
from utils.timezone import BRUSSELS_TZ
from version import CODENAME, __version__
from webhooks.app_reflections import router as app_reflections_webhook_router
from webhooks.discord_link import router as discord_link_webhook_router
from webhooks.founder import router as founder_webhook_router
from webhooks.legal_update import router as legal_update_webhook_router
from webhooks.premium_invalidate import router as premium_invalidate_webhook_router
from webhooks.reflections import router as reflections_webhook_router
from webhooks.revoke_reflection import router as revoke_reflection_webhook_router
from webhooks.supabase import router as supabase_webhook_router

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


async def verify_api_key(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> None:
    """Guard routes with a Supabase JWT or optional API key."""
    claims: dict[str, Any] | None = None

    if authorization:
        try:
            claims = await verify_supabase_token(authorization)
        except HTTPException:
            # Invalid JWT; fall back to API key check if configured.
            claims = None

    if claims:
        request.state.supabase_claims = claims
        return

    configured_key = getattr(config, "API_KEY", None)
    if configured_key:
        if x_api_key != configured_key:
            raise HTTPException(status_code=401, detail="Unauthorized")
        request.state.supabase_claims = None
        return

    # No Supabase claims and no API key configured — allow anonymous access.
    request.state.supabase_claims = None


async def require_observability_api_key(
    x_api_key: str | None = Header(None),
) -> None:
    """Require service API key for internal observability endpoint access."""
    configured_key = getattr(config, "API_KEY", None)
    if not configured_key:
        raise HTTPException(
            status_code=503,
            detail="Observability endpoint unavailable: API key is not configured",
        )
    if x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def get_authenticated_user_id(
    request: Request,
    authorization: str | None = Header(None),
) -> str:
    """Extract authenticated user ID from a verified Supabase JWT."""
    claims = getattr(request.state, "supabase_claims", None)

    if not claims and authorization:
        try:
            claims = await verify_supabase_token(authorization)
        except HTTPException:
            claims = None

    if claims and "sub" in claims:
        return str(claims["sub"])

    raise HTTPException(status_code=401, detail="Missing authentication context")


async def _require_discord_id_for_linked_innersync(innersync_sub: str) -> int:
    """
    Resolve Supabase JWT `sub` to a Discord snowflake using `alphapy_discord_links`
    (and Supabase profile fallback). Fails closed if nothing is linked.
    """
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    from utils.innersync_identity import resolve_innersync_jwt_sub_to_discord_int

    did = await resolve_innersync_jwt_sub_to_discord_int(db_pool, innersync_sub)
    if did is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "No Discord account linked to this Innersync user. "
                "Run /link in the Alphapy Discord server to connect your account."
            ),
        )
    return did


# ---------------------------------------------------------------------------
# FastAPI app bootstrap
# ---------------------------------------------------------------------------

db_pool: asyncpg.Pool | None = None
router = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])

# Telemetry retry queue
_telemetry_queue: list[dict[str, Any]] = []
MAX_TELEMETRY_QUEUE_SIZE = 100
MAX_TELEMETRY_RETRIES = 5

# In-memory IP-based rate limiter
_ip_rate_limits: dict[str, list[float]] = defaultdict(list)
MAX_IP_ENTRIES = 1000
RATE_LIMIT_CLEANUP_INTERVAL = 600  # 10 minutes

# API observability counters (single-process, rolling windows)
_api_latencies_ms: deque[float] = deque(maxlen=2000)
_webhook_latencies_ms: deque[float] = deque(maxlen=2000)
_api_total_requests = 0
_api_success_requests = 0
_webhook_total_requests = 0
_webhook_success_requests = 0

# In-memory idempotency store for write endpoints
_idempotency_cache: dict[str, tuple[float, dict[str, Any]]] = {}
IDEMPOTENCY_TTL_SECONDS = 600


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(ordered[int(k)])
    return float(ordered[f] * (c - k) + ordered[c] * (k - f))


def _record_observability(path: str, status_code: int, latency_ms: float) -> None:
    global _api_total_requests, _api_success_requests, _webhook_total_requests, _webhook_success_requests
    is_webhook = path.startswith("/webhooks/")
    success = status_code < 500
    if is_webhook:
        _webhook_total_requests += 1
        if success:
            _webhook_success_requests += 1
        _webhook_latencies_ms.append(latency_ms)
    else:
        _api_total_requests += 1
        if success:
            _api_success_requests += 1
        _api_latencies_ms.append(latency_ms)


def _cleanup_idempotency_cache() -> None:
    now = time.time()
    stale = [key for key, (expires_at, _) in _idempotency_cache.items() if expires_at <= now]
    for key in stale:
        _idempotency_cache.pop(key, None)


def _idempotency_cache_key(namespace: str, auth_user_id: str, raw_key: str) -> str:
    return f"{namespace}:{auth_user_id}:{raw_key.strip()}"

# Command stats TTL cache (initialized after CommandStats class definition)
_command_stats_cache: dict[tuple[int | None, int, int], tuple[Any, datetime]] = {}
MAX_COMMAND_STATS_CACHE_SIZE = 50
COMMAND_STATS_CACHE_TTL = 30  # seconds


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global db_pool
    db_pool = await asyncpg.create_pool(config.DATABASE_URL)
    app.state.db_pool = db_pool
    logger.info("✅ DB pool created")
    
    # Warn if API is running without any authentication configured
    _api_key_set = bool(getattr(config, "API_KEY", None))
    _supabase_configured = bool(getattr(config, "SUPABASE_URL", None))
    if not _api_key_set and not _supabase_configured:
        logger.warning(
            "⚠️  API running in UNAUTHENTICATED mode — API_KEY and SUPABASE_URL are both unset. "
            "All /api/* routes are publicly accessible. Set API_KEY or SUPABASE_URL to require auth."
        )

    # Warn about unset webhook secrets — missing secrets mean those endpoints
    # accept unauthenticated requests, which is a security risk in production.
    # Note: we log only the env var *name*, never its value.
    if not getattr(config, "SUPABASE_WEBHOOK_SECRET", None):
        logger.warning("⚠️  SUPABASE_WEBHOOK_SECRET is not set — Supabase auth/GDPR erasure webhook is unauthenticated.")
    if not getattr(config, "PREMIUM_INVALIDATE_WEBHOOK_SECRET", None):
        logger.warning("⚠️  PREMIUM_INVALIDATE_WEBHOOK_SECRET is not set — premium invalidation webhook is unauthenticated.")
    if not getattr(config, "APP_REFLECTIONS_WEBHOOK_SECRET", None):
        logger.warning("⚠️  APP_REFLECTIONS_WEBHOOK_SECRET is not set — reflections sync webhook is unauthenticated.")
    if not getattr(config, "FOUNDER_WEBHOOK_SECRET", None):
        logger.warning("⚠️  FOUNDER_WEBHOOK_SECRET is not set — founder DM webhook is unauthenticated.")
    if not getattr(config, "LEGAL_UPDATE_WEBHOOK_SECRET", None):
        logger.warning("⚠️  LEGAL_UPDATE_WEBHOOK_SECRET is not set — legal update webhook is unauthenticated.")
    if not getattr(config, "DISCORD_LINK_WEBHOOK_SECRET", None):
        logger.warning("⚠️  DISCORD_LINK_WEBHOOK_SECRET is not set — Discord/Innersync link webhook is unauthenticated.")

    strict_security_mode = os.getenv("STRICT_SECURITY_MODE", "0") == "1"
    is_production = os.getenv("APP_ENV", "development").lower() in {"prod", "production"}
    if strict_security_mode and is_production:
        missing = []
        if not _api_key_set and not _supabase_configured:
            missing.append("API_KEY or SUPABASE_URL")
        for key in (
            "SUPABASE_WEBHOOK_SECRET",
            "PREMIUM_INVALIDATE_WEBHOOK_SECRET",
            "APP_REFLECTIONS_WEBHOOK_SECRET",
            "FOUNDER_WEBHOOK_SECRET",
            "LEGAL_UPDATE_WEBHOOK_SECRET",
            "DISCORD_LINK_WEBHOOK_SECRET",
        ):
            if not getattr(config, key, None):
                missing.append(key)
        if missing:
            raise RuntimeError(
                "STRICT_SECURITY_MODE failed. Missing required production security configuration: "
                + ", ".join(missing)
            )

    # Log MAIN_GUILD_ID configuration
    if hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        logger.info(f"🏠 MAIN_GUILD_ID configured: {config.MAIN_GUILD_ID} (API endpoints will filter to this guild by default)")
    else:
        logger.info("🌐 MAIN_GUILD_ID not configured (API endpoints will show data from all guilds)")
    
    # Database schema is managed via Alembic migrations; startup only validates connectivity.
    # Note: Command tracker is initialized in bot.py on_ready() event
    # to ensure it uses the bot's event loop, not FastAPI's event loop
    
    # Start background telemetry ingest task
    ingest_interval = getattr(config, "TELEMETRY_INGEST_INTERVAL", 45)
    ingest_task = asyncio.create_task(_telemetry_ingest_loop(ingest_interval))
    
    # Start IP rate limits cleanup task
    rate_limit_cleanup_task = asyncio.create_task(_cleanup_rate_limits_loop())
    
    try:
        yield
    finally:
        # Cancel and wait for the background tasks to finish gracefully
        logger.info("🛑 Shutting down background tasks...")
        
        # Cancel telemetry task
        ingest_task.cancel()
        try:
            await asyncio.wait_for(ingest_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.debug(f"Telemetry task exception during shutdown (expected): {exc.__class__.__name__}")
        
        # Cancel rate limit cleanup task
        rate_limit_cleanup_task.cancel()
        try:
            await asyncio.wait_for(rate_limit_cleanup_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.debug(f"Rate limit cleanup task exception during shutdown (expected): {exc.__class__.__name__}")
        
        # Close database pool after tasks are done
        if db_pool:
            try:
                await db_pool.close()
                logger.info("🔌 DB pool closed")
            except Exception as exc:
                logger.debug(f"Error closing DB pool (expected during shutdown): {exc.__class__.__name__}")


app = FastAPI(lifespan=lifespan)
app.include_router(supabase_webhook_router)
app.include_router(reflections_webhook_router)
app.include_router(app_reflections_webhook_router)
app.include_router(revoke_reflection_webhook_router)
app.include_router(premium_invalidate_webhook_router)
app.include_router(discord_link_webhook_router)
app.include_router(founder_webhook_router)
app.include_router(legal_update_webhook_router)

# CORS settings
_allowed_origins = getattr(config, "ALLOWED_ORIGINS", [])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins if _allowed_origins else ["http://localhost:3000"],
    allow_credentials=bool(_allowed_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# IP-based Rate Limiting Middleware
# ---------------------------------------------------------------------------

class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach request id and collect simple latency/success metrics."""

    async def dispatch(self, request: StarletteRequest, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000
        _record_observability(request.url.path, response.status_code, latency_ms)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP-based rate limiting middleware for API endpoints."""
    
    async def dispatch(self, request: StarletteRequest, call_next):
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Clean old entries (older than 1 minute)
        current_time = time.time()
        _ip_rate_limits[client_ip] = [
            ts for ts in _ip_rate_limits[client_ip]
            if current_time - ts < 60.0
        ]

        # Health/metrics get a generous limit; write endpoints are stricter
        is_health = (
            request.url.path.startswith("/health")
            or request.url.path.startswith("/metrics")
            or request.url.path == "/status"
        )
        if is_health:
            limit = 60  # 60 requests/min for health probes
        elif request.method in ["POST", "PUT", "DELETE"]:
            limit = 10  # 10 writes/min per IP
        else:
            limit = 30  # 30 reads/min per IP

        if len(_ip_rate_limits[client_ip]) >= limit:
            return Response(
                content=f'{{"detail": "Rate limit exceeded. Max {limit} requests per minute per IP."}}',
                status_code=429,
                media_type="application/json"
            )

        # Record this request
        _ip_rate_limits[client_ip].append(current_time)

        return await call_next(request)


app.add_middleware(RequestObservabilityMiddleware)

# Apply rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# ---------------------------------------------------------------------------
# Simple status endpoints (kept for backwards compat)
# ---------------------------------------------------------------------------

startup_time = time.time()


class HealthStatus(BaseModel):
    service: str
    version: str
    uptime_seconds: int
    db_status: str
    timestamp: str
    guild_count: int | None = None
    active_commands_24h: int | None = None
    gpt_status: str | None = None
    database_pool_size: int | None = None


@app.get("/api/health", response_model=HealthStatus, include_in_schema=False)
async def health_check() -> HealthStatus:
    uptime_seconds = int(time.time() - startup_time)
    db_status = "not_initialized"
    guild_count: int | None = None
    active_commands_24h: int | None = None
    gpt_status: str | None = None
    database_pool_size: int | None = None

    # Check database status
    if db_pool:
        try:
            async with db_pool.acquire() as connection:
                await connection.execute("SELECT 1")
            db_status = "ok"
            try:
                database_pool_size = db_pool.get_size()
            except Exception:
                pass
        except Exception as error:
            db_status = f"error:{error.__class__.__name__}"
    
    # Get bot metrics if available
    try:
        snapshot = await get_bot_snapshot()
        if snapshot:
            guild_count = len(snapshot.guilds)
    except Exception:
        pass  # Non-critical - bot might not be ready
    
    # Get Grok/LLM status
    try:
        gpt_logs = get_gpt_status_logs()
        if gpt_logs.error_count > 0 and gpt_logs.success_count == 0:
            gpt_status = "error"
        elif gpt_logs.error_count > 5:
            gpt_status = "degraded"
        else:
            gpt_status = "operational"
    except Exception:
        pass
    
    # Get command usage count (24h)
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                try:
                    active_commands_24h = await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM audit_logs
                        WHERE created_at >= NOW() - interval '24 hours'
                        """
                    ) or 0
                except pg_exceptions.UndefinedTableError:
                    active_commands_24h = None  # Table doesn't exist yet
        except Exception:
            pass
    
    # Structured logging
    health_data = {
        "service": config.SERVICE_NAME,
        "version": __version__,
        "uptime_seconds": uptime_seconds,
        "db_status": db_status,
        "guild_count": guild_count,
        "active_commands_24h": active_commands_24h,
        "gpt_status": gpt_status,
        "database_pool_size": database_pool_size,
        "timestamp": datetime.now(UTC).isoformat()
    }
    logger.info(f"Health check: {health_data}")

    # Persist health check to history table (non-blocking)
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO health_check_history 
                    (service, version, uptime_seconds, db_status, guild_count, active_commands_24h, gpt_status, database_pool_size)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    config.SERVICE_NAME,
                    __version__,
                    uptime_seconds,
                    db_status,
                    guild_count,
                    active_commands_24h,
                    gpt_status,
                    database_pool_size
                )
        except pg_exceptions.UndefinedTableError:
            pass  # Table doesn't exist yet - non-critical
        except Exception as e:
            logger.debug(f"Failed to persist health check history (non-critical): {e}")

    return HealthStatus(
        service=config.SERVICE_NAME,
        version=__version__,
        uptime_seconds=uptime_seconds,
        db_status=db_status,
        timestamp=datetime.now(UTC).isoformat(),
        guild_count=guild_count,
        active_commands_24h=active_commands_24h,
        gpt_status=gpt_status,
        database_pool_size=database_pool_size,
    )


@app.get("/status")
def get_status():
    return {
        "online": True,
        "latency": 0,
        "uptime": f"{int((time.time() - startup_time) // 60)} min",
    }


@app.get(
    "/api/observability",
    include_in_schema=False,
    dependencies=[Depends(require_observability_api_key)],
)
def get_observability() -> dict[str, Any]:
    api_success_rate = (_api_success_requests / _api_total_requests) if _api_total_requests else 1.0
    webhook_success_rate = (_webhook_success_requests / _webhook_total_requests) if _webhook_total_requests else 1.0
    return {
        "api": {
            "requests": _api_total_requests,
            "success_rate": round(api_success_rate, 4),
            "latency_ms": {
                "p50": round(_percentile(list(_api_latencies_ms), 0.50), 2),
                "p95": round(_percentile(list(_api_latencies_ms), 0.95), 2),
                "p99": round(_percentile(list(_api_latencies_ms), 0.99), 2),
            },
        },
        "webhooks": {
            "requests": _webhook_total_requests,
            "success_rate": round(webhook_success_rate, 4),
            "latency_ms": {
                "p50": round(_percentile(list(_webhook_latencies_ms), 0.50), 2),
                "p95": round(_percentile(list(_webhook_latencies_ms), 0.95), 2),
                "p99": round(_percentile(list(_webhook_latencies_ms), 0.99), 2),
            },
        },
        "hermit_context": get_hermit_context_stats(),
    }


@app.get("/api/health/history")
async def get_health_history(
    hours: int = 24,
    limit: int = 100
) -> dict[str, Any]:
    """
    Get health check history for trend analysis.
    
    Args:
        hours: Number of hours to look back (default: 24)
        limit: Maximum number of records to return (default: 100)
    
    Returns:
        Dictionary with health check history records
    """
    global db_pool
    if db_pool is None:
        return {"error": "Database not initialized"}
    
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    service,
                    version,
                    uptime_seconds,
                    db_status,
                    guild_count,
                    active_commands_24h,
                    gpt_status,
                    database_pool_size,
                    checked_at
                FROM health_check_history
                WHERE checked_at >= NOW() - ($1 || ' hours')::INTERVAL
                ORDER BY checked_at DESC
                LIMIT $2
                """,
                str(hours),
                limit
            )
            
            history = []
            for row in rows:
                history.append({
                    "service": row["service"],
                    "version": row["version"],
                    "uptime_seconds": row["uptime_seconds"],
                    "db_status": row["db_status"],
                    "guild_count": row["guild_count"],
                    "active_commands_24h": row["active_commands_24h"],
                    "gpt_status": row["gpt_status"],
                    "database_pool_size": row["database_pool_size"],
                    "checked_at": row["checked_at"].isoformat() if row["checked_at"] else None
                })
            
            return {
                "history": history,
                "period_hours": hours,
                "total_records": len(history)
            }
    except pg_exceptions.UndefinedTableError:
        return {"error": "health_check_history table not initialized"}
    except Exception as exc:
        logger.error(f"Failed to get health history: {exc}")
        return {"error": "Failed to fetch health history"}


@app.get("/top-commands")
async def get_top_commands(
    guild_id: int | None = None,
    days: int = 7,
    limit: int = 10
) -> dict[str, Any]:
    """
    Get top commands by usage.
    
    Args:
        guild_id: Optional guild ID to filter by (None = uses MAIN_GUILD_ID if configured, otherwise all guilds)
        days: Number of days to look back (default: 7)
        limit: Maximum number of commands to return (default: 10)
    
    Returns:
        Dictionary with command names and usage counts
    """
    global db_pool
    if db_pool is None:
        return {"error": "Database not initialized"}
    
    # Use MAIN_GUILD_ID as default if no guild_id is specified
    effective_guild_id = guild_id
    if effective_guild_id is None and hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        effective_guild_id = config.MAIN_GUILD_ID
        logger.debug(f"📈 Top commands: Using MAIN_GUILD_ID ({effective_guild_id}) as default (no guild_id provided)")
    elif effective_guild_id is not None:
        logger.debug(f"📈 Top commands: Using provided guild_id ({effective_guild_id})")
    
    try:
        async with db_pool.acquire() as conn:
            where_clause = "WHERE created_at >= NOW() - ($1 || ' days')::INTERVAL"
            params: list[Any] = [str(days)]
            
            if effective_guild_id is not None:
                where_clause += " AND guild_id = $2"
                params.append(effective_guild_id)
            
            rows = await conn.fetch(
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
            
            result = {row["command_name"]: row["usage_count"] for row in rows}
            return {
                "commands": result,
                "period_days": days,
                "guild_id": effective_guild_id,
                "total_commands": len(result)
            }
    except Exception as exc:
        logger.error(f"Failed to get top commands: {exc}")
        return {"error": "Failed to fetch top commands"}


# ---------------------------------------------------------------------------
# Helper utilities for dashboard payloads
# ---------------------------------------------------------------------------


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


class CommandUsage(BaseModel):
    command_name: str
    usage_count: int


class CommandStats(BaseModel):
    top_commands: list[CommandUsage]
    total_commands_24h: int
    period_days: int


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
    global db_pool
    if db_pool is None:
        return default
    try:
        async with db_pool.acquire() as conn:
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
    global db_pool
    if db_pool is None or db_pool.is_closing():
        return default
    try:
        async with db_pool.acquire() as conn:
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
    global db_pool
    if db_pool is None:
        return []
    try:
        async with db_pool.acquire() as conn:
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
    global db_pool
    if db_pool is None:
        return InfrastructureMetrics(database_up=False, pool_size=None, checked_at=checked_at)

    database_up = False
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("SELECT 1;")
            database_up = True
    except Exception as exc:
        logger.warning(f"[WARN] db health check failed: {exc}")

    pool_size: int | None
    try:
        pool_size = db_pool.get_size()
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
            global db_pool
            if db_pool is None or db_pool.is_closing():
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


async def _cleanup_rate_limits_loop() -> None:
    """Background task that periodically cleans up IP rate limits dictionary."""
    global _ip_rate_limits
    
    logger.info(f"🚀 IP rate limits cleanup loop started (interval: {RATE_LIMIT_CLEANUP_INTERVAL}s)")
    
    while True:
        try:
            await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL)
            
            current_time = time.time()
            initial_size = len(_ip_rate_limits)
            keys_to_remove = []
            
            # Remove entries with empty lists or old data
            for ip, timestamps in list(_ip_rate_limits.items()):
                # Clean timestamps older than 1 minute
                _ip_rate_limits[ip] = [ts for ts in timestamps if current_time - ts < 60.0]
                # Remove if list is empty
                if not _ip_rate_limits[ip]:
                    keys_to_remove.append(ip)
            
            for key in keys_to_remove:
                del _ip_rate_limits[key]
            
            # Enforce max dict size (LRU eviction - remove oldest entries)
            if len(_ip_rate_limits) > MAX_IP_ENTRIES:
                excess = len(_ip_rate_limits) - MAX_IP_ENTRIES
                # Remove first N entries (simplified LRU)
                for _ in range(excess):
                    if _ip_rate_limits:
                        _ip_rate_limits.pop(next(iter(_ip_rate_limits)), None)
            
            final_size = len(_ip_rate_limits)
            if initial_size != final_size or final_size > MAX_IP_ENTRIES * 0.8:
                logger.debug(f"Rate limits cleanup: {initial_size} -> {final_size} active IPs (max: {MAX_IP_ENTRIES})")
        
        except asyncio.CancelledError:
            logger.info("🛑 IP rate limits cleanup loop cancelled")
            raise
        except Exception as e:
            logger.error(f"❌ Error in IP rate limits cleanup loop: {e}", exc_info=True)
            await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL)  # Wait before retrying


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
    global db_pool
    
    # Use MAIN_GUILD_ID for telemetry if configured
    main_guild_id = None
    if hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        main_guild_id = config.MAIN_GUILD_ID
    
    if db_pool:
        try:
            # Check if pool is closed before acquiring
            if db_pool.is_closing():
                command_events_24h = 0
            else:
                async with db_pool.acquire() as conn:
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
    global db_pool, _command_stats_cache
    
    cache_key = (guild_id, days, limit)
    now = datetime.now(UTC)
    
    # Check cache
    if cache_key in _command_stats_cache:
        stats, cached_at = _command_stats_cache[cache_key]
        if (now - cached_at).total_seconds() < COMMAND_STATS_CACHE_TTL:
            logger.debug(f"Command stats cache HIT: {cache_key}")
            return stats
    
    # Cache miss or expired - fetch from DB
    if db_pool is None:
        return None
    
    try:
        async with db_pool.acquire() as conn:
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


@router.get("/dashboard/metrics", response_model=DashboardMetrics)
async def get_dashboard_metrics(
    guild_id: int | None = None,
    auth_user_id: str = Depends(get_authenticated_user_id)
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
    )


# Alias for Mind monitoring system - expects /api/metrics
@router.get("/metrics", response_model=DashboardMetrics)
async def get_metrics(
    guild_id: int | None = None,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Alias endpoint for Mind monitoring system."""
    return await get_dashboard_metrics(guild_id, auth_user_id)


# ---------------------------------------------------------------------------
# Reminder REST endpoints (existing behaviour)
# ---------------------------------------------------------------------------


class Reminder(BaseModel):
    id: int
    name: str
    time: str
    days: list[str]
    message: str
    channel_id: int
    user_id: str  # JWT `sub` in requests; DB stores Discord snowflake in `created_by` after link resolution


@router.get("/reminders/{user_id}", response_model=list[Reminder])
async def get_user_reminders(
    user_id: str, 
    guild_id: int | None = None,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """
    Get reminders for a specific user.
    
    Users can only access their own reminders unless they are admins.
    Guild filtering is recommended for multi-guild support.
    Uses MAIN_GUILD_ID as default if no guild_id is specified.
    """
    if auth_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    discord_uid = await _require_discord_id_for_linked_innersync(auth_user_id)
    
    # Use MAIN_GUILD_ID as default if no guild_id is specified
    effective_guild_id = guild_id
    if effective_guild_id is None and hasattr(config, "MAIN_GUILD_ID") and config.MAIN_GUILD_ID:
        effective_guild_id = config.MAIN_GUILD_ID
        logger.debug(f"📅 Reminders for user {user_id}: Using MAIN_GUILD_ID ({effective_guild_id}) as default (no guild_id provided)")
    elif effective_guild_id is not None:
        logger.debug(f"📅 Reminders for user {user_id}: Using provided guild_id ({effective_guild_id})")
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        async with db_pool.acquire() as conn:
            rows = await get_reminders_for_user(cast(Any, conn), discord_uid, effective_guild_id)
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "time": r["time"].strftime("%H:%M") if r.get("time") else None,
                "call_time": r["call_time"].strftime("%H:%M") if r.get("call_time") else None,
                "days": r["days"],
                "message": r["message"],
                "channel_id": r["channel_id"],
                "location": r.get("location"),
                "event_time": r["event_time"].isoformat() if r.get("event_time") else None,
                "user_id": str(r["created_by"]),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"[ERROR] Failed to get reminders: {exc}")
        return []


@router.post("/reminders")
async def add_reminder(
    reminder: Reminder,
    request: Request,
    auth_user_id: str = Depends(get_authenticated_user_id),
):
    global db_pool
    payload = reminder.dict()
    payload["created_by"] = payload.pop("user_id")
    if str(payload["created_by"]) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    discord_uid = await _require_discord_id_for_linked_innersync(auth_user_id)
    payload["created_by"] = discord_uid
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    idem_key = request.headers.get("Idempotency-Key")
    cache_key = None
    if idem_key:
        _cleanup_idempotency_cache()
        cache_key = _idempotency_cache_key("add_reminder", auth_user_id, idem_key)
        cached = _idempotency_cache.get(cache_key)
        if cached:
            return cached[1]

    async with db_pool.acquire() as conn:
        await create_reminder(cast(Any, conn), payload)

    result = {"success": True}
    if cache_key:
        _idempotency_cache[cache_key] = (time.time() + IDEMPOTENCY_TTL_SECONDS, result)
    return result


@router.put("/reminders")
async def edit_reminder(
    reminder: Reminder,
    request: Request,
    auth_user_id: str = Depends(get_authenticated_user_id),
):
    global db_pool
    payload = reminder.dict()
    payload["created_by"] = payload.pop("user_id")
    if str(payload["created_by"]) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    discord_uid = await _require_discord_id_for_linked_innersync(auth_user_id)
    payload["created_by"] = discord_uid
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    idem_key = request.headers.get("Idempotency-Key")
    cache_key = None
    if idem_key:
        _cleanup_idempotency_cache()
        cache_key = _idempotency_cache_key("edit_reminder", auth_user_id, idem_key)
        cached = _idempotency_cache.get(cache_key)
        if cached:
            return cached[1]

    async with db_pool.acquire() as conn:
        await update_reminder(cast(Any, conn), payload)

    result = {"success": True}
    if cache_key:
        _idempotency_cache[cache_key] = (time.time() + IDEMPOTENCY_TTL_SECONDS, result)
    return result


@router.delete("/reminders/{reminder_id}/{created_by}")
async def remove_reminder(
    reminder_id: str,
    created_by: str,
    request: Request,
    auth_user_id: str = Depends(get_authenticated_user_id),
):
    if str(created_by) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    discord_uid = await _require_discord_id_for_linked_innersync(auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    idem_key = request.headers.get("Idempotency-Key")
    cache_key = None
    if idem_key:
        _cleanup_idempotency_cache()
        cache_key = _idempotency_cache_key("delete_reminder", auth_user_id, idem_key)
        cached = _idempotency_cache.get(cache_key)
        if cached:
            return cached[1]

    async with db_pool.acquire() as conn:
        await delete_reminder(cast(Any, conn), int(reminder_id), discord_uid)

    result = {"success": True}
    if cache_key:
        _idempotency_cache[cache_key] = (time.time() + IDEMPOTENCY_TTL_SECONDS, result)
    return result


# ---------------------------------------------------------------------------
# Settings Management Endpoints (Web Configuration Interface)
# ---------------------------------------------------------------------------

class GuildSettingsResponse(BaseModel):
    system: dict[str, Any] = {}
    reminders: dict[str, Any] = {}
    embedwatcher: dict[str, Any] = {}
    gpt: dict[str, Any] = {}
    invites: dict[str, Any] = {}
    gdpr: dict[str, Any] = {}
    automod: dict[str, Any] = {}
    onboarding: dict[str, Any] = {}
    ticketbot: dict[str, Any] = {}
    verification: dict[str, Any] = {}


class UpdateSettingsRequest(BaseModel):
    category: str
    settings: dict[str, Any]


_APP_OWNER_CACHE: tuple[int, float] | None = None
_APP_OWNER_CACHE_TTL = 60.0


async def _check_guild_admin_on_bot_loop(discord_id: int, guild_id: int) -> bool:
    """Run on bot's event loop: check if Discord user has admin in guild."""
    from gpt.helpers import bot_instance
    from utils.guild_admin import member_has_admin_in_guild

    global _APP_OWNER_CACHE

    if bot_instance is None:
        return False
    guild = bot_instance.get_guild(guild_id)
    if guild is None:
        return False
    member = guild.get_member(discord_id)
    if member is None:
        try:
            member = await guild.fetch_member(discord_id)
        except Exception:
            return False
    app_owner_id: int | None = None
    now = time.time()
    if _APP_OWNER_CACHE is not None and (now - _APP_OWNER_CACHE[1]) < _APP_OWNER_CACHE_TTL:
        app_owner_id = _APP_OWNER_CACHE[0]
    else:
        app_info = await bot_instance.application_info()
        app_owner_id = app_info.owner.id
        _APP_OWNER_CACHE = (app_owner_id, now)
    return member_has_admin_in_guild(member, app_owner_id)


async def verify_guild_admin_access(
    guild_id: int,
    auth_user_id: str,
) -> None:
    """Verify that the authenticated Supabase user has admin access to the specified guild.
    Raises HTTPException 403 if not admin or Discord ID not linked."""
    discord_id = await _require_discord_id_for_linked_innersync(auth_user_id)

    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not available for permission check.")

    loop = bot_instance.loop

    async def runner() -> bool:
        return await _check_guild_admin_on_bot_loop(discord_id, guild_id)

    try:
        future = asyncio.run_coroutine_threadsafe(runner(), loop)
        is_admin = await asyncio.wait_for(asyncio.wrap_future(future), timeout=5.0)
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Permission check timed out.") from exc
    except Exception as exc:
        logger.debug(f"Guild admin check failed: {exc}")
        raise HTTPException(status_code=403, detail="Could not verify guild admin access.") from exc

    if not is_admin:
        raise HTTPException(status_code=403, detail="You do not have admin access to this guild.")
    
    # Log successful admin access for audit trail
    logger.info(f"Admin access granted: user={auth_user_id}, discord_id={discord_id}, guild={guild_id}")


@router.get("/dashboard/settings/{guild_id}", response_model=GuildSettingsResponse)
async def get_guild_settings(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get all settings for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Fetch all settings for this guild
            rows = await conn.fetch(
                """
                SELECT scope, key, value
                FROM bot_settings
                WHERE guild_id = $1
                ORDER BY scope, key;
                """,
                guild_id
            )

            # Organize settings by category
            settings = {
                'system': {},
                'reminders': {},
                'embedwatcher': {},
                'gpt': {},
                'invites': {},
                'gdpr': {},
                'automod': {},
                'onboarding': {},
                'ticketbot': {},
                'verification': {}
            }

            for row in rows:
                scope = row['scope']
                key = row['key']
                value = row['value']

                # Convert string values back to appropriate types
                if scope in settings:
                    if key in ['allow_everyone_mentions', 'enabled', 'log_actions', 'log_to_database', 'gpt_fallback_enabled', 'non_embed_enabled', 'process_bot_messages']:
                        settings[scope][key] = value.lower() == 'true'
                    elif key in ['embed_watcher_offset_hours', 'max_tokens', 'log_channel_id', 'reminder_offset_minutes', 'category_id', 'staff_role_id', 'escalation_role_id', 'idle_days_threshold', 'auto_close_days_threshold', 'completion_role_id', 'join_role_id', 'verified_role_id']:
                        try:
                            settings[scope][key] = int(value)
                        except ValueError:
                            settings[scope][key] = value
                    elif key in ['temperature']:
                        try:
                            settings[scope][key] = float(value)
                        except ValueError:
                            settings[scope][key] = value
                    else:
                        settings[scope][key] = value

            return GuildSettingsResponse(**settings)

    except Exception as exc:
        logger.error(f"[ERROR] Failed to get guild settings: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch settings") from exc


@router.post("/dashboard/settings/{guild_id}")
async def update_guild_settings(
    guild_id: int,
    request: UpdateSettingsRequest,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Update settings for a specific guild category."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Validate category
    valid_categories = ['system', 'reminders', 'embedwatcher', 'gpt', 'invites', 'gdpr']
    if request.category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category: {request.category}")

    try:
        async with db_pool.acquire() as conn:
            # Start transaction
            async with conn.transaction():
                # First, delete existing settings for this category
                await conn.execute(
                    """
                    DELETE FROM bot_settings
                    WHERE guild_id = $1 AND scope = $2;
                    """,
                    guild_id, request.category
                )

                # Insert new settings
                for key, value in request.settings.items():
                    if value is not None and value != "":
                        await conn.execute(
                            """
                            INSERT INTO bot_settings (guild_id, scope, key, value)
                            VALUES ($1, $2, $3, $4);
                            """,
                            guild_id, request.category, key, str(value)
                        )

            # Log to operational events
            log_operational_event(
                EventType.SETTINGS_CHANGED,
                f"Bulk settings update: {len(request.settings)} settings in scope '{request.category}'",
                guild_id=guild_id,
                details={
                    "scope": request.category,
                    "count": len(request.settings),
                    "updated_by": auth_user_id,
                    "action": "bulk_update",
                    "source": "api"
                }
            )

            return {"success": True, "message": f"Updated {request.category} settings"}

    except Exception as exc:
        logger.error("[ERROR] Failed to update guild settings: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update settings") from exc


# ---------------------------------------------------------------------------
# GDPR Dashboard Endpoint
# ---------------------------------------------------------------------------

@router.get("/dashboard/{guild_id}/gdpr")
async def get_gdpr_dashboard(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id),
):
    """Return GDPR acceptance statistics for the guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM gdpr_acceptance WHERE guild_id = $1 AND accepted = 1",
                guild_id,
            )
        return {"guild_id": guild_id, "acceptance_count": int(count or 0)}
    except Exception as exc:
        logger.error("[GDPR] Dashboard fetch error: %s", exc)
        raise HTTPException(status_code=500, detail="Error fetching GDPR data") from exc


# ---------------------------------------------------------------------------
# Onboarding Questions & Rules Management Endpoints (Web Configuration Interface)
# ---------------------------------------------------------------------------

class OnboardingQuestion(BaseModel):
    id: int | None = None
    question: str
    question_type: Literal['select', 'multiselect', 'text', 'email']
    options: list[dict[str, str]] | None = None  # [{"label": "Option 1", "value": "value1"}]
    followup: dict[str, Any] | None = None  # {"value": {"question": "Followup question"}}
    required: bool = True
    enabled: bool = True
    step_order: int

class OnboardingRule(BaseModel):
    id: int | None = None
    title: str
    description: str
    thumbnail_url: str | None = None  # Image shown right/top (rechts)
    image_url: str | None = None  # Image shown at bottom (onderaan)
    enabled: bool = True
    rule_order: int


@router.get("/dashboard/{guild_id}/onboarding/questions", response_model=list[OnboardingQuestion])
async def get_guild_onboarding_questions(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get all onboarding questions for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, step_order, question, question_type, options, followup, required, enabled
                FROM guild_onboarding_questions
                WHERE guild_id = $1 AND enabled = TRUE
                ORDER BY step_order
                """,
                guild_id
            )

            questions = []
            for row in rows:
                questions.append(OnboardingQuestion(
                    id=row["id"],
                    question=row["question"],
                    question_type=row["question_type"],
                    options=row["options"],
                    followup=row["followup"],
                    required=row["required"],
                    enabled=row["enabled"],
                    step_order=row["step_order"]
                ))

            return questions

    except Exception as exc:
        logger.error("[ERROR] Failed to get guild onboarding questions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch questions") from exc


@router.post("/dashboard/{guild_id}/onboarding/questions")
async def save_guild_onboarding_question(
    guild_id: int,
    question: OnboardingQuestion,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Save or update an onboarding question for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guild_onboarding_questions
                (guild_id, step_order, question, question_type, options, followup, required, enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (guild_id, step_order)
                DO UPDATE SET
                    question = EXCLUDED.question,
                    question_type = EXCLUDED.question_type,
                    options = EXCLUDED.options,
                    followup = EXCLUDED.followup,
                    required = EXCLUDED.required,
                    enabled = EXCLUDED.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                guild_id,
                question.step_order,
                question.question,
                question.question_type,
                question.options,
                question.followup,
                question.required,
                question.enabled
            )

            return {"success": True, "message": "Question saved successfully"}

    except Exception as exc:
        logger.error("[ERROR] Failed to save onboarding question: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save question") from exc


@router.delete("/dashboard/{guild_id}/onboarding/questions/{question_id}")
async def delete_guild_onboarding_question(
    guild_id: int,
    question_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Delete an onboarding question for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM guild_onboarding_questions WHERE guild_id = $1 AND id = $2",
                guild_id, question_id
            )

            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Question not found")

            return {"success": True, "message": "Question deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ERROR] Failed to delete onboarding question: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete question") from exc


@router.get("/dashboard/{guild_id}/onboarding/rules", response_model=list[OnboardingRule])
async def get_guild_onboarding_rules(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get all onboarding rules for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, rule_order, title, description, thumbnail_url, image_url, enabled
                FROM guild_rules
                WHERE guild_id = $1 AND enabled = TRUE
                ORDER BY rule_order
                """,
                guild_id
            )

            rules = []
            for row in rows:
                rules.append(OnboardingRule(
                    id=row["id"],
                    title=row["title"],
                    description=row["description"],
                    thumbnail_url=row.get("thumbnail_url"),
                    image_url=row.get("image_url"),
                    enabled=row["enabled"],
                    rule_order=row["rule_order"]
                ))

            return rules

    except Exception as exc:
        logger.error("[ERROR] Failed to get guild onboarding rules: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch rules") from exc


@router.post("/dashboard/{guild_id}/onboarding/rules")
async def save_guild_onboarding_rule(
    guild_id: int,
    rule: OnboardingRule,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Save or update an onboarding rule for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guild_rules
                (guild_id, rule_order, title, description, thumbnail_url, image_url, enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (guild_id, rule_order)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    image_url = EXCLUDED.image_url,
                    enabled = EXCLUDED.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                guild_id,
                rule.rule_order,
                rule.title,
                rule.description,
                rule.thumbnail_url or None,
                rule.image_url or None,
                rule.enabled
            )

            return {"success": True, "message": "Rule saved successfully"}

    except Exception as exc:
        logger.error("[ERROR] Failed to save onboarding rule: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save rule") from exc


@router.delete("/dashboard/{guild_id}/onboarding/rules/{rule_id}")
async def delete_guild_onboarding_rule(
    guild_id: int,
    rule_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Delete an onboarding rule for a specific guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM guild_rules WHERE guild_id = $1 AND id = $2",
                guild_id, rule_id
            )

            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Rule not found")

            return {"success": True, "message": "Rule deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ERROR] Failed to delete onboarding rule: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete rule") from exc


class ReorderRequest(BaseModel):
    questions: list[int] | None = None
    rules: list[int] | None = None

@router.post("/dashboard/{guild_id}/onboarding/reorder")
async def reorder_onboarding_items(
    guild_id: int,
    request: ReorderRequest,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Reorder onboarding questions and rules."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # Update question order
                if request.questions:
                    for i, question_id in enumerate(request.questions):
                        await conn.execute(
                            "UPDATE guild_onboarding_questions SET step_order = $1 WHERE guild_id = $2 AND id = $3",
                            i + 1, guild_id, question_id
                        )

                # Update rule order
                if request.rules:
                    for i, rule_id in enumerate(request.rules):
                        await conn.execute(
                            "UPDATE guild_rules SET rule_order = $1 WHERE guild_id = $2 AND id = $3",
                            i + 1, guild_id, rule_id
                        )

            return {"success": True, "message": "Order updated successfully"}

    except Exception as exc:
        logger.error("[ERROR] Failed to reorder onboarding items: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reorder items") from exc


# ---------------------------------------------------------------------------
# Settings History Endpoints (Web Configuration Interface)
# ---------------------------------------------------------------------------

class SettingsHistoryEntry(BaseModel):
    id: int
    scope: str
    key: str
    old_value: Any | None = None
    new_value: Any
    value_type: str | None = None
    changed_by: int | None = None
    changed_at: str
    change_type: Literal['created', 'updated', 'deleted', 'rollback']


@router.get("/dashboard/{guild_id}/settings/history", response_model=list[SettingsHistoryEntry])
async def get_settings_history(
    guild_id: int,
    scope: str | None = None,
    key: str | None = None,
    limit: int = 50,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get settings change history for a specific guild."""
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            if scope and key:
                query = """
                    SELECT id, scope, key, old_value, new_value, value_type, changed_by, changed_at, change_type
                    FROM settings_history
                    WHERE guild_id = $1 AND scope = $2 AND key = $3
                    ORDER BY changed_at DESC LIMIT $4
                """
                params = [guild_id, scope, key, limit]
            elif scope:
                query = """
                    SELECT id, scope, key, old_value, new_value, value_type, changed_by, changed_at, change_type
                    FROM settings_history
                    WHERE guild_id = $1 AND scope = $2
                    ORDER BY changed_at DESC LIMIT $3
                """
                params = [guild_id, scope, limit]
            else:
                query = """
                    SELECT id, scope, key, old_value, new_value, value_type, changed_by, changed_at, change_type
                    FROM settings_history
                    WHERE guild_id = $1
                    ORDER BY changed_at DESC LIMIT $2
                """
                params = [guild_id, limit]

            rows = await conn.fetch(query, *params)

            history = []
            for row in rows:
                history.append(SettingsHistoryEntry(
                    id=row["id"],
                    scope=row["scope"],
                    key=row["key"],
                    old_value=row["old_value"],
                    new_value=row["new_value"],
                    value_type=row["value_type"],
                    changed_by=row["changed_by"],
                    changed_at=row["changed_at"].isoformat(),
                    change_type=row["change_type"]
                ))

            return history

    except Exception as exc:
        logger.error("[ERROR] Failed to get settings history: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch settings history") from exc


@router.post("/dashboard/{guild_id}/settings/rollback/{history_id}")
async def rollback_setting_change(
    guild_id: int,
    history_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Rollback a setting to a previous value from history."""
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Get the history entry
            history_row = await conn.fetchrow(
                """
                SELECT scope, key, old_value, change_type, value_type
                FROM settings_history
                WHERE id = $1 AND guild_id = $2
                """,
                history_id, guild_id
            )

            if not history_row:
                raise HTTPException(status_code=404, detail="History entry not found")

            scope: str = history_row["scope"]
            key: str = history_row["key"]
            old_value: Any = history_row["old_value"]
            change_type: str = history_row["change_type"]

            # Only allow rollback for updated entries with old values
            if change_type != "updated" or old_value is None:
                raise HTTPException(status_code=400, detail="Cannot rollback this type of change")

            # Update the setting back to the old value
            await conn.execute(
                """
                UPDATE bot_settings
                SET value = $1, updated_by = $2, updated_at = NOW()
                WHERE guild_id = $3 AND scope = $4 AND key = $5
                """,
                old_value, int(auth_user_id), guild_id, scope, key
            )

            # Record the rollback in history
            await conn.execute(
                """
                INSERT INTO settings_history
                (guild_id, scope, key, old_value, new_value, value_type, changed_by, change_type)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'rollback')
                """,
                guild_id, scope, key, history_row["old_value"], old_value,
                history_row["value_type"], int(auth_user_id)
            )

            # Log to operational events
            log_operational_event(
                EventType.SETTINGS_CHANGED,
                f"Setting rolled back: {scope}.{key}",
                guild_id=guild_id,
                details={
                    "scope": scope,
                    "key": key,
                    "history_id": history_id,
                    "updated_by": auth_user_id,
                    "action": "rollback",
                    "source": "api"
                }
            )

            return {"success": True, "message": f"Rolled back {scope}.{key} to previous value"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ERROR] Failed to rollback setting: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to rollback setting") from exc


class OperationalLogsResponse(BaseModel):
    logs: list[dict[str, Any]]


# Auto-Moderation Management Endpoints (Web Configuration Interface)
# ---------------------------------------------------------------------------

class AutoModRule(BaseModel):
    id: int | None = None
    guild_id: int
    rule_type: str  # 'spam', 'content', 'regex', 'ai', 'mentions', 'caps', 'duplicate'
    name: str
    enabled: bool = True
    config: dict[str, Any]
    action_type: str  # 'delete', 'warn', 'mute', 'timeout', 'ban'
    action_config: dict[str, Any]
    severity: int = 1
    created_by: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    is_premium: bool = False


class AutoModRuleCreate(BaseModel):
    rule_type: str
    name: str
    enabled: bool = True
    config: dict[str, Any]
    action_type: str
    action_config: dict[str, Any]
    severity: int = 1


class AutoModRuleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    action_type: str | None = None
    action_config: dict[str, Any] | None = None
    severity: int | None = None


class AutoModStats(BaseModel):
    total_rules: int
    enabled_rules: int
    rules_by_type: dict[str, int]
    total_violations: int
    violations_today: int
    violations_week: int
    top_violated_rules: list[dict[str, Any]]


class AutoModViolation(BaseModel):
    id: int
    guild_id: int
    user_id: int
    message_id: int | None
    channel_id: int | None
    rule_id: int | None
    action_taken: str
    message_content: str | None
    ai_analysis: dict[str, Any] | None
    context: dict[str, Any] | None
    timestamp: str
    moderator_id: int | None


class AutoModSettings(BaseModel):
    enabled: bool = False
    log_channel_id: int | None = None
    log_actions: bool = True
    log_to_database: bool = True


@router.get("/dashboard/logs", response_model=OperationalLogsResponse)
async def get_dashboard_logs(
    guild_id: int,
    limit: int = 50,
    event_types: str | None = None,
    auth_user_id: str = Depends(get_authenticated_user_id),
):
    """Get operational logs (reconnect, disconnect, etc.) for the Mind dashboard.
    Requires guild admin access. Global events (no guild_id) are included for any guild request."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    limit = min(limit, 100)
    types_list: list[str] | None = None
    if event_types:
        types_list = [t.strip() for t in event_types.split(",") if t.strip()]
    logs = get_operational_events(guild_id=guild_id, limit=limit, event_types=types_list)
    return OperationalLogsResponse(logs=logs)


# Auto-Moderation Management Endpoints
# ---------------------------------------------------------------------------

@router.get("/dashboard/{guild_id}/automod/rules", response_model=list[AutoModRule])
async def get_automod_rules(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get all auto-moderation rules for a guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        # Import here to avoid circular imports
        
        # Get bot instance for RuleProcessor
        # Note: In production, you'd need to inject the bot instance properly
        # For now, we'll query the database directly
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    r.id, r.guild_id, r.rule_type, r.name, r.enabled, r.config,
                    r.created_by, r.created_at, r.updated_at, r.is_premium,
                    a.action_type, a.config as action_config, a.severity
                FROM automod_rules r
                LEFT JOIN automod_actions a ON r.action_id = a.id
                WHERE r.guild_id = $1
                ORDER BY a.severity DESC, r.created_at DESC
            """, guild_id)
            
            rules = []
            for row in rows:
                rule_dict = dict(row)
                # Parse JSON config if needed
                if rule_dict.get('config') and isinstance(rule_dict['config'], str):
                    try:
                        import json
                        rule_dict['config'] = json.loads(rule_dict['config'])
                    except (ValueError, TypeError):
                        pass
                
                if rule_dict.get('action_config') and isinstance(rule_dict['action_config'], str):
                    try:
                        import json
                        rule_dict['action_config'] = json.loads(rule_dict['action_config'])
                    except (ValueError, TypeError):
                        pass
                
                rules.append(AutoModRule(**rule_dict))
            
            return rules
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to get auto-mod rules for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch auto-mod rules") from exc


@router.post("/dashboard/{guild_id}/automod/rules", response_model=AutoModRule)
async def create_automod_rule(
    guild_id: int,
    rule: AutoModRuleCreate,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Create a new auto-moderation rule."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        # Get user's Discord ID (Innersync JWT sub → Discord via alphapy_discord_links / profile fallback)
        user_id = await _require_discord_id_for_linked_innersync(auth_user_id)
        
        # Rule metadata reflects guild entitlement, not the caller's personal subscription
        from utils.premium_guard import guild_has_premium
        is_premium = await guild_has_premium(guild_id)
        
        async with db_pool.acquire() as conn:
            # Create action first
            import json
            action_id = await conn.fetchval("""
                INSERT INTO automod_actions (guild_id, action_type, config, is_premium, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """, guild_id, rule.action_type, json.dumps(rule.action_config), is_premium, user_id)
            
            # Create rule
            rule_id = await conn.fetchval("""
                INSERT INTO automod_rules (guild_id, rule_type, name, enabled, config, action_id, created_by, is_premium)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, guild_id, rule.rule_type, rule.name, rule.enabled, json.dumps(rule.config), action_id, user_id, is_premium)
            
            # Fetch the complete rule with action
            row = await conn.fetchrow("""
                SELECT 
                    r.id, r.guild_id, r.rule_type, r.name, r.enabled, r.config,
                    r.created_by, r.created_at, r.updated_at, r.is_premium,
                    a.action_type, a.config as action_config, a.severity
                FROM automod_rules r
                LEFT JOIN automod_actions a ON r.action_id = a.id
                WHERE r.id = $1
            """, rule_id)
            
            if row:
                rule_dict = dict(row)
                rule_dict['config'] = json.loads(rule_dict['config'])
                rule_dict['action_config'] = json.loads(rule_dict['action_config'])
                return AutoModRule(**rule_dict)
            else:
                raise HTTPException(status_code=500, detail="Failed to create rule")
                
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to create auto-mod rule for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create auto-mod rule") from exc


@router.put("/dashboard/{guild_id}/automod/rules/{rule_id}", response_model=AutoModRule)
async def update_automod_rule(
    guild_id: int,
    rule_id: int,
    update: AutoModRuleUpdate,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Update an existing auto-moderation rule."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Fetch linked action_id so we can update both rule and action records
            row_ids = await conn.fetchrow(
                "SELECT id, action_id FROM automod_rules WHERE guild_id = $1 AND id = $2",
                guild_id,
                rule_id,
            )
            if not row_ids:
                raise HTTPException(status_code=404, detail="Rule not found")

            action_id = row_ids["action_id"]

            # Update rule fields
            if update.name is not None:
                await conn.execute(
                    "UPDATE automod_rules SET name = $1, updated_at = NOW() WHERE guild_id = $2 AND id = $3",
                    update.name,
                    guild_id,
                    rule_id,
                )

            if update.enabled is not None:
                await conn.execute(
                    "UPDATE automod_rules SET enabled = $1, updated_at = NOW() WHERE guild_id = $2 AND id = $3",
                    update.enabled,
                    guild_id,
                    rule_id,
                )

            if update.config is not None:
                import json

                await conn.execute(
                    "UPDATE automod_rules SET config = $1, updated_at = NOW() WHERE guild_id = $2 AND id = $3",
                    json.dumps(update.config),
                    guild_id,
                    rule_id,
                )

            # Update action fields on the linked automod_actions row
            if action_id:
                if update.action_type is not None:
                    await conn.execute(
                        "UPDATE automod_actions SET action_type = $1 WHERE guild_id = $2 AND id = $3",
                        update.action_type,
                        guild_id,
                        action_id,
                    )

                if update.action_config is not None:
                    import json

                    await conn.execute(
                        "UPDATE automod_actions SET config = $1 WHERE guild_id = $2 AND id = $3",
                        json.dumps(update.action_config),
                        guild_id,
                        action_id,
                    )

                if update.severity is not None:
                    await conn.execute(
                        "UPDATE automod_actions SET severity = $1 WHERE guild_id = $2 AND id = $3",
                        update.severity,
                        guild_id,
                        action_id,
                    )
            
            # Fetch updated rule
            row = await conn.fetchrow("""
                SELECT 
                    r.id, r.guild_id, r.rule_type, r.name, r.enabled, r.config,
                    r.created_by, r.created_at, r.updated_at, r.is_premium,
                    a.action_type, a.config as action_config, a.severity
                FROM automod_rules r
                LEFT JOIN automod_actions a ON r.action_id = a.id
                WHERE r.guild_id = $1 AND r.id = $2
            """, guild_id, rule_id)
            
            if row:
                rule_dict = dict(row)
                import json
                rule_dict['config'] = json.loads(rule_dict['config'])
                rule_dict['action_config'] = json.loads(rule_dict['action_config'])
                return AutoModRule(**rule_dict)
            else:
                raise HTTPException(status_code=404, detail="Rule not found")
                
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to update auto-mod rule {rule_id} for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update auto-mod rule") from exc


@router.delete("/dashboard/{guild_id}/automod/rules/{rule_id}")
async def delete_automod_rule(
    guild_id: int,
    rule_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Delete an auto-moderation rule."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Get rule row first so we can distinguish between "rule not found"
            # and "rule exists but has no linked action".
            row = await conn.fetchrow(
                "SELECT action_id FROM automod_rules WHERE guild_id = $1 AND id = $2",
                guild_id,
                rule_id,
            )

            if not row:
                raise HTTPException(status_code=404, detail="Rule not found")

            action_id = row["action_id"]

            # Delete rule
            await conn.execute(
                "DELETE FROM automod_rules WHERE guild_id = $1 AND id = $2",
                guild_id,
                rule_id,
            )

            # Delete linked action if present
            if action_id:
                await conn.execute(
                    "DELETE FROM automod_actions WHERE guild_id = $1 AND id = $2",
                    guild_id,
                    action_id,
                )
            
            return {"success": True}
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to delete auto-mod rule {rule_id} for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete auto-mod rule") from exc


@router.get("/dashboard/{guild_id}/automod/stats", response_model=AutoModStats)
async def get_automod_stats(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get auto-moderation statistics for a guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Get rule stats
            rule_stats = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_rules,
                    COUNT(*) FILTER (WHERE enabled = true) as enabled_rules
                FROM automod_rules 
                WHERE guild_id = $1
            """, guild_id)
            
            # Get rules by type
            rules_by_type = await conn.fetch("""
                SELECT rule_type, COUNT(*) as count
                FROM automod_rules 
                WHERE guild_id = $1
                GROUP BY rule_type
            """, guild_id)
            
            # Get violation stats
            violation_stats = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_violations,
                    COUNT(*) FILTER (WHERE timestamp >= NOW() - interval '1 day') as violations_today,
                    COUNT(*) FILTER (WHERE timestamp >= NOW() - interval '7 days') as violations_week
                FROM automod_logs 
                WHERE guild_id = $1
            """, guild_id)
            
            # Get top violated rules
            top_rules = await conn.fetch("""
                SELECT 
                    r.name,
                    r.rule_type,
                    COUNT(*) as violation_count
                FROM automod_logs l
                JOIN automod_rules r ON l.rule_id = r.id
                WHERE l.guild_id = $1 AND l.timestamp >= NOW() - interval '7 days'
                GROUP BY r.id, r.name, r.rule_type
                ORDER BY violation_count DESC
                LIMIT 5
            """, guild_id)
            
            return AutoModStats(
                total_rules=rule_stats['total_rules'] or 0,
                enabled_rules=rule_stats['enabled_rules'] or 0,
                rules_by_type={row['rule_type']: row['count'] for row in rules_by_type},
                total_violations=violation_stats['total_violations'] or 0,
                violations_today=violation_stats['violations_today'] or 0,
                violations_week=violation_stats['violations_week'] or 0,
                top_violated_rules=[dict(row) for row in top_rules]
            )
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to get auto-mod stats for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch auto-mod stats") from exc


@router.get("/dashboard/{guild_id}/automod/violations", response_model=list[AutoModViolation])
async def get_automod_violations(
    guild_id: int,
    limit: int = 50,
    days: int = 7,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get recent auto-moderation violations for a guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Safety clamp to avoid overly large responses
    limit = min(limit, 200)

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    id, guild_id, user_id, message_id, channel_id, rule_id,
                    action_taken, message_content, ai_analysis, context,
                    timestamp, moderator_id
                FROM automod_logs 
                WHERE guild_id = $1
                  AND timestamp >= NOW() - ($2::text || ' days')::interval
                ORDER BY timestamp DESC
                LIMIT $3
            """, guild_id, days, limit)
            
            violations = []
            for row in rows:
                violation_dict = dict(row)
                # Parse JSON fields if needed
                if violation_dict.get('ai_analysis') and isinstance(violation_dict['ai_analysis'], str):
                    try:
                        import json
                        violation_dict['ai_analysis'] = json.loads(violation_dict['ai_analysis'])
                    except (ValueError, TypeError):
                        pass
                
                if violation_dict.get('context') and isinstance(violation_dict['context'], str):
                    try:
                        import json
                        violation_dict['context'] = json.loads(violation_dict['context'])
                    except (ValueError, TypeError):
                        pass
                
                # Format timestamp
                if violation_dict.get('timestamp'):
                    violation_dict['timestamp'] = violation_dict['timestamp'].isoformat()
                
                violations.append(AutoModViolation(**violation_dict))
            
            return violations
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to get auto-mod violations for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch auto-mod violations") from exc


@router.get("/dashboard/{guild_id}/automod/settings", response_model=AutoModSettings)
async def get_automod_settings(
    guild_id: int,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Get auto-moderation settings for a guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Get automod settings from bot_settings table
            rows = await conn.fetch("""
                SELECT key, value
                FROM bot_settings
                WHERE guild_id = $1 AND scope = 'automod'
            """, guild_id)
            
            settings = {}
            for row in rows:
                key = row['key']
                value = row['value']
                
                # Convert boolean values
                if key in ['enabled', 'log_actions', 'log_to_database']:
                    settings[key] = value.lower() == 'true'
                elif key == 'log_channel_id':
                    settings[key] = int(value) if value.isdigit() else None
                else:
                    settings[key] = value
            
            return AutoModSettings(**settings)
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to get auto-mod settings for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch auto-mod settings") from exc


@router.post("/dashboard/{guild_id}/automod/settings")
async def update_automod_settings(
    guild_id: int,
    settings: AutoModSettings,
    auth_user_id: str = Depends(get_authenticated_user_id)
):
    """Update auto-moderation settings for a guild."""
    await verify_guild_admin_access(guild_id, auth_user_id)
    
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            # Update each setting
            for key, value in settings.dict().items():
                # Convert value to string for storage
                str_value = str(value).lower() if isinstance(value, bool) else str(value)
                
                await conn.execute("""
                    INSERT INTO bot_settings (guild_id, scope, key, value)
                    VALUES ($1, 'automod', $2, $3)
                    ON CONFLICT (guild_id, scope, key) 
                    DO UPDATE SET value = EXCLUDED.value
                """, guild_id, key, str_value)
            
            return {"success": True}
            
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to update auto-mod settings for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update auto-mod settings") from exc


# ---------------------------------------------------------------------------
# Verification dashboard (manual review queue)
# ---------------------------------------------------------------------------


class VerificationQueueItem(BaseModel):
    id: int
    user_id: int
    channel_id: int
    status: str
    ai_reason: str | None = None
    ai_can_verify: bool | None = None
    ai_needs_manual_review: bool | None = None
    payment_date: str | None = None
    created_at: datetime | None = None


class VerificationResolveRequest(BaseModel):
    outcome: Literal["approved", "rejected"]
    reason: str | None = None


async def verify_dashboard_discord_admin(
    guild_id: int,
    x_discord_user_id: str = Header(..., alias="X-Discord-User-Id"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
) -> int:
    """Dashboard service auth: API key + Discord snowflake with guild admin check."""
    configured_key = getattr(config, "API_KEY", None)
    if not configured_key or x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        discord_id = int(x_discord_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Discord user id") from exc

    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not available for permission check.")

    loop = bot_instance.loop

    async def runner() -> bool:
        return await _check_guild_admin_on_bot_loop(discord_id, guild_id)

    try:
        future = asyncio.run_coroutine_threadsafe(runner(), loop)
        is_admin = await asyncio.wait_for(asyncio.wrap_future(future), timeout=5.0)
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Permission check timed out.") from exc
    except Exception as exc:
        logger.debug(f"Dashboard guild admin check failed: {exc}")
        raise HTTPException(status_code=403, detail="Could not verify guild admin access.") from exc

    if not is_admin:
        raise HTTPException(status_code=403, detail="You do not have admin access to this guild.")
    return discord_id


async def _resolve_verification_on_bot_loop(
    guild_id: int,
    ticket_id: int,
    outcome: Literal["approved", "rejected"],
    reason: str,
    resolved_by_discord_id: int,
) -> None:
    import discord

    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise RuntimeError("Bot not available")

    cog = bot_instance.get_cog("VerificationCog")
    if cog is None:
        raise RuntimeError("Verification cog not loaded")

    global db_pool
    if db_pool is None:
        raise RuntimeError("Database not available")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT channel_id, user_id, status
            FROM verification_tickets
            WHERE id = $1 AND guild_id = $2
            """,
            ticket_id,
            guild_id,
        )
    if not row:
        raise RuntimeError("Verification ticket not found")
    if str(row["status"]) != "manual_review":
        raise RuntimeError("Ticket is not awaiting manual review")

    guild = bot_instance.get_guild(guild_id)
    if guild is None:
        raise RuntimeError("Guild not found")

    channel_id = int(row["channel_id"])
    channel = guild.get_channel(channel_id)
    if channel is None:
        channel = await guild.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError("Verification channel not found")

    member = guild.get_member(int(row["user_id"]))
    if member is None:
        try:
            member = await guild.fetch_member(int(row["user_id"]))
        except Exception:
            member = None

    resolver = guild.get_member(resolved_by_discord_id)
    if resolver is None:
        try:
            resolver = await bot_instance.fetch_user(resolved_by_discord_id)
        except Exception:
            resolver = None

    from cogs.verification import VerificationCog

    await cast(VerificationCog, cog)._resolve_verification(
        channel=channel,
        member=member,
        guild=guild,
        resolved_by=resolver,
        outcome=outcome,
        reason=reason,
    )


@router.get(
    "/dashboard/{guild_id}/verification/queue",
    response_model=list[VerificationQueueItem],
)
async def get_verification_queue(
    guild_id: int,
    discord_admin_id: int = Depends(verify_dashboard_discord_admin),
):
    """List verification tickets awaiting manual review (no screenshot content)."""
    del discord_admin_id
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, channel_id, status, ai_reason, ai_can_verify,
                       ai_needs_manual_review, payment_date, created_at
                FROM verification_tickets
                WHERE guild_id = $1 AND status = 'manual_review' AND resolved_at IS NULL
                ORDER BY created_at ASC
                """,
                guild_id,
            )
        return [
            VerificationQueueItem(
                id=int(row["id"]),
                user_id=int(row["user_id"]),
                channel_id=int(row["channel_id"]),
                status=str(row["status"]),
                ai_reason=row["ai_reason"],
                ai_can_verify=row["ai_can_verify"],
                ai_needs_manual_review=row["ai_needs_manual_review"],
                payment_date=row["payment_date"].isoformat() if row["payment_date"] else None,
                created_at=row["created_at"],
            )
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ERROR] Failed to fetch verification queue for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch verification queue") from exc


@router.post("/dashboard/{guild_id}/verification/{ticket_id}/resolve")
async def resolve_verification_ticket(
    guild_id: int,
    ticket_id: int,
    body: VerificationResolveRequest,
    discord_admin_id: int = Depends(verify_dashboard_discord_admin),
):
    """Approve or reject a manual verification review (runs on bot event loop)."""
    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not available")

    reason = (body.reason or "").strip()

    async def runner() -> None:
        await _resolve_verification_on_bot_loop(
            guild_id,
            ticket_id,
            body.outcome,
            reason,
            discord_admin_id,
        )

    try:
        future = asyncio.run_coroutine_threadsafe(runner(), bot_instance.loop)
        await asyncio.wait_for(asyncio.wrap_future(future), timeout=30.0)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Verification resolution timed out.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            f"[ERROR] Failed to resolve verification ticket {ticket_id} for guild {guild_id}: {exc}"
        )
        raise HTTPException(status_code=500, detail="Failed to resolve verification") from exc

    return {"success": True, "outcome": body.outcome, "ticket_id": ticket_id}


# ---------------------------------------------------------------------------
# Dashboard discord meta (channels / roles for pickers)
# ---------------------------------------------------------------------------


class DiscordMetaChannel(BaseModel):
    id: str
    name: str
    type: Literal["text", "announcement", "category"]
    parent_id: str | None = None


class DiscordMetaRole(BaseModel):
    id: str
    name: str
    color: int | None = None
    position: int


class DiscordMetaResponse(BaseModel):
    channels: list[DiscordMetaChannel]
    roles: list[DiscordMetaRole]


async def _fetch_discord_meta_on_bot_loop(guild_id: int) -> DiscordMetaResponse:
    import discord

    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise RuntimeError("Bot not available")

    guild = bot_instance.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot_instance.fetch_guild(guild_id)
        except Exception as exc:
            raise RuntimeError("Guild not found") from exc

    if not guild.channels:
        try:
            await guild.fetch_channels()
        except Exception:
            pass

    channels: list[DiscordMetaChannel] = []
    for channel in guild.channels:
        if channel.type == discord.ChannelType.category:
            channels.append(
                DiscordMetaChannel(
                    id=str(channel.id),
                    name=channel.name,
                    type="category",
                    parent_id=None,
                )
            )
        elif channel.type == discord.ChannelType.text:
            channels.append(
                DiscordMetaChannel(
                    id=str(channel.id),
                    name=channel.name,
                    type="text",
                    parent_id=str(channel.category_id) if channel.category_id else None,
                )
            )
        elif channel.type == discord.ChannelType.news:
            channels.append(
                DiscordMetaChannel(
                    id=str(channel.id),
                    name=channel.name,
                    type="announcement",
                    parent_id=str(channel.category_id) if channel.category_id else None,
                )
            )

    channels.sort(
        key=lambda item: (
            item.type != "category",
            item.parent_id or "",
            item.name.lower(),
        )
    )

    roles: list[DiscordMetaRole] = []
    for role in sorted(guild.roles, key=lambda item: item.position, reverse=True):
        if role.is_default() or role.managed or not role.is_assignable():
            continue
        roles.append(
            DiscordMetaRole(
                id=str(role.id),
                name=role.name,
                color=role.color.value or None,
                position=role.position,
            )
        )

    return DiscordMetaResponse(channels=channels, roles=roles)


@router.get(
    "/dashboard/{guild_id}/discord-meta",
    response_model=DiscordMetaResponse,
)
async def get_dashboard_discord_meta(
    guild_id: int,
    discord_admin_id: int = Depends(verify_dashboard_discord_admin),
):
    """List guild channels and assignable roles for dashboard pickers."""
    del discord_admin_id
    from gpt.helpers import bot_instance

    if bot_instance is None:
        raise HTTPException(status_code=503, detail="Bot not available")

    async def runner() -> DiscordMetaResponse:
        return await _fetch_discord_meta_on_bot_loop(guild_id)

    try:
        future = asyncio.run_coroutine_threadsafe(runner(), bot_instance.loop)
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=10.0)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Discord meta fetch timed out.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[ERROR] Failed to fetch discord meta for guild {guild_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch discord meta") from exc


include_agent_routes(
    router,
    get_authenticated_user_id=get_authenticated_user_id,
    require_discord_link=_require_discord_id_for_linked_innersync,
)


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
