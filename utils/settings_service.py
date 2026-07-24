import asyncio
import json
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from typing import Any

import asyncpg
from asyncpg import exceptions as pg_exceptions

from utils.logger import log_database_event
from utils.operational_logs import EventType, log_operational_event

SettingListener = Callable[[Any], Coroutine[Any, Any, None]]
# Global listener receives (scope, key, guild_id, new_value) for every setting change.
GlobalSettingListener = Callable[[str, str, int, Any], Coroutine[Any, Any, None]]
# Fired after reload_guild so helpers can drop per-guild LRU entries.
ReloadListener = Callable[[int], None]

# How long ensure_fresh may reuse the last DB reload for a guild (seconds).
DEFAULT_GUILD_SETTINGS_TTL = 2.0

# Sentinel for "key was not present in old overrides" (identity-stable).
_MISSING = object()


def _unwrap_quoted_scalar_str(value: str) -> str:
    """Strip redundant quote layers from settings loaded as JSON strings (e.g. '\"123\"')."""
    s = value.strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    return s


@dataclass(frozen=True)
class SettingDefinition:
    scope: str
    key: str
    description: str
    value_type: str
    default: Any
    allow_null: bool = False
    min_value: float | None = None
    max_value: float | None = None
    choices: Iterable[Any] | None = None


