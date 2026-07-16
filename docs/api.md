---
title: API Reference
description: HTTP API endpoints for Alphapy dashboard and integrations.
---

# API Reference

Complete API documentation for the Alphapy Discord Bot FastAPI server.

## Base URL

All API endpoints are prefixed with `/api` unless otherwise noted.

## Endpoint Categories

- **Health & Status**: Basic health checks and monitoring (`/api/health`, `/api/health/history`)
- **Metrics & Analytics**: Dashboard metrics and command analytics (`/api/dashboard/metrics`, `/top-commands`)
- **Dashboard Configuration**: Web dashboard endpoints for managing settings, onboarding, auto-moderation (requires Supabase JWT)
- **Auto-Moderation**: Complete auto-moderation rule management with analytics (`/api/dashboard/{guild_id}/automod/*`)
- **Onboarding Management**: Questions, rules, and flow configuration (`/api/dashboard/{guild_id}/onboarding/*`)
- **Reminder Management**: User-facing reminder CRUD operations (requires Supabase JWT subject match)
- **Agent Sessions**: Cross-platform `/agent` REST API for App/Mind (requires Supabase JWT + Discord link)
- **Verification Queue**: Dashboard manual-review tickets (requires dashboard Discord admin auth)
- **Exports**: CSV export endpoints for tickets and FAQ
- **Webhooks**: Incoming webhooks from Core-API and GitHub Actions; validated via `X-Webhook-Signature` (includes app-reflections, discord-link, premium-invalidate, founder, legal-update)

**Note for Mind Dashboard**: Mind primarily uses:
- `/api/dashboard/metrics` (or `/api/metrics` alias) for live metrics
- `/api/health` for health checks
- Dashboard configuration endpoints for web UI management
- `/api/observability` for internal/ops observability snapshots (not part of Alphapy dashboard configuration flows)

## Authentication

Most endpoints require authentication via:
- **Supabase JWT**: `Authorization: Bearer <token>` (required for user-scoped and dashboard endpoints)
- **API Key**: `X-API-Key` (used for internal service endpoints such as `/api/observability`)

Important:
- User identity is derived from verified JWT claims (`sub`) only.
- `X-User-Id` is not trusted for authentication or authorization.

Example (JWT):
```bash
curl -H "Authorization: Bearer <supabase_jwt>" \
  https://your-bot-url/api/reminders/<supabase_user_uuid>
```

Dashboard endpoints example:
```bash
curl -H "Authorization: Bearer supabase_token" \
  https://your-bot-url/api/dashboard/123456789/settings
```

## Endpoints

### Health & Status

#### `GET /api/health`

Enhanced health check endpoint with detailed metrics.

**Response:**
```json
{
  "service": "alphapy",
  "version": "3.6.0",
  "uptime_seconds": 3600,
  "db_status": "ok",
  "timestamp": "2026-01-21T12:00:00Z",
  "guild_count": 2,
  "active_commands_24h": 150,
  "gpt_status": "operational",
  "database_pool_size": 5
}
```

#### `GET /status`

Simple status check endpoint (legacy, no authentication required).

**Response:**
```json
{
  "online": true,
  "latency": 0,
  "uptime": "60 min"
}
```

#### `GET /api/observability`

Internal observability snapshot endpoint.

This endpoint is intended for Mind/internal monitoring and operations use. It is not a `/api/dashboard/*` configuration endpoint.
Requires `X-Api-Key` with the configured service key.

Returns rolling in-memory request metrics for API and webhook traffic:
- success rate
- latency percentiles (`p50`, `p95`, `p99`)
- request counts

#### `GET /api/hermit/growth-checkins`

Service-key broker for Core/Hermit progress loops. Reads Railway `growth_checkins` (plaintext Discord `/growthcheckin` content). Requires `X-API-Key` = Alphapy `API_KEY`.

Query: `user_id` (Discord snowflake), optional `lookback_days` (default 30), `limit` (default 20).

Response includes `source: "railway"` and `items[]` with `content` shaped as `Goal:` / `Obstacle:` / `Feeling:` lines plus optional `future_message` (Grok reply).

**Response:**
```json
{
  "api": {
    "requests": 120,
    "success_rate": 0.9917,
    "latency_ms": { "p50": 12.4, "p95": 43.9, "p99": 81.2 }
  },
  "webhooks": {
    "requests": 45,
    "success_rate": 1.0,
    "latency_ms": { "p50": 7.1, "p95": 18.0, "p99": 28.3 }
  }
}
```

