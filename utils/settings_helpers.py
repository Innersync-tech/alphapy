"""
Settings Helper Utilities

Cached settings access and bulk operations to reduce database queries and
boilerplate code across cogs. Provides type-safe getters with automatic
coercion and caching.
"""

import inspect
import json
from collections import OrderedDict
from typing import Any

from utils.db_helpers import acquire_transactional
from utils.logger import logger
from utils.settings_service import DEFAULT_GUILD_SETTINGS_TTL, SettingsService

# Scopes whose `{scope}.enabled` defaults to False when unset.
_MODULE_ENABLED_DEFAULT_FALSE = frozenset({"agents"})

MODULE_DISABLED_MSG = (
    "This module is disabled in this server. Ask an admin to enable it in the Alphapy Dashboard."
)


def is_module_enabled(source: Any, guild_id: int, scope: str, *, fallback: bool | None = None) -> bool:
    """Return True if `{scope}.enabled` is on for the guild.

    ``source`` may be a bot (with ``.settings`` / ``.settings_helper``) or a SettingsService.
    Default fallback is True for all scopes except ``agents`` (False).

    For Discord gates after Dashboard writes, prefer ``is_module_enabled_async`` so the
    in-memory settings snapshot is refreshed from ``bot_settings``.
    """
    if fallback is None:
        fallback = scope not in _MODULE_ENABLED_DEFAULT_FALSE

    # Prefer CachedSettingsHelper on bot-like objects (not on SettingsService itself).
    if not callable(getattr(source, "get", None)):
        helper = getattr(source, "settings_helper", None)
        if helper is not None and callable(getattr(helper, "get_bool", None)):
            return bool(helper.get_bool(scope, "enabled", guild_id, fallback=fallback))

    # SettingsService directly, or bot.settings
    settings = source if callable(getattr(source, "get", None)) else getattr(source, "settings", None)
    if settings is None or not callable(getattr(settings, "get", None)):
        return fallback
    try:
        value = settings.get(scope, "enabled", guild_id, fallback)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value) if value is not None else fallback
    except Exception:
        return fallback


async def is_module_enabled_async(
    source: Any,
    guild_id: int,
    scope: str,
    *,
    fallback: bool | None = None,
    ttl: float = DEFAULT_GUILD_SETTINGS_TTL,
) -> bool:
    """Like ``is_module_enabled``, but reloads guild settings from DB when stale.

    On refresh failure: retries once with ``ttl=0``. If that also fails, returns
    ``False`` (fail closed) so Dashboard Disable cannot be bypassed by a stale cache
    during a transient DB error.

    Non-awaitable ``ensure_fresh`` stubs (e.g. MagicMock in tests) are ignored.
    """
    settings = source if callable(getattr(source, "get", None)) else getattr(source, "settings", None)
    ensure_fresh = getattr(settings, "ensure_fresh", None)
    if callable(ensure_fresh):
        async def _run_fresh(fresh_ttl: float) -> bool:
            """Return True if refresh ran (or was skipped as non-awaitable); False on failure."""
            try:
                result = ensure_fresh(guild_id, ttl=fresh_ttl)
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as exc:
                logger.warning(
                    "settings ensure_fresh failed guild=%s scope=%s ttl=%s: %s",
                    guild_id,
                    scope,
                    fresh_ttl,
                    exc,
                )
                return False

        if not await _run_fresh(ttl):
            if not await _run_fresh(0):
                logger.warning(
                    "settings ensure_fresh retry failed guild=%s scope=%s; treating module as disabled",
                    guild_id,
                    scope,
                )
                return False
    return is_module_enabled(source, guild_id, scope, fallback=fallback)