class SettingsService:
    """Runtime settings registry backed by PostgreSQL overrides."""

    def __init__(self, dsn: str | None):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._definitions: dict[tuple[str, str], SettingDefinition] = {}
        self._overrides: dict[tuple[int, str, str], Any] = {}
        self._raw_overrides: dict[tuple[int, str, str], Any] = {}  # in-memory raw (e.g. fyi) when pool unavailable
        self._listeners: dict[tuple[str, str], list[SettingListener]] = {}
        self._global_listeners: list[GlobalSettingListener] = []
        self._reload_listeners: list[ReloadListener] = []
        self._guild_fresh_at: dict[int, float] = {}
        self._reload_locks: dict[int, asyncio.Lock] = {}
        self._ready = False

    def register(self, definition: SettingDefinition) -> None:
        key = (definition.scope, definition.key)
        if key in self._definitions:
            raise ValueError(f"Setting '{definition.scope}.{definition.key}' already registered")
        self._definitions[key] = definition

    async def setup(self) -> None:
        if self._ready or not self._dsn:
            self._ready = True
            return

        await self._init_pool_with_retry()
        self._ready = True

    async def _init_pool_with_retry(self, attempts: int = 5, base_delay: float = 1.5) -> None:
        """Initialize database pool with retry logic."""
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._create_pool()
                log_database_event("POOL_INITIALIZED", details=f"Attempt {attempt}/{attempts}")
                return
            except (pg_exceptions.PostgresError, ConnectionError, OSError) as exc:
                last_error = exc
                log_database_event("POOL_INIT_RETRY", details=f"Attempt {attempt}/{attempts} failed: {exc}")
                delay = base_delay * attempt
                await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                log_database_event("POOL_INIT_ERROR", details=f"Fatal error: {exc}")
                raise

        log_database_event("POOL_INIT_FAILED", details=f"All {attempts} attempts failed: {last_error}")
        raise RuntimeError(f"SettingsService: could not initialize database pool: {last_error}")

    async def _create_pool(self) -> None:
        """Create database connection pool."""
        if not self._dsn:
            return

        # Create pool with better connection settings for Railway
        pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,          # Minimum connections in pool
            max_size=10,         # Maximum connections in pool
            max_queries=50000,   # Maximum queries per connection
            max_inactive_connection_lifetime=300.0,  # 5 minutes
            command_timeout=60.0,  # 60 second timeout
            init=None,
            setup=None,
            server_settings={
                'application_name': 'alphapy_bot_settings'
            }
        )

        async with pool.acquire() as conn:
            # Test connection
            await conn.execute("SELECT 1")

            # Create tables if they don't exist
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    guild_id BIGINT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    value_type TEXT,
                    updated_by BIGINT,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY(guild_id, scope, key)
                );
                """
            )

            # Settings history table for audit trail
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings_history (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    old_value JSONB,
                    new_value JSONB NOT NULL,
                    value_type TEXT,
                    changed_by BIGINT,
                    changed_at TIMESTAMPTZ DEFAULT NOW(),
                    change_type TEXT NOT NULL
                );
                """
            )
            # Create indexes separately for better compatibility
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_settings_history_guild_scope_key
                ON settings_history (guild_id, scope, key);
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_settings_history_changed_at
                ON settings_history (changed_at);
                """
            )

            # Load existing settings
            rows = await conn.fetch("SELECT guild_id, scope, key, value FROM bot_settings")
            loaded_count = 0
            loaded_guilds: set[int] = set()
            for row in rows:
                composite_key = (row["guild_id"], row["scope"], row["key"])
                definition = self._definitions.get((row["scope"], row["key"]))
                if not definition:
                    # fyi.* keys are stored via set_raw (no SettingDefinition) — expected, suppress noise.
                    if row["scope"] != "fyi":
                        log_database_event("UNKNOWN_SETTING", details=f"Guild {row['guild_id']}: {row['scope']}.{row['key']}")
                    continue  # Unknown setting stored earlier; ignore gracefully.
                decoded = self._decode_value(row["value"], definition)
                self._overrides[composite_key] = decoded
                loaded_guilds.add(int(row["guild_id"]))
                loaded_count += 1

            now = time.monotonic()
            for guild_id in loaded_guilds:
                self._guild_fresh_at[guild_id] = now

            log_database_event("SETTINGS_LOADED", details=f"Loaded {loaded_count} settings from database")

        self._pool = pool
        log_database_event("POOL_CREATED", details="Pool created with min_size=1, max_size=10")

    def get(self, scope: str, key: str, guild_id: int = 0, fallback: Any | None = None) -> Any:
        definition = self._definitions.get((scope, key))
        if not definition:
            raise KeyError(f"Setting '{scope}.{key}' is not registered")

        if (guild_id, scope, key) in self._overrides:
            return self._overrides[(guild_id, scope, key)]

        if fallback is not None:
            return fallback

        return definition.default

    def _reload_lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._reload_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._reload_locks[guild_id] = lock
        return lock

    async def reload_guild(self, guild_id: int) -> int:
        """Reload registered settings for one guild from ``bot_settings`` into memory.

        Dashboard (and other) writers update Postgres directly; without this the bot
        keeps startup-time overrides and ignores Disable / setting changes until restart.

        Builds the new override map fully before swapping so concurrent ``get()`` never
        observes a half-cleared guild.
        """
        if not self._pool:
            self._guild_fresh_at[guild_id] = time.monotonic()
            return 0

        async with self._reload_lock_for(guild_id):
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT scope, key, value FROM bot_settings WHERE guild_id = $1",
                    guild_id,
                )

            new_overrides: dict[tuple[int, str, str], Any] = {}
            for row in rows:
                definition = self._definitions.get((row["scope"], row["key"]))
                if not definition:
                    continue
                composite = (guild_id, row["scope"], row["key"])
                try:
                    new_overrides[composite] = self._decode_value(row["value"], definition)
                except Exception as exc:
                    log_database_event(
                        "SETTINGS_RELOAD_DECODE_FAILED",
                        guild_id=guild_id,
                        details=f"{row['scope']}.{row['key']}: {exc}",
                    )

            old_overrides = {
                key: value for key, value in self._overrides.items() if key[0] == guild_id
            }
            # Single dict reassignment — no await between drop and apply (asyncio-safe).
            self._overrides = {
                key: value for key, value in self._overrides.items() if key[0] != guild_id
            } | new_overrides

            self._guild_fresh_at[guild_id] = time.monotonic()
            for listener in self._reload_listeners:
                try:
                    listener(guild_id)
                except Exception as exc:
                    log_database_event(
                        "SETTINGS_RELOAD_LISTENER_FAILED",
                        guild_id=guild_id,
                        details=str(exc),
                    )

            touched = set(old_overrides) | set(new_overrides)
            for composite in touched:
                _gid, scope, key = composite
                new_value = self.get(scope, key, guild_id)
                old_value = old_overrides.get(composite, _MISSING)
                if old_value is _MISSING or old_value != new_value:
                    await self._notify(scope, key, guild_id, new_value)

            loaded = len(new_overrides)
            log_database_event(
                "SETTINGS_RELOADED",
                guild_id=guild_id,
                details=f"Reloaded {loaded} settings from database",
            )
            return loaded

    async def ensure_fresh(
        self,
        guild_id: int,
        *,
        ttl: float = DEFAULT_GUILD_SETTINGS_TTL,
    ) -> None:
        """Reload guild settings from DB when the in-memory snapshot is older than ``ttl``."""
        if guild_id <= 0:
            return
        now = time.monotonic()
        last = self._guild_fresh_at.get(guild_id, 0.0)
        if ttl > 0 and (now - last) < ttl:
            return
        await self.reload_guild(guild_id)

    def is_overridden(self, scope: str, key: str, guild_id: int = 0) -> bool:
        return (guild_id, scope, key) in self._overrides

    def scopes(self) -> list[str]:
        return sorted({scope for scope, _ in self._definitions})

    def list_scope(self, scope: str, guild_id: int = 0) -> list[tuple[SettingDefinition, Any, bool]]:
        items: list[tuple[SettingDefinition, Any, bool]] = []
        for (registered_scope, registered_key), definition in self._definitions.items():
            if registered_scope != scope:
                continue
            value = self.get(registered_scope, registered_key, guild_id)
            overridden = self.is_overridden(registered_scope, registered_key, guild_id)
            items.append((definition, value, overridden))
        items.sort(key=lambda item: item[0].key)
        return items

    async def set(self, scope: str, key: str, value: Any, guild_id: int = 0, updated_by: int | None = None) -> Any:
        definition = self._definitions.get((scope, key))
        if not definition:
            raise KeyError(f"Setting '{scope}.{key}' is not registered")

        coerced = self._coerce_value(value, definition)

        if not self._dsn or not self._pool:
            # Run in-memory mode (useful for tests) if no database is configured.
            self._overrides[(guild_id, scope, key)] = coerced
            await self._notify(scope, key, guild_id, coerced)
            return coerced

        payload = json.dumps(coerced)
        try:
            async with self._pool.acquire() as conn:
                # Get current value for history tracking
                existing_row = await conn.fetchrow(
                    "SELECT value FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                    guild_id, scope, key
                )
                existing_value: Any | None = existing_row["value"] if existing_row else None

                await conn.execute(
                    """
                    INSERT INTO bot_settings (guild_id, scope, key, value, value_type, updated_by, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, NOW())
                    ON CONFLICT(guild_id, scope, key)
                    DO UPDATE SET value = EXCLUDED.value, value_type = EXCLUDED.value_type,
                                  updated_by = EXCLUDED.updated_by, updated_at = NOW();
                    """,
                    guild_id,
                    scope,
                    key,
                    payload,
                    definition.value_type,
                    updated_by,
                )

                # Record history
                change_type = "created" if existing_row is None else "updated"
                await conn.execute(
                    """
                    INSERT INTO settings_history
                    (guild_id, scope, key, old_value, new_value, value_type, changed_by, change_type)
                    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8)
                    """,
                    guild_id,
                    scope,
                    key,
                    existing_value,
                    payload,
                    definition.value_type,
                    updated_by,
                    change_type,
                )
        except (pg_exceptions.PostgresError, ConnectionError, OSError) as e:
            log_database_event("CONNECTION_LOST", guild_id=guild_id, details=f"During set operation: {e}")
            # Try to reconnect and retry once
            try:
                await self._init_pool_with_retry(attempts=3)
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO bot_settings (guild_id, scope, key, value, value_type, updated_by, updated_at)
                        VALUES ($1, $2, $3, $4::jsonb, $5, $6, NOW())
                        ON CONFLICT(guild_id, scope, key)
                        DO UPDATE SET value = EXCLUDED.value, value_type = EXCLUDED.value_type,
                                      updated_by = EXCLUDED.updated_by, updated_at = NOW();
                        """,
                        guild_id,
                        scope,
                        key,
                        payload,
                        definition.value_type,
                        updated_by,
                    )
                log_database_event("RECONNECT_SUCCESS", guild_id=guild_id, details="Successfully reconnected and saved setting")
            except Exception as retry_error:
                log_database_event("RECONNECT_FAILED", guild_id=guild_id, details=f"Failed to reconnect: {retry_error}")
                raise retry_error

        self._overrides[(guild_id, scope, key)] = coerced
        await self._notify(scope, key, guild_id, coerced)

        # Log to operational events
        log_operational_event(
            EventType.SETTINGS_CHANGED,
            f"Setting updated: {scope}.{key} = {value}",
            guild_id=guild_id,
            details={
                "scope": scope,
                "key": key,
                "value": str(value)[:100],  # truncate lange values
                "updated_by": updated_by,
                "action": "set",
                "source": "command"
            }
        )
        
        return coerced

    async def get_raw(self, scope: str, key: str, guild_id: int = 0, fallback: Any = None) -> Any:
        """Read a setting from storage without requiring a registered definition (e.g. internal fyi flags). Only scope 'fyi' is allowed. Uses in-memory store when pool is unavailable."""
        if scope != "fyi":
            return fallback
        composite = (guild_id, scope, key)
        if not self._pool:
            return self._raw_overrides.get(composite, fallback)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                    guild_id, scope, key,
                )
                if row is None:
                    return self._raw_overrides.get(composite, fallback)
                val = row["value"]
                return val if val is not None else self._raw_overrides.get(composite, fallback)
        except Exception as e:
            log_database_event("RAW_GET_FAILED", guild_id=guild_id, details=f"scope={scope} key={key} error={e}")
            return self._raw_overrides.get(composite, fallback)

    async def set_raw(self, scope: str, key: str, value: Any, guild_id: int = 0) -> None:
        """Write a setting to storage without requiring a registered definition (e.g. internal fyi flags). Only scope 'fyi' is allowed. Persists in-memory when pool is unavailable."""
        if scope != "fyi":
            return
        composite = (guild_id, scope, key)
        self._raw_overrides[composite] = value
        if not self._pool:
            return
        try:
            payload = json.dumps(value)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO bot_settings (guild_id, scope, key, value, value_type, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, 'internal', NOW())
                    ON CONFLICT(guild_id, scope, key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                    """,
                    guild_id, scope, key, payload,
                )
        except Exception as e:
            log_database_event("RAW_SET_FAILED", guild_id=guild_id, details=f"scope={scope} key={key} error={e}")

    async def clear_raw(self, scope: str, key: str, guild_id: int = 0) -> None:
        """Remove a raw setting (e.g. to reset an FYI flag for testing). Only scope 'fyi' is allowed. Clears in-memory when pool is unavailable."""
        if scope != "fyi":
            return
        composite = (guild_id, scope, key)
        self._raw_overrides.pop(composite, None)
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                    guild_id, scope, key,
                )
        except Exception as e:
            log_database_event("RAW_CLEAR_FAILED", guild_id=guild_id, details=f"scope={scope} key={key} error={e}")

    async def clear(self, scope: str, key: str, guild_id: int = 0, updated_by: int | None = None) -> None:
        if (guild_id, scope, key) not in self._overrides:
            return

        if self._dsn and self._pool:
            try:
                async with self._pool.acquire() as conn:
                    # Get current value for history tracking before deletion
                    existing_row = await conn.fetchrow(
                        "SELECT value FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                        guild_id, scope, key
                    )
                    existing_value: Any | None = existing_row["value"] if existing_row else None

                    await conn.execute(
                        "DELETE FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                        guild_id,
                        scope,
                        key,
                    )

                    # Record deletion in history
                    if existing_row:
                        definition = self._definitions.get((scope, key))
                        await conn.execute(
                            """
                            INSERT INTO settings_history
                            (guild_id, scope, key, old_value, new_value, value_type, changed_by, change_type)
                            VALUES ($1, $2, $3, $4::jsonb, NULL, $5, $6, 'deleted')
                            """,
                            guild_id,
                            scope,
                            key,
                            existing_value,
                            definition.value_type if definition else None,
                            updated_by,
                    )
            except (pg_exceptions.PostgresError, ConnectionError, OSError) as e:
                log_database_event("CONNECTION_LOST", guild_id=guild_id, details=f"During clear operation: {e}")
                # Try to reconnect and retry once
                try:
                    await self._init_pool_with_retry(attempts=3)
                    async with self._pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM bot_settings WHERE guild_id = $1 AND scope = $2 AND key = $3",
                            guild_id,
                            scope,
                            key,
                        )
                    log_database_event("RECONNECT_SUCCESS", guild_id=guild_id, details="Successfully reconnected and cleared setting")
                except Exception as retry_error:
                    log_database_event("RECONNECT_FAILED", guild_id=guild_id, details=f"Failed to reconnect: {retry_error}")
                    raise retry_error

        self._overrides.pop((guild_id, scope, key), None)
        value = self.get(scope, key, guild_id)
        await self._notify(scope, key, guild_id, value)
        
        # Log to operational events
        log_operational_event(
            EventType.SETTINGS_CHANGED,
            f"Setting cleared: {scope}.{key}",
            guild_id=guild_id,
            details={
                "scope": scope,
                "key": key,
                "updated_by": updated_by,
                "action": "clear",
                "source": "command"
            }
        )

    def add_listener(self, scope: str, key: str, listener: SettingListener) -> None:
        composite_key = (scope, key)
        listeners = self._listeners.setdefault(composite_key, [])
        listeners.append(listener)

    def add_global_listener(self, listener: GlobalSettingListener) -> None:
        """Register a listener that fires on every setting change with (scope, key, guild_id, value)."""
        self._global_listeners.append(listener)

    def add_reload_listener(self, listener: ReloadListener) -> None:
        """Register a sync callback invoked after ``reload_guild`` (guild_id)."""
        self._reload_listeners.append(listener)

    async def _notify(self, scope: str, key: str, guild_id: int, value: Any) -> None:
        per_key = self._listeners.get((scope, key))
        if per_key:
            for listener in per_key:
                asyncio.create_task(listener(value))
        for listener in self._global_listeners:
            asyncio.create_task(listener(scope, key, guild_id, value))

    def _coerce_value(self, value: Any, definition: SettingDefinition) -> Any:
        if value is None:
            if definition.allow_null:
                return None
            raise ValueError(f"Setting '{definition.scope}.{definition.key}' cannot be null")

        expected_type = definition.value_type

        if expected_type == "bool":
            if isinstance(value, bool):
                coerced = value
            elif isinstance(value, str):
                lowered = value.lower()
                if lowered in {"true", "1", "yes", "on"}:
                    coerced = True
                elif lowered in {"false", "0", "no", "off"}:
                    coerced = False
                else:
                    raise ValueError("Boolean setting expects true/false")
            else:
                coerced = bool(value)
        elif expected_type == "int":
            coerced = int(value)
        elif expected_type == "float":
            coerced = float(value)
        elif expected_type in {"channel", "role"}:
            if hasattr(value, "id"):
                coerced = int(value.id)
            else:
                coerced = int(value)
        elif expected_type == "str":
            coerced = str(value)
        else:
            coerced = value

        if definition.choices and coerced not in definition.choices:
            raise ValueError(
                f"Setting '{definition.scope}.{definition.key}' expects one of {list(definition.choices)}"
            )

        if isinstance(coerced, (int, float)):
            if definition.min_value is not None and coerced < definition.min_value:
                raise ValueError(
                    f"Setting '{definition.scope}.{definition.key}' cannot be smaller than {definition.min_value}"
                )

            if definition.max_value is not None and coerced > definition.max_value:
                raise ValueError(
                    f"Setting '{definition.scope}.{definition.key}' cannot be larger than {definition.max_value}"
                )

        return coerced

    def _decode_value(self, stored: Any, definition: SettingDefinition) -> Any:
        if stored is None:
            return None

        expected_type = definition.value_type
        if expected_type == "int":
            if type(stored) is int:
                return stored
            return int(_unwrap_quoted_scalar_str(str(stored)))
        if expected_type == "float":
            if isinstance(stored, (int, float)) and not isinstance(stored, bool):
                return float(stored)
            return float(_unwrap_quoted_scalar_str(str(stored)))
        if expected_type in {"channel", "role"}:
            if type(stored) is int:
                return stored
            return int(_unwrap_quoted_scalar_str(str(stored)))
        if expected_type == "bool":
            if isinstance(stored, bool):
                return stored
            if isinstance(stored, str):
                return stored.lower() in {"true", "1"}
            return bool(stored)
        if expected_type == "str":
            return str(stored)
        return stored