All responses now include an `X-Request-ID` header for request correlation.

**Fields:**
- `service`: Service name
- `version`: Bot version
- `uptime_seconds`: Uptime in seconds
- `db_status`: Database status (`ok`, `not_initialized`, or `error:...`)
- `timestamp`: ISO timestamp of check
- `guild_count`: Number of guilds bot is connected to (optional)
- `active_commands_24h`: Number of commands executed in last 24 hours (optional)
- `gpt_status`: Grok/LLM service status (`operational`, `degraded`, `error`) (optional)
- `database_pool_size`: Current size of the database connection pool (managed automatically by `asyncpg`)

#### `GET /api/health/history`

Get historical health check data for trend analysis.

**Query Parameters:**
- `hours` (optional, default: 24): Number of hours to look back
- `limit` (optional, default: 100): Maximum number of records to return

**Response:**
```json
{
  "history": [
    {
      "service": "alphapy",
      "version": "3.6.0",
      "uptime_seconds": 3600,
      "db_status": "ok",
      "guild_count": 2,
      "active_commands_24h": 150,
      "gpt_status": "operational",
      "database_pool_size": 5,
      "checked_at": "2026-01-21T12:00:00Z"
    }
  ],
  "period_hours": 24,
  "total_records": 1
}
```

### Metrics & Analytics

#### `GET /api/dashboard/metrics`

Comprehensive dashboard metrics including bot status, Grok/LLM stats, reminders, tickets, and command usage.

**Authentication:** Required (Supabase JWT token)

**Query Parameters:**
- `guild_id` (optional): Filter metrics by guild ID

**Response:**
```json
{
  "bot": {
    "version": "3.6.0",
    "codename": "Lifecycle Manager",
    "online": true,
    "latency_ms": 45.2,
    "uptime_seconds": 3600,
    "uptime_human": "1 hour",
    "commands_loaded": 30,
    "guilds": [...]
  },
  "gpt": {
    "last_success_time": "2026-01-21T12:00:00Z",
    "last_error_time": "2026-01-21T12:05:00Z",
    "last_error_type": "RateLimitError: ...",
    "average_latency_ms": 1200,
    "total_tokens_session": 5000,
    "current_model": "grok-3",
    "last_user_id": 123456789,
    "success_count": 100,
    "error_count": 2,
    "rate_limit_hits": 1,
    "last_rate_limit_time": "2026-01-21T12:05:00Z",
    "last_success_latency_ms": 980,
    "recent_successes": [...],
    "recent_errors": [...]
  },
  "reminders": {
    "total": 15,
    "recurring": 10,
    "one_off": 5,
    "next_event_time": "2026-01-21T19:00:00Z",
    ...
  },
  "tickets": {
    "total": 50,
    "open_count": 5,
    "per_status": {...},
    "open_ticket_ids": [123, 456, 789],
    "open_items": [
      {
        "id": 123,
        "username": "user123",
        "status": "open",
        "channel_id": 987654321,
        "created_at": "2026-01-21T12:00:00Z"
      }
    ],
    ...
  },
  "command_usage": {
    "top_commands": [
      {"command_name": "add_reminder", "usage_count": 45},
      {"command_name": "ticket", "usage_count": 30}
    ],
    "total_commands_24h": 150,
    "period_days": 7
  },
  "infrastructure": {
    "database_up": true,
    "pool_size": 5,
    "checked_at": "2026-01-21T12:00:00Z"
  },
  "premium_metrics": {
    "premium_checks_total": 120,
    "premium_checks_core_api": 80,
    "premium_checks_local": 40,
    "premium_cache_hits": 200,
    "premium_transfers_count": 3,
    "premium_cache_size": 25,
    "premium_guild_cache_size": 8,
    "premium_guild_cache_hits": 90,
    "premium_guild_cache_misses": 12
  },
  "agent_sessions": {
    "enabled": true,
    "active_sessions": 2,
    "started_24h": 5,
    "completed_24h": 3,
    "active_origin_discord": 1,
    "active_origin_app": 1
  }
}
```

The optional `agent_sessions` block provides aggregate `/agent` session observability for Mind (no message content):