class CachedSettingsHelper:
    """
    Wrapper around SettingsService that provides type-safe getters with LRU caching.

    Note: SettingsService.get() is already a pure in-memory dict lookup (all settings
    are bulk-loaded at startup). This class adds type coercion and a second LRU layer
    mainly to avoid re-coercing frequently accessed values. The cache has no TTL —
    entries are valid until explicitly invalidated via invalidate_cache() or until
    evicted by size pressure.
    """
    
    def __init__(self, settings: SettingsService, max_cache_size: int = 500):
        """
        Initialize the cached settings helper.

        Args:
            settings: The SettingsService instance to wrap
            max_cache_size: Maximum number of entries in cache (default: 500)
        """
        self._settings = settings
        self._cache: OrderedDict[tuple[str, str, int], Any] = OrderedDict()
        self._cache_enabled = True
        self._max_cache_size = max_cache_size

        # Auto-invalidate this helper's LRU cache whenever any setting changes so
        # cogs always read the current value without needing an explicit TTL.
        async def _on_setting_changed(scope: str, key: str, guild_id: int, value: Any) -> None:
            self._cache.pop(self._get_cache_key(scope, key, guild_id), None)

        settings.add_global_listener(_on_setting_changed)
        add_reload = getattr(settings, "add_reload_listener", None)
        if callable(add_reload):
            add_reload(lambda guild_id: self.clear_cache(guild_id=guild_id))
    
    def _get_cache_key(self, scope: str, key: str, guild_id: int) -> tuple[str, str, int]:
        """Generate cache key from scope, key, and guild_id."""
        return (scope, key, guild_id)
    
    def _evict_if_needed(self) -> None:
        """Evict oldest entry if cache exceeds max size."""
        if len(self._cache) >= self._max_cache_size:
            # Remove oldest (first) entry
            evicted_key = self._cache.popitem(last=False)
            logger.debug(f"Settings cache eviction: size={len(self._cache)}/{self._max_cache_size}, evicted={evicted_key[0]}")
    
    def clear_cache(self, scope: str | None = None, key: str | None = None, guild_id: int | None = None) -> None:
        """
        Clear cached settings. If parameters are provided, only clear matching entries.
        
        Args:
            scope: Optional scope to filter by
            key: Optional key to filter by
            guild_id: Optional guild_id to filter by
        """
        if scope is None and key is None and guild_id is None:
            self._cache.clear()
            return
        
        keys_to_remove = []
        for cache_key in self._cache.keys():
            cache_scope, cache_key_name, cache_guild_id = cache_key
            if (scope is None or cache_scope == scope) and \
               (key is None or cache_key_name == key) and \
               (guild_id is None or cache_guild_id == guild_id):
                keys_to_remove.append(cache_key)
        
        for key_to_remove in keys_to_remove:
            del self._cache[key_to_remove]
    
    def get_int(self, scope: str, key: str, guild_id: int, fallback: int = 0) -> int:
        """
        Cached int getter with type coercion.
        
        Args:
            scope: Settings scope (e.g., "system", "reminders")
            key: Settings key (e.g., "log_channel_id")
            guild_id: Guild ID to get setting for
            fallback: Default value if setting is not found or invalid
            
        Returns:
            int: The setting value as an integer
        """
        cache_key = self._get_cache_key(scope, key, guild_id)
        
        if self._cache_enabled and cache_key in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(cache_key)
            cached_value = self._cache[cache_key]
            if isinstance(cached_value, int):
                return cached_value
        
        try:
            value = self._settings.get(scope, key, guild_id, fallback)
            coerced = int(value) if value else fallback
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = coerced
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return coerced
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to coerce setting {scope}.{key} to int: {e}")
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = fallback
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return fallback
    
    def get_bool(self, scope: str, key: str, guild_id: int, fallback: bool = False) -> bool:
        """
        Cached bool getter with type coercion.
        
        Args:
            scope: Settings scope
            key: Settings key
            guild_id: Guild ID
            fallback: Default value if setting is not found or invalid
            
        Returns:
            bool: The setting value as a boolean
        """
        cache_key = self._get_cache_key(scope, key, guild_id)
        
        if self._cache_enabled and cache_key in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(cache_key)
            cached_value = self._cache[cache_key]
            if isinstance(cached_value, bool):
                return cached_value
        
        try:
            value = self._settings.get(scope, key, guild_id, fallback)
            if isinstance(value, bool):
                coerced = value
            elif isinstance(value, str):
                coerced = value.lower() in ("true", "1", "yes", "on")
            else:
                coerced = bool(value) if value else fallback
            
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = coerced
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return coerced
        except Exception as e:
            logger.warning(f"Failed to coerce setting {scope}.{key} to bool: {e}")
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = fallback
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return fallback
    
    def get_str(self, scope: str, key: str, guild_id: int, fallback: str = "") -> str:
        """
        Cached string getter.
        
        Args:
            scope: Settings scope
            key: Settings key
            guild_id: Guild ID
            fallback: Default value if setting is not found
            
        Returns:
            str: The setting value as a string
        """
        cache_key = self._get_cache_key(scope, key, guild_id)
        
        if self._cache_enabled and cache_key in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(cache_key)
            cached_value = self._cache[cache_key]
            if isinstance(cached_value, str):
                return cached_value
        
        try:
            value = self._settings.get(scope, key, guild_id, fallback)
            coerced = str(value) if value else fallback
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = coerced
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return coerced
        except Exception as e:
            logger.warning(f"Failed to coerce setting {scope}.{key} to str: {e}")
            if self._cache_enabled:
                self._evict_if_needed()  # Before adding new entry
                self._cache[cache_key] = fallback
                self._cache.move_to_end(cache_key)  # Mark as recently used
            return fallback
    
    async def set_bulk(
        self,
        guild_id: int,
        updates: dict[tuple[str, str], Any],
        updated_by: int | None = None
    ) -> None:
        """
        Bulk settings update using a single database transaction.

        All values are upserted atomically in one roundtrip, then in-memory
        overrides are updated and listeners are fired per key.

        Args:
            guild_id: Guild ID to update settings for
            updates: Dictionary mapping (scope, key) tuples to values
            updated_by: Optional user ID who made the changes
        """
        if not updates:
            return

        service = self._settings

        # Validate and coerce all values up front; skip unknown settings.
        coerced: dict[tuple[str, str], Any] = {}
        rows = []
        for (scope, key), value in updates.items():
            definition = service._definitions.get((scope, key))
            if not definition:
                logger.error(f"set_bulk: unregistered setting {scope}.{key}, skipping")
                continue
            coerced_value = service._coerce_value(value, definition)
            coerced[(scope, key)] = coerced_value
            rows.append((guild_id, scope, key, json.dumps(coerced_value), definition.value_type, updated_by))

        if not rows:
            return

        # If no pool (e.g. tests), fall back to per-key in-memory updates.
        if not service._dsn or not service._pool:
            for (scope, key), coerced_value in coerced.items():
                service._overrides[(guild_id, scope, key)] = coerced_value
                await service._notify(scope, key, guild_id, coerced_value)
            return

        # Single transaction — one DB roundtrip for all keys.
        try:
            async with acquire_transactional(service._pool) as conn:
                await conn.executemany(
                    """
                    INSERT INTO bot_settings (guild_id, scope, key, value, value_type, updated_by, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, NOW())
                    ON CONFLICT (guild_id, scope, key)
                    DO UPDATE SET value = EXCLUDED.value,
                                  value_type = EXCLUDED.value_type,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = NOW()
                    """,
                    rows,
                )
        except Exception as e:
            logger.error(f"set_bulk: transaction failed: {e}")
            raise

        # Update in-memory overrides and fire listeners (global listener handles cache invalidation).
        for (scope, key), coerced_value in coerced.items():
            service._overrides[(guild_id, scope, key)] = coerced_value
            await service._notify(scope, key, guild_id, coerced_value)
    
    async def invalidate_cache(self, scope: str, key: str, guild_id: int) -> None:
        """
        Invalidate cache for a specific setting (useful after external updates).
        
        Args:
            scope: Settings scope
            key: Settings key
            guild_id: Guild ID
        """
        cache_key = self._get_cache_key(scope, key, guild_id)
        self._cache.pop(cache_key, None)