- `enabled`: `ALPHAPY_AGENTS_ENABLED` gate
- `active_sessions`: rows in `agent_sessions` with `status = active`
- `started_24h` / `completed_24h`: rolling 24h window counts
- `active_origin_discord` / `active_origin_app`: breakdown from session `metadata.origin_channel` when sessions are active

Same aggregates are also appended to telemetry ingest `notes` as `agents: …` for Supabase snapshot fallback.

The optional `premium_metrics` block provides observability for the Premium guard:

- `premium_checks_total`: Total number of premium checks performed
- `premium_checks_core_api`: Checks served by the Core-API
- `premium_checks_local`: Checks served from local database/cache
- `premium_cache_hits`: Number of cache hits when resolving premium status
- `premium_transfers_count`: Number of premium transfers between guilds
- `premium_cache_size`: Current in-memory cache size for premium status
- `premium_guild_cache_size`: Current in-memory cache size for guild-level premium checks
- `premium_guild_cache_hits`: Cache hits for `guild_has_premium(guild_id)`
- `premium_guild_cache_misses`: Cache misses for `guild_has_premium(guild_id)`

The optional `cache_metrics` block now also includes cache metrics for:

- `automod_rules_cache_*`: active-rules and rule-list cache size/hit/miss counters from `RuleProcessor`
- `engagement_feature_flag_cache_*`: cache size/hit/miss counters for engagement `*_enabled` checks
- `engagement_food_channels_cache_*`: cache size/hit/miss counters for engagement food-channel resolution

#### `GET /api/metrics`

Alias for `/api/dashboard/metrics` - provided for compatibility with Mind monitoring system.

**Authentication:** Required (Supabase JWT token)

**Query Parameters:**
- `guild_id` (optional): Filter metrics by guild ID

**Response:** Same as `/api/dashboard/metrics`

#### `GET /top-commands`

Command usage analytics endpoint.

**Query Parameters:**
- `guild_id` (optional): Filter by guild ID
- `days` (optional, default: 7): Number of days to look back
- `limit` (optional, default: 10): Maximum number of commands to return

**Response:**
```json
{
  "commands": {
    "add_reminder": 45,
    "ticket": 30,
    "reminder_list": 20
  },
  "period_days": 7,
  "guild_id": null,
  "total_commands": 3
}
```

### Dashboard Configuration Endpoints

These endpoints are used by the web dashboard (Mind) for configuration management.

#### `GET /api/dashboard/settings/{guild_id}`

Get all settings for a specific guild, organized by category.

**Authentication:** Required (Supabase JWT token)

**Path Parameters:**
- `guild_id` (required): Discord guild ID

**Response:**
```json
{
  "system": {
    "log_channel_id": 123456789,
    "rules_channel_id": 987654321,
    "log_level": "verbose"
  },
  "reminders": {
    "enabled": true,
    "default_channel_id": 111222333,
    "allow_everyone_mentions": false
  },
  "embedwatcher": {
    "announcements_channel_id": 444555666,
    "reminder_offset_minutes": 60,
    "gpt_fallback_enabled": true,
    "non_embed_enabled": false,
    "process_bot_messages": false
  },
  "gpt": {
    "model": "grok-3",
    "temperature": 0.7
  },
  "invites": {
    "enabled": true,
    "announcement_channel_id": 123456789,
    "with_inviter_template": "{member} joined! {inviter} now has {count} invites.",
    "no_inviter_template": "{member} joined, but no inviter data found."
  },
  "gdpr": {
    "enabled": true,
    "channel_id": 123456789
  },
  "automod": {
    "enabled": false,
    "log_channel_id": 123456789,
    "log_actions": true,
    "log_to_database": true
  },
  "onboarding": {
    "enabled": true,
    "mode": "rules_with_questions",
    "completion_role_id": 123456789,
    "join_role_id": 987654321
  },
  "ticketbot": {
    "category_id": 123456789,
    "staff_role_id": 987654321,
    "escalation_role_id": 555666777,
    "idle_days_threshold": 5,
    "auto_close_days_threshold": 14
  },
  "verification": {
    "verified_role_id": 123456789,
    "category_id": 987654321,
    "vision_model": "grok-3"
  }
}
```

#### `POST /api/dashboard/settings/{guild_id}`

Update settings for a specific guild category.

**Authentication:** Required (Supabase JWT token)

**Path Parameters:**
- `guild_id` (required): Discord guild ID

**Request Body:**
```json
{
  "category": "reminders",
  "settings": {
    "default_channel_id": "111222333",
    "allow_everyone_mentions": false
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Updated reminders settings"
}
```

#### `GET /api/dashboard/{guild_id}/onboarding/questions`

Get all onboarding questions for a guild.

**Authentication:** Required (Supabase JWT token)

**Response:**
```json
[
  {
    "id": 1,
    "question": "What is your trading experience?",
    "question_type": "select",
    "options": [{"label": "Beginner", "value": "beginner"}],
    "required": true,
    "enabled": true,
    "step_order": 1
  }
]
```

#### `POST /api/dashboard/{guild_id}/onboarding/questions`

Save or update an onboarding question.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Request Body:** Same structure as GET response



#### `DELETE /api/dashboard/{guild_id}/onboarding/questions/{question_id}`

Delete an onboarding question.

**Authentication:** Required (Supabase JWT token + guild admin access)

#### `GET /api/dashboard/{guild_id}/onboarding/rules`

Get all onboarding rules for a guild.

**Authentication:** Required (Supabase JWT token)

**Response:**
```json
[
  {
    "id": 1,
    "title": "Be Respectful",
    "description": "Treat all members with respect",
    "thumbnail_url": "https://example.com/thumb.png",
    "image_url": "https://example.com/image.png",
    "enabled": true,
    "rule_order": 1
  }
]
```

`thumbnail_url` and `image_url` are optional; shown as thumbnail (right) and image (bottom) in rule embeds.

#### `POST /api/dashboard/{guild_id}/onboarding/rules`

Save or update an onboarding rule.

**Authentication:** Required (Supabase JWT token + guild admin access)

#### `DELETE /api/dashboard/{guild_id}/onboarding/rules/{rule_id}`

Delete an onboarding rule.

**Authentication:** Required (Supabase JWT token + guild admin access)

#### `POST /api/dashboard/{guild_id}/onboarding/reorder`

Reorder onboarding questions and rules.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Request Body:**
```json
{
  "questions": [1, 3, 2],
  "rules": [2, 1]
}
```

**Response:**
```json
{
  "success": true
}
```

#### `GET /api/dashboard/{guild_id}/settings/history`

Get settings change history for a guild.

**Authentication:** Required (Supabase JWT token)

**Query Parameters:**
- `scope` (optional): Filter by scope (e.g., "reminders")
- `key` (optional): Filter by specific key
- `limit` (optional, default: 50): Maximum number of records

**Response:**
```json
[
  {
    "id": 1,
    "scope": "reminders",
    "key": "default_channel_id",
    "old_value": "111222333",
    "new_value": "444555666",
    "changed_by": 123456789,
    "changed_at": "2026-01-21T12:00:00Z",
    "change_type": "updated"
  }
]
```

#### `POST /api/dashboard/{guild_id}/settings/rollback/{history_id}`

Rollback a setting to a previous value.

**Authentication:** Required (Supabase JWT token)

**Response:**
```json
{
  "success": true,
  "message": "Rolled back reminders.default_channel_id to previous value"
}
```

### Auto-Moderation Management

#### `GET /api/dashboard/{guild_id}/automod/rules`

List all auto-moderation rules for a guild.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Response:**
```json
[
  {
    "id": 1,
    "guild_id": 123456789,
    "rule_type": "content",
    "name": "No Bad Words",
    "enabled": true,
    "config": {
      "content_type": "bad_words",
      "words": ["spam", "curse"]
    },
    "action_type": "warn",
    "action_config": {
      "message": "Please watch your language!"
    },
    "severity": 1,
    "created_by": 987654321,
    "created_at": "2026-01-21T12:00:00Z",
    "updated_at": "2026-01-21T12:00:00Z",
    "is_premium": false
  }
]
```

#### `POST /api/dashboard/{guild_id}/automod/rules`

Create a new auto-moderation rule.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Request Body:**
```json
{
  "rule_type": "content",
  "name": "No Links",
  "enabled": true,
  "config": {
    "content_type": "links",
    "allow_links": false,
    "whitelist": ["discord.com"],
    "blacklist": ["spam.com"]
  },
  "action_type": "delete",
  "action_config": {},
  "severity": 2
}
```

**Response:** Returns the created rule with assigned ID

#### `PUT /api/dashboard/{guild_id}/automod/rules/{rule_id}`

Update an existing auto-moderation rule.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Request Body:**
```json
{
  "name": "Updated Rule Name",
  "enabled": false,
  "severity": 3
}
```

**Response:** Returns the updated rule

#### `DELETE /api/dashboard/{guild_id}/automod/rules/{rule_id}`

Delete an auto-moderation rule.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Response:**
```json
{
  "success": true
}
```

#### `POST /api/dashboard/{guild_id}/automod/invalidate-cache`

Drop the in-memory AutoMod rules cache for a guild after dashboard direct DB writes (Alphapy Dashboard calls this after create/update/delete rule operations).

**Authentication:** Required (`X-Api-Key` + `X-Discord-User-Id` with guild admin access; same as verification queue / discord-meta)

**Response:**
```json
{
  "success": true
}
```

#### `GET /api/dashboard/{guild_id}/verification/queue`

List verification tickets awaiting manual review (no screenshot content).

**Authentication:** Required (`X-Api-Key` + `X-Discord-User-Id` with guild admin access; same as automod invalidate-cache)

**Response:**
```json
[
  {
    "id": 42,
    "user_id": 123456789,
    "channel_id": 987654321,
    "status": "manual_review",
    "ai_reason": "Payment date unclear",
    "ai_can_verify": false,
    "ai_needs_manual_review": true,
    "payment_date": "2026-07-01",
    "created_at": "2026-07-13T12:00:00Z"
  }
]
```

#### `POST /api/dashboard/{guild_id}/verification/{ticket_id}/resolve`

Approve or reject a manual verification ticket. Resolution runs on the bot event loop (role assignment, DB update, channel cleanup).

**Authentication:** Required (`X-Api-Key` + `X-Discord-User-Id` with guild admin access)

**Request body:**
```json
{
  "outcome": "approved",
  "reason": "Optional reject reason shown to the member"
}
```

**Response:**
```json
{
  "success": true
}
```

#### `GET /api/dashboard/{guild_id}/automod/stats`

Get auto-moderation statistics and analytics.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Response:**
```json
{
  "total_rules": 5,
  "enabled_rules": 3,
  "rules_by_type": {
    "content": 2,
    "spam": 1,
    "links": 1,
    "mentions": 1
  },
  "total_violations": 127,
  "violations_today": 8,
  "violations_week": 45,
  "top_violated_rules": [
    {
      "name": "No Bad Words",
      "rule_type": "content",
      "violation_count": 23
    }
  ]
}
```

#### `GET /api/dashboard/{guild_id}/automod/violations`

Get recent auto-moderation violation logs.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Query Parameters:**
- `limit` (optional, default: 50): Maximum number of violations to return
- `days` (optional, default: 7): Number of days to look back

**Response:**
```json
[
  {
    "id": 1,
    "guild_id": 123456789,
    "user_id": 987654321,
    "message_id": 111222333,
    "channel_id": 444555666,
    "rule_id": 1,
    "action_taken": "warn",
    "message_content": "This message contained bad words",
    "ai_analysis": null,
    "context": {},
    "timestamp": "2026-01-21T12:00:00Z",
    "moderator_id": null
  }
]
```

#### `GET /api/dashboard/{guild_id}/automod/settings`

Get auto-moderation specific settings.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Response:**
```json
{
  "enabled": false,
  "log_channel_id": 123456789,
  "log_actions": true,
  "log_to_database": true
}
```

#### `POST /api/dashboard/{guild_id}/automod/settings`

Update auto-moderation settings.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Request Body:**
```json
{
  "enabled": true,
  "log_channel_id": 123456789,
  "log_actions": true,
  "log_to_database": true
}
```

**Response:**
```json
{
  "success": true
}
```

#### `GET /api/dashboard/{guild_id}/gdpr`

Get GDPR acceptance statistics for a guild.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Response:**
```json
{
  "guild_id": 123456789,
  "acceptance_count": 42
}
```

---

#### `GET /api/dashboard/logs`

Get operational logs (reconnect, disconnect, etc.) for the Mind dashboard. Requires guild admin access (verified via Supabase profile's Discord ID). Global events (e.g. `BOT_RECONNECT`, `BOT_DISCONNECT`) are included for any guild request.

**Authentication:** Required (Supabase JWT token + guild admin access)

**Query Parameters:**
- `guild_id` (required): Discord guild ID – user must have admin access to this guild
- `limit` (optional, default: 50, max: 100): Maximum number of log entries to return
- `event_types` (optional): Comma-separated list of event types to filter (e.g. `BOT_RECONNECT,BOT_DISCONNECT`)

**Response:**
```json
{
  "logs": [
    {
      "timestamp": "2026-02-10T21:30:00Z",
      "event_type": "BOT_RECONNECT",
      "guild_id": null,
      "message": "Reconnect phase complete: commands synced",
      "details": {"synced": 5, "skipped": 0}
    }
  ]
}
```

**Event types:**
- `BOT_READY` – Bot startup complete
- `BOT_RECONNECT` – Bot reconnected and resynced commands (includes `synced` and `skipped` counts)
- `BOT_DISCONNECT` – Bot disconnected from Discord
- `GUILD_SYNC` – Command sync per guild (success/failure/cooldown, includes `sync_type`: `first_ready` / `reconnect` / `guild_join`)
- `ONBOARDING_ERROR` – Onboarding errors (no rules configured, role assignment failures, member not found)
- `SETTINGS_CHANGED` – Settings changes via commands or API (includes `action`: set/clear/bulk_update/rollback, `source`: command/api)
- `COG_ERROR` – Slash command errors per guild (includes command name, user ID, error type)

### Agent Sessions

Cross-platform agent session API (same runtime as Discord `/agent`). Registered in `agents/http_routes.py`. Requires `ALPHAPY_AGENTS_ENABLED=true` on the deployment.

**Authentication:** Supabase JWT (`Authorization: Bearer <token>`). All routes require an active Discord link (`alphapy_discord_links`); unlinked users receive **403**.

#### `POST /api/agents/sessions`

Start a new agent session (HTTP equivalent of `/agent start`).

**Request body:**
```json
{
  "agent": "reflection",
  "message": "Optional opening message"
}
```

**Response:** `201` — active session with `session_id`, `assistant_message`, `turn_count`, and optional `messages` history.

**Errors:** `409` if a session is already active; `402` if daily `/agent start` quota exceeded.

#### `GET /api/agents/sessions/active`

Return the caller's active session for the given agent (query param `agent`, default `reflection`).

**Response:** Session payload + message history, or **404** if none active.

#### `POST /api/agents/sessions/{session_id}/turns`

Continue an active session (HTTP equivalent of `/agent continue`).

**Request body:**
```json
{
  "message": "Follow-up message"
}
```

#### `POST /api/agents/sessions/{session_id}/complete`

End an active session (HTTP equivalent of `/agent end`). Emits Hermit `gpt_command` on success.

### Reminder Management

Mind and other clients authenticate with a **Supabase JWT**. The `user_id` path field and reminder payload `user_id` must equal the JWT `sub` (Innersync user UUID). Alphapy resolves that UUID to a Discord snowflake via `alphapy_discord_links` only (`resolve_innersync_jwt_sub_to_discord_int()` with default `allow_profile_fallback=False`). Legacy `profiles.discord_id` fallback exists for one-off backfill scripts but is not used on the reminder API hot path. If the user is not linked, reminder endpoints return **403** with guidance to run `/link` in Discord.

#### `GET /api/reminders/{user_id}`

List reminders for a specific user.

**Authentication:** Required (Supabase JWT; `user_id` must match authenticated JWT `sub`)

**Path Parameters:**
- `user_id` (required): Must equal the authenticated JWT `sub` (Innersync UUID). Reminders are loaded using the linked Discord id.

**Response:**
```json
[
  {
    "id": 1,
    "name": "Weekly Meeting",
    "channel_id": 123456789,
    "time": "18:00:00",
    "call_time": "19:00:00",
    "days": ["2"],
    "message": "Weekly team meeting",
    "location": "Conference Room",
    "event_time": null,
    "created_at": "2026-01-21T10:00:00Z"
  }
]
```

#### `POST /api/reminders`

Create a new reminder.

**Authentication:** Required (Supabase JWT; `user_id` in payload must match authenticated JWT `sub`)

Supports optional `Idempotency-Key` header for safe retries (duplicate requests with the same key return the cached success response instead of creating duplicate writes).

**Request Body:**
```json
{
  "name": "Team Standup",
  "channel_id": 123456789,
  "time": "09:00:00",
  "days": ["0", "2", "4"],
  "message": "Daily standup meeting",
  "location": "Main Channel"
}
```

#### `PUT /api/reminders`

Update an existing reminder.

**Authentication:** Required (Supabase JWT; `user_id` in payload must match authenticated JWT `sub`)

Supports optional `Idempotency-Key` header for safe retries.

**Request Body:** Same as POST, include `id` in payload. The `user_id` field must equal the JWT `sub`; it is stored as Discord `created_by` after link resolution.

#### `DELETE /api/reminders/{reminder_id}/{created_by}`

Delete a reminder.

**Authentication:** Required (Supabase JWT; `created_by` must match authenticated JWT `sub`)

Supports optional `Idempotency-Key` header for safe retries.

**Path Parameters:**
- `reminder_id` (required): ID of the reminder to delete
- `created_by` (required): Must equal the authenticated JWT `sub` (Innersync UUID); must match the reminder owner after link resolution.

### Exports

**Note:** Ticket and FAQ exports are available via Discord slash commands (`/export_tickets`, `/export_faq`), not API endpoints. These commands are admin-only and generate CSV files sent via Discord.

## Webhooks

These endpoints receive payloads from Core-API. They do not use API key authentication; use `X-Webhook-Signature` (HMAC) with the configured secret (`DISCORD_LINK_WEBHOOK_SECRET`, `APP_REFLECTIONS_WEBHOOK_SECRET`, or other per-route fallbacks). See [Configuration](../configuration/) for environment variables.

### `POST /webhooks/app-reflections`

Receives plaintext reflection content from the App via Core-API. Payload is stored in `app_reflections` (Railway) and used for Grok context in user-self flows (e.g. `/growthcheckin`, `/agent` + `journal_sync`; not used for ticket "Suggest reply" for privacy).

**Note:** Grok context merges consent-gated sources only: Railway `app_reflections` and Supabase `reflections_shared` require an active row in `reflection_alphapy_consent` (non-revoked). Discord check-ins from Supabase `reflections` are separate. `/agent` uses `load_agent_reflection_context()` — never bulk App vault sync.

**Headers:** `X-Webhook-Signature` (HMAC-SHA256; required in production when `APP_REFLECTIONS_WEBHOOK_SECRET` is set)

**Request body:**
```json
{
  "user_id": 123456789,
  "reflection_id": "uuid-from-app",
  "plaintext_content": { "reflection_text": "...", "mantra": "...", "thoughts": "...", "future_message": "...", "date": "YYYY-MM-DD" }
}
```

**Response:** `200` with `{"status": "acknowledged", "reflection_id": "..."}`.

### `POST /webhooks/revoke-reflection`

Deletes a previously stored reflection when the user revokes consent in the App. Core-API sends this after consent is revoked.

**Headers:** `X-Webhook-Signature` (optional if no secret configured)

**Request body:**
```json
{
  "user_id": 123456789,
  "reflection_id": "uuid-from-app"
}
```

**Response:** `200` with `{"status": "deleted", "count": 1}` (or `count: 0` if no row matched).

> **Note:** Legacy `POST /webhooks/reflections` (`reflection.created` events) was removed. App reflection sync uses `POST /webhooks/app-reflections` via Core-API.

### `POST /webhooks/supabase/auth`

Supabase Auth lifecycle webhook used for profile sync and GDPR-style cleanup workflows.

**Headers:** `X-Webhook-Signature` (optional if no webhook secret configured)

**Response:** `200` with acknowledgment status when processed.

### `POST /webhooks/legal-update`

Triggered by a GitHub Action when `docs/terms-of-service.md` or `docs/privacy-policy.md` changes on main. Posts a formatted embed in the configured channel of the main guild (`MAIN_GUILD_ID`).

**Headers:** `X-Webhook-Signature` (HMAC-SHA256; secret: `LEGAL_UPDATE_WEBHOOK_SECRET`, falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`)

**Request body:**
```json
{
  "documents": ["tos", "pp"],
  "tos_version": "2026-03-31",
  "pp_version": "2026-03-31"
}
```

- `documents` (required): array of keys — `"tos"` (Terms of Service) and/or `"pp"` (Privacy Policy)
- `tos_version` / `pp_version`: effective date string extracted from the document header (format `YYYY-MM-DD`)

**Response:** `200` with `{"status": "acknowledged", "sent": "tos, pp"}`. Returns `{"status": "skipped", "reason": "..."}` if `MAIN_GUILD_ID` is not set or no target channel is configured.

---

### `POST /webhooks/premium-invalidate`

Clears the premium cache for a user so the next check refetches from Core-API/DB. Sent by Core-API on subscription changes (new purchase, cancellation, transfer).

**Headers:** `X-Webhook-Signature` (HMAC-SHA256; secret: `PREMIUM_INVALIDATE_WEBHOOK_SECRET`, falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`)

**Request body:**
```json
{
  "user_id": 123456789,
  "guild_id": 987654321
}
```

- `guild_id` is optional — if omitted, cache is cleared for all guilds for that user.

**Response:** `200` with `{"status": "ok"}`.

---

### `POST /webhooks/discord-link`

Confirms a completed Discord ↔ Innersync link or unlink from Core. Upserts or deletes `alphapy_discord_links` and may send the user a confirmation DM.

**Headers:** `X-Webhook-Signature` (HMAC-SHA256; secret: `DISCORD_LINK_WEBHOOK_SECRET`, falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET` / `SUPABASE_WEBHOOK_SECRET`)

**Link request body:**
```json
{
  "event": "link",
  "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
  "discord_user_id": 123456789012345678,
  "link_source": "app_link"
}
```

**Unlink request body:**
```json
{
  "event": "unlink",
  "innersync_user_id": "550e8400-e29b-41d4-a716-446655440000",
  "discord_user_id": 123456789012345678
}
```

- `event` defaults to link when omitted (legacy payloads).
- `link_source` is optional on link (stored for auditing).

**Responses:**
- `200` with `{"status": "ok"|"noop", "discord_user_id": "<snowflake>"}`
- `409` on link if the Discord user or Innersync user is already linked to a different account

---

### `POST /webhooks/founder`

Sends a founder welcome DM to a Discord user. Triggered by Core-API when a founder purchase is confirmed.

**Headers:** `X-Webhook-Signature` (HMAC-SHA256; secret: `FOUNDER_WEBHOOK_SECRET`, falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`)

**Request body:**
```json
{
  "user_id": 123456789,
  "message": "Optional custom message to include in the DM"
}
```

**Response:** `200` with `{"status": "ok"}` or `{"status": "dm_failed"}` if the user has DMs disabled.

## Error Responses

All endpoints may return standard HTTP error codes:

- `400 Bad Request`: Invalid request parameters
- `401 Unauthorized`: Missing or invalid authentication
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error

Error response format:
```json
{
  "detail": "Error message description"
}
```

## Rate Limiting

In-memory IP-based sliding window limits:

- Health/metrics/status endpoints: **60 req/min**
- Read requests (`GET`): **30 req/min**
- Write requests (`POST`, `PUT`, `DELETE`): **10 req/min**

## Versioning

Current API version: **3.12.1** (Reflection Loop)

Version information is included in health check responses and can be queried via `/api/health`.

## Data Types Reference

### Settings Categories

- **system**: Log channels, log level
- **reminders**: Reminder functionality, default channels
- **embedwatcher**: Embed parsing, reminder offsets
- **gpt**: AI model configuration
- **invites**: Invite tracking settings
- **gdpr**: GDPR compliance settings
- **automod**: Auto-moderation configuration
- **onboarding**: User onboarding flow
- **ticketbot**: Ticket system configuration
- **verification**: Payment verification setup
- **engagement**: Challenges, weekly awards, streaks, badges, OG claims
- **growth**: Growth Check-in channel

### Auto-Moderation Rule Types

- `spam`: Message spam detection
- `content`: Content filtering (bad words, links, etc.)
- `regex`: Custom regex patterns (premium)
- `ai`: AI-powered content analysis (premium)
- `mentions`: Mention spam detection
- `caps`: Excessive capitalization
- `duplicate`: Duplicate message detection

### Auto-Moderation Action Types

- `delete`: Delete message
- `warn`: Send warning message
- `mute`: Mute user (premium)
- `timeout`: Timeout user (premium)
- `ban`: Ban user (premium)

### Onboarding Question Types

- `text`: Free text input
- `email`: Email validation
- `select`: Single choice dropdown
- `multiselect`: Multiple choice checkboxes
