---
title: Database Schema
description: PostgreSQL table reference for Alphapy on Railway.
---

# Database Schema Reference

Complete reference for all database tables used by the Alphapy Discord Bot.

## Overview

The bot uses PostgreSQL for persistent storage. Schema is primarily managed via [Alembic migrations](../migrations/), with some legacy cogs still running idempotent `CREATE TABLE IF NOT EXISTS` safeguards at startup for backward compatibility. All tables support multi-guild architecture via `guild_id` columns where applicable.

## Tables

### `bot_settings`

Guild-specific bot configuration settings.

**Columns:**
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `scope` (TEXT, NOT NULL): Setting scope (e.g., "system", "reminders", "gpt")
- `key` (TEXT, NOT NULL): Setting key
- `value` (JSONB, NOT NULL): Setting value (stored as JSON)
- `value_type` (TEXT): Type of value (e.g., "channel", "bool", "int")
- `updated_by` (BIGINT): User ID who last updated this setting
- `updated_at` (TIMESTAMPTZ): Last update timestamp

**Primary Key:** `(guild_id, scope, key)`

**Indexes:** None (primary key provides efficient lookups)

---

### `settings_history`

Audit trail for settings changes.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `scope` (TEXT, NOT NULL)
- `key` (TEXT, NOT NULL)
- `old_value` (JSONB): Previous value
- `new_value` (JSONB, NOT NULL): New value
- `value_type` (TEXT)
- `changed_by` (BIGINT): User ID who made the change
- `changed_at` (TIMESTAMPTZ): Change timestamp
- `change_type` (TEXT, NOT NULL): Type of change (e.g., "created", "updated", "deleted", "rollback")

**Indexes:**
- `idx_settings_history_guild_scope_key` on `(guild_id, scope, key)`
- `idx_settings_history_changed_at` on `changed_at`

---

### `reminders`

Scheduled reminders (recurring and one-off events).

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `name` (TEXT, NOT NULL): Reminder name
- `channel_id` (BIGINT, NOT NULL): Channel to send reminder to
- `time` (TIME): Reminder trigger time (T−60 for one-off, reminder time for recurring)
- `call_time` (TIME): Event display time (T0, what user sees)
- `days` (TEXT[]): Days of week (0=Mon..6=Sun); empty for one-off events
- `message` (TEXT): Reminder message content
- `created_by` (BIGINT): User ID who created the reminder
- `origin_channel_id` (BIGINT): Original channel where embed was detected
- `origin_message_id` (BIGINT): Original message ID
- `event_time` (TIMESTAMPTZ): One-off event timestamp (NULL for recurring)
- `location` (TEXT): Event location
- `last_sent_at` (TIMESTAMPTZ): Last send timestamp (for idempotency)
- `image_url` (TEXT): Optional image or banner URL (Premium feature)
- `sent_message_id` (BIGINT): Discord message ID of the T-60 offset reminder send; used to delete it when the T0 on-time reminder fires

**Indexes:**
- `idx_reminders_time` on `time`
- `idx_reminders_event_time` on `event_time`
- **Recommendation:** A composite index on `(guild_id, id DESC)` can speed up listing reminders by guild (e.g. reminder_list, add_live_session default channel resolution).

**Notes:**
- One-off events: `event_time` is set, `days` is empty
- Recurring events: `event_time` is NULL, `days` contains weekday numbers
- `time` is the reminder trigger time (T−60), `call_time` is the event time (T0)
- Premium: reminders with `image_url` require an active premium subscription for the creator

---

### `premium_subs`

Premium subscription status (local fallback when Core-API is unavailable). **One active subscription per user**; it applies to a single guild. Users can move it via `/premium_transfer` (or dashboard later).

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `user_id` (BIGINT, NOT NULL): Discord user ID
- `guild_id` (BIGINT, NOT NULL): Guild where Premium is active (can be updated for transfer)
- `tier` (TEXT, NOT NULL): `monthly`, `yearly`, `lifetime`
- `status` (TEXT, NOT NULL): `active`, `cancelled`, `expired`
- `stripe_subscription_id` (TEXT): External subscription ID (for support/cancellation only)
- `expires_at` (TIMESTAMPTZ): NULL for lifetime or N/A
- `created_at` (TIMESTAMPTZ): When the record was created
- `expiry_warning_sent_at` (TIMESTAMPTZ, added migration 015): Timestamp of last 7-day expiry warning DM. NULL = no warning sent yet. Used by the `check_expiry_warnings` background task to avoid duplicate DMs.

**Indexes:**
- `idx_premium_subs_user_guild` on `(user_id, guild_id)`
- `idx_premium_subs_guild_status` on `(guild_id, status)`
- `idx_premium_subs_one_active_per_user`: unique on `(user_id)` WHERE `status = 'active'` (enforces one active per user)

**RLS (Supabase):** Row Level Security is enabled (migration 007). No policies are defined for `anon`/`authenticated`, so only the backend role (table owner / service role / superuser used by `DATABASE_URL`) can read or write. Direct Supabase client access with anon or authenticated keys sees no rows.

**GDPR:** This table stores only access-control data (user_id, guild_id, tier, status, optional external ID, expiry). No payment details, email, or other PII are stored here.

---

### `gpt_usage`

Per-user, per-guild daily GPT call tracking for quota enforcement. Added in migration 014.

**Columns:**
- `user_id` (BIGINT, NOT NULL): Discord user ID
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `usage_date` (DATE, NOT NULL, DEFAULT CURRENT_DATE): The UTC date of usage
- `call_count` (INTEGER, NOT NULL, DEFAULT 0): Number of GPT calls made on this date

**Primary key:** `(user_id, guild_id, usage_date)`

**Indexes:**
- `idx_gpt_usage_date` on `usage_date` (for efficient cleanup of old rows)

**Notes:**
- `check_and_increment_gpt_quota()` in `utils/premium_guard.py` uses an atomic `INSERT … ON CONFLICT DO UPDATE` so no race conditions
- Limits per tier: free = 5/day, monthly = 25/day, yearly/lifetime = unlimited (NULL)
- Only user-initiated GPT calls are counted; ticket summaries and embed watcher calls pass `user_id=None` and are excluded

**GDPR:** Stores only Discord user ID, guild ID, date, and a counter. No message content or PII beyond the snowflake ID.

---

### `agent_session_usage`

Per-user daily `/agent start` tracking for tier-based session caps. Added in migration 024.

**Columns:**
- `user_id` (BIGINT, NOT NULL): Discord user ID
- `usage_date` (DATE, NOT NULL, DEFAULT CURRENT_DATE): The UTC date of usage
- `session_count` (INTEGER, NOT NULL, DEFAULT 0): Number of agent sessions started on this date

**Primary key:** `(user_id, usage_date)`

**Indexes:**
- `idx_agent_session_usage_date` on `usage_date`

**Notes:**
- `check_and_increment_agent_session_quota()` in `utils/premium_guard.py` — atomic upsert, same pattern as `gpt_usage`
- Limits per tier: free = 10/day, monthly = 25/day, yearly/lifetime = unlimited
- Counts `/agent start` only; `/agent continue` and `/agent end` do not consume quota
- Deleted on GDPR erasure (`webhooks/supabase.py`, `/delete_my_data`)

---

### `terms_acceptance`

Tracks user acceptance of the Terms of Service and Privacy Policy for GDPR compliance.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `user_id` (BIGINT, NOT NULL, UNIQUE): Discord user ID
- `accepted_at` (TIMESTAMPTZ, NOT NULL, DEFAULT NOW()): When the user accepted the terms
- `version` (TEXT, NOT NULL, DEFAULT '2026-03-02'): Legal terms version the user accepted

> **Note:** The `ip_address` column was dropped in migration 016. Discord gateway interactions carry no client IP, so the column was always NULL. Re-add it via a new migration only if a web-based consent flow is implemented.

**Indexes:**
- `idx_terms_acceptance_user` on `user_id`
- `idx_terms_acceptance_accepted_at` on `accepted_at`

**Notes:**
- Used by the `/premium` flow: users must accept terms before accessing premium checkout.
- Stores only minimal consent metadata for legal compliance; no content of the terms is stored here.

---

### `gdpr_acceptance`

Guild GDPR agreement acceptance (the "I Agree" button in the GDPR channel).

**Columns:**
- `user_id` (BIGINT, NOT NULL): Discord user ID
- `guild_id` (BIGINT, NOT NULL): Discord guild ID — scopes acceptances per server (added migration 018)
- `accepted` (INTEGER, NOT NULL, DEFAULT 0): 1 = accepted, 0 = not accepted
- `timestamp` (TIMESTAMP, NOT NULL, DEFAULT CURRENT_TIMESTAMP): When the user clicked "I Agree"

**Primary Key:** `(user_id, guild_id)`

**Notes:**
- Written by `cogs/gdpr.py` / `utils/gdpr_helpers.py` when a user clicks the "I Agree" button on the GDPR embed
- Formalised in Alembic migration 016; `guild_id` column added in migration 018
- Deleted as part of GDPR erasure when a user's Supabase account is deleted (`webhooks/supabase.py:_purge_railway_data`) or when the user runs `/delete_my_data`

---

### `app_reflections`

Plaintext reflections received from the App via Core-API webhook. Used for Grok context in user-self flows (e.g. `/growthcheckin` only; not used for ticket "Suggest reply" for privacy). Consent is validated by Core before the webhook is sent; revoke is handled via `POST /webhooks/revoke-reflection`.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `user_id` (BIGINT, NOT NULL): Discord user ID
- `reflection_id` (TEXT, NOT NULL): Unique reflection identifier from App/Core
- `plaintext_content` (JSONB, NOT NULL): Full reflection payload (e.g. reflection_text, mantra, thoughts, future_message, date)
- `created_at` (TIMESTAMPTZ, NOT NULL, DEFAULT NOW())

**Unique constraint:** `(user_id, reflection_id)` to prevent duplicates (upsert on conflict).

**Indexes:**
- `idx_app_reflections_user_created` on `(user_id, created_at DESC)` for chronological queries (e.g. last 30 days).

**Notes:**
- Populated by `POST /webhooks/app-reflections`; deleted by `POST /webhooks/revoke-reflection`.
- Context loader (`gpt/context_loader.py`) reads from this table for user-self flows (e.g. `/growthcheckin`). Ticket "Suggest reply" does not use reflection context.

**Grok context sources for `/growthcheckin` (all three are merged, max 5 total):**
1. Supabase `reflections_shared` — App reflections the user opted to share (requires `bot_sharing_enabled = true`)
2. Supabase `reflections` — Discord check-ins written by `/growthcheckin` itself (no opt-in required)
3. Railway `app_reflections` — plaintext from Core-API webhook (last 30 days, no opt-in required)

**Also used by Alphapy Agents** (`journal_sync` skill): opt-in shared reflections via `load_user_reflections` — never encrypted App ciphertext. See [Agent safety guidelines](../agents-safety-guidelines/).

---

### `alphapy_discord_links`

Maps Supabase Auth user id (Innersync UUID) to a Discord snowflake for Alphapy and the HTTP API. There is no database-level foreign key to Supabase `users` (different host); integrity is enforced when Core confirms a link via webhook.

**Columns:**
- `innersync_user_id` (UUID, NOT NULL, PRIMARY KEY): Supabase Auth `sub`
- `discord_user_id` (BIGINT, NOT NULL, UNIQUE): Discord user snowflake
- `linked_at` (TIMESTAMPTZ, NOT NULL, DEFAULT NOW())
- `link_source` (TEXT, nullable): e.g. `magic_link`, `otp`, `webhook`

**Indexes:**
- `idx_alphapy_discord_links_discord` on `(discord_user_id)`

**Notes:**
- Written by `POST /webhooks/discord-link` after the user completes linking in Core/App.
- Removed by `/unlink` or GDPR purge when the Supabase user is deleted (`webhooks/supabase.py`).
- Resolution helpers live in `utils/innersync_identity.py` (with optional fallback to Supabase `profiles` for migration).

---

### `onboarding`

User onboarding responses per guild.

**Columns:**
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `responses` (JSONB): User responses (answers by question index, plus personalization keys)
- `timestamp` (TIMESTAMP): Response timestamp

**Primary Key:** `(guild_id, user_id)`

**Notes:**
- Responses stored as JSONB with flexible structure
- Supports multi-select, text input, email validation, and follow-up questions
- Fixed personalization keys (synthetic steps after guild questions): `personalized_opt_in` (`"full"` | `"events_only"` | `"no"`), `preferred_language` (e.g. `"en"`, `"nl"`, or `"other: <text>"`)

---

### `guild_onboarding_questions`

Onboarding question definitions per guild.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `step_order` (INTEGER, NOT NULL): Question order
- `question` (TEXT, NOT NULL): Question text
- `question_type` (TEXT, NOT NULL): Type (select, multiselect, text, email)
- `options` (JSONB): Options for select/multiselect questions
- `followup` (JSONB): Conditional follow-up questions
- `required` (BOOLEAN): Whether question is required
- `enabled` (BOOLEAN): Whether question is enabled
- `created_at` (TIMESTAMP): Creation timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Unique Constraint:** `(guild_id, step_order)`

---

### `guild_rules`

Guild rules for onboarding display.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `rule_order` (INTEGER, NOT NULL): Display order
- `title` (TEXT, NOT NULL): Rule title
- `description` (TEXT, NOT NULL): Rule description
- `thumbnail_url` (TEXT, nullable): Image shown right/top in embed (rechts)
- `image_url` (TEXT, nullable): Image shown at bottom in embed (onderaan)
- `enabled` (BOOLEAN): Whether rule is enabled
- `created_at` (TIMESTAMP): Creation timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Unique Constraint:** `(guild_id, rule_order)`

---

### `support_tickets`

Support ticket system data.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL): Ticket creator
- `username` (TEXT): Username at creation time
- `description` (TEXT, NOT NULL): Ticket description
- `status` (TEXT): Ticket status (open, claimed, waiting_for_user, escalated, closed, archived)
- `created_at` (TIMESTAMPTZ): Creation timestamp
- `channel_id` (BIGINT): Private channel ID for this ticket
- `claimed_by` (BIGINT): User ID who claimed the ticket
- `claimed_at` (TIMESTAMPTZ): Claim timestamp
- `updated_at` (TIMESTAMPTZ): Last update timestamp
- `escalated_to` (BIGINT): Role ID for escalation
- `archived_at` (TIMESTAMPTZ): When the ticket was archived (NULL if not archived)
- `archived_by` (BIGINT): User ID who archived the ticket

**Indexes:**
- `idx_support_tickets_user_id` on `user_id`
- `idx_support_tickets_status` on `status`
- `idx_support_tickets_channel_id` on `channel_id`

---

### `verification_tickets`

AI-assisted verification ticket metadata for payment/checkout verification.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `user_id` (BIGINT, NOT NULL): User who started the verification
- `channel_id` (BIGINT, NOT NULL): Private verification channel ID
- `status` (TEXT, NOT NULL, DEFAULT `'pending'`): Verification status (`pending`, `verified`, `manual_review`, `error`, `closed_manual`)
- `ai_can_verify` (BOOLEAN): Whether the AI considered the screenshot sufficient for auto-verification
- `ai_needs_manual_review` (BOOLEAN): Whether the AI requested human review
- `ai_reason` (TEXT): Short, sanitized explanation of the AI decision (no raw payment details)
- `created_at` (TIMESTAMPTZ, DEFAULT NOW()): When the verification was created
- `resolved_at` (TIMESTAMPTZ): When the verification was completed (NULL while pending)
- `resolved_by_user_id` (BIGINT): Discord ID of the admin who resolved a manual review (NULL if auto-resolved)
- `rejection_reason` (TEXT): Reason shown to the user on rejection
- `payment_date` (DATE): Date extracted from the payment screenshot by the AI (NULL if unreadable or not yet populated)

**Indexes:**
- `idx_verification_tickets_guild_status` on `(guild_id, status)`
- `idx_verification_tickets_channel_id` on `channel_id`

**Runtime note:** `cogs/verification.py` still includes idempotent startup DDL guards (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... IF NOT EXISTS`) to keep older deployments resilient.

### `ticket_summaries`

AI-generated summaries of closed tickets (Grok).

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `ticket_id` (INTEGER, NOT NULL): Reference to support_tickets.id
- `summary` (TEXT, NOT NULL): AI-generated summary (Grok)
- `similarity_key` (TEXT): Key for detecting similar tickets
- `created_at` (TIMESTAMPTZ): Creation timestamp

**Notes:**
- Used for FAQ proposal generation when 3+ similar summaries appear
- Legacy startup guards in `cogs/ticketbot.py` still ensure table/index presence on boot.

---

### `ticket_metrics`

Ticket statistics snapshots.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `snapshot` (JSONB): Full snapshot data
- `scope` (TEXT): Time scope (7d, 30d, all)
- `counts` (JSONB): Status → count mapping
- `average_cycle_time` (BIGINT): Average ticket lifecycle in seconds
- `triggered_by` (BIGINT): User ID who triggered the snapshot
- `created_at` (TIMESTAMPTZ): Snapshot timestamp

**Runtime note:** Historically created/extended by idempotent startup DDL in `cogs/ticketbot.py`; Alembic migration coverage should remain the source of truth for new changes.

---

### `faq_entries`

FAQ entries for knowledge base.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `title` (TEXT): Entry title
- `summary` (TEXT): Entry summary/content
- `keywords` (TEXT[]): Search keywords
- `created_at` (TIMESTAMPTZ): Creation timestamp

**Notes:**
- Can be created manually or from ticket summaries via "Add to FAQ" button

---

### `faq_search_logs`

FAQ search analytics.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `user_id` (BIGINT): User who searched
- `query` (TEXT): Search query
- `results_count` (INTEGER): Number of results returned
- `searched_at` (TIMESTAMPTZ): Search timestamp

---

### `audit_logs`

Command usage analytics (automatically tracked).

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL): Guild where command was executed (0 for DMs)
- `user_id` (BIGINT, NOT NULL): User who executed the command
- `command_name` (TEXT, NOT NULL): Command name
- `command_type` (TEXT, NOT NULL): Type ('slash' or 'text')
- `success` (BOOLEAN): Whether command executed successfully
- `error_message` (TEXT): Error message if command failed
- `created_at` (TIMESTAMPTZ): Execution timestamp

**Indexes:**
- `idx_audit_logs_guild_created` on `(guild_id, created_at)`
- `idx_audit_logs_command` on `(command_name, created_at)`

**Notes:**
- Automatically populated by event handlers in `bot.py` (`on_app_command_completion`, `on_command_completion`, etc.)
- Uses the shared bot database pool via `DatabaseManager` / `get_bot_db_pool()` (bot event loop)
- Initialized in `on_ready()` event handler, persists across bot restarts
- Used for command usage analytics, dashboard metrics, and `/command_stats` Discord command
- Tracking is non-blocking and failures don't affect command execution

---

### `health_check_history`

Historical health check data for trend analysis.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `service` (TEXT, NOT NULL): Service name
- `version` (TEXT, NOT NULL): Bot version
- `uptime_seconds` (INTEGER, NOT NULL): Uptime at check time
- `db_status` (TEXT, NOT NULL): Database status
- `guild_count` (INTEGER): Number of guilds
- `active_commands_24h` (INTEGER): Commands executed in last 24h
- `gpt_status` (TEXT): Grok/LLM service status
- `database_pool_size` (INTEGER): Database pool size
- `checked_at` (TIMESTAMPTZ): Health check timestamp

**Indexes:**
- `idx_health_check_history_checked_at` on `checked_at DESC`
- `idx_health_check_history_service` on `(service, checked_at DESC)`

**Notes:**
- Automatically populated on each `/api/health` call
- Startup no longer mutates schema; table is managed via Alembic migration `022_api_observability_tables`
- Auto-cleanup: Records older than 30 days are automatically deleted

---

### `custom_commands`

Guild-defined automated message responses with configurable trigger patterns.

**Columns:**
- `id` (SERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `name` (TEXT, NOT NULL): Unique slug per guild (e.g. `hello`)
- `trigger_type` (TEXT, NOT NULL): `exact`, `starts_with`, `contains`, or `regex`
- `trigger_value` (TEXT, NOT NULL): The pattern to match (max 200 chars)
- `response` (TEXT, NOT NULL): Response template (max 1900 chars, supports dynamic variables)
- `enabled` (BOOLEAN, DEFAULT true)
- `case_sensitive` (BOOLEAN, DEFAULT false)
- `delete_trigger` (BOOLEAN, DEFAULT false): Delete triggering message on match
- `reply_to_user` (BOOLEAN, DEFAULT true): Reply vs plain send
- `uses` (INTEGER, DEFAULT 0): Total number of times this command has fired
- `created_by` (BIGINT): User ID who created the command
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)

**Primary Key:** `id`

**Unique constraint:** `(guild_id, name)`

**Indexes:**
- `idx_custom_commands_guild_enabled` on `(guild_id, enabled)`

**Migration:** `010_add_custom_commands`

---

## Database Connection Architecture

The bot uses a **shared** `asyncpg` pool for Railway bot data (`utils/db_helpers.get_bot_db_pool()`). Cogs acquire connections through `DatabaseManager` in `utils/database_helpers.py` — it no longer creates per-cog pools.

Benefits:
- **Better concurrency**: Multiple operations can run simultaneously
- **Connection reuse**: Efficient resource management
- **Graceful error handling**: Automatic retry and recovery from connection errors

### Connection pool configuration

- **Bot + cogs**: Shared pool from `get_bot_db_pool()` (created during startup Phase 1)
- **FastAPI (`api.py`)**: Uses the same shared pool when the bot instance is available
- **Command tracker** (optional): Small dedicated pool in the bot event loop for batch flush (`utils/command_tracker.py`, min_size=1, max_size=5)

All pool usage includes connection timeout handling, graceful shutdown, and health checks via `is_pool_healthy()`.

## Schema Management

All schema changes are managed via Alembic migrations. See [Migrations guide](../migrations/) for migration workflow.

**Current Migration Head:** `024_agent_session_usage`

To view current schema state:
```bash
alembic current
```

To apply migrations:
```bash
alembic upgrade head
```

---

## Multi-Guild Architecture

All tables that store guild-specific data include a `guild_id` column:
- `bot_settings`
- `settings_history`
- `reminders`
- `onboarding`
- `guild_onboarding_questions`
- `guild_rules`
- `support_tickets`
- `audit_logs`

Tables without `guild_id` are global:
- `ticket_summaries` (linked via `ticket_id` → `support_tickets` → `guild_id`)
- `ticket_metrics` (scope-based, not guild-specific)
- `faq_entries` (global knowledge base)
- `faq_search_logs` (analytics)
- `health_check_history` (system-wide)

---

## Indexes

All tables have appropriate indexes for common query patterns:
- Primary keys provide efficient lookups
- Foreign key columns are indexed where applicable
- Time-based columns have indexes for range queries
- Composite indexes support multi-column filters

---

## Data Retention

- **Health check history**: Auto-deletes records older than 30 days
- **Audit logs**: No automatic cleanup (can be configured per guild)
- **Ticket summaries**: No automatic cleanup
- **Other tables**: No automatic cleanup (manual maintenance recommended)

---

### `automod_actions`

Reusable action definitions for the auto-moderation system. Referenced by rules via FK.

**Columns:**
- `id` (SERIAL, PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `action_type` (TEXT, NOT NULL): `delete`, `warn`, `mute`, `timeout`, `ban`
- `severity` (INTEGER, DEFAULT 1): Severity level (1–5)
- `config` (JSONB, NOT NULL): Action-specific config (e.g. timeout duration)
- `is_premium` (BOOLEAN, DEFAULT false): Whether this action requires premium
- `created_by` (BIGINT): User ID who created the action
- `created_at` (TIMESTAMPTZ, DEFAULT NOW())

**Indexes:** `idx_automod_actions_guild` on `(guild_id)`

**Notes:** Added in migration 009. Created before `automod_rules` to satisfy the FK constraint.

---

### `automod_rules`

Per-guild content moderation rules. Each rule references one action via FK.

**Columns:**
- `id` (SERIAL, PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL): Discord guild ID
- `rule_type` (TEXT, NOT NULL): `spam`, `content`, `ai`, `regex`
- `name` (TEXT, NOT NULL): Human-readable rule name
- `enabled` (BOOLEAN, DEFAULT true)
- `config` (JSONB, NOT NULL): Rule-specific config (patterns, thresholds, etc.)
- `action_id` (INTEGER, FK → `automod_actions.id`): Action to take on trigger
- `created_by` (BIGINT)
- `created_at` (TIMESTAMPTZ, DEFAULT NOW())
- `updated_at` (TIMESTAMPTZ, DEFAULT NOW())
- `is_premium` (BOOLEAN, DEFAULT false)

**Indexes:**
- `idx_automod_rules_guild_enabled` on `(guild_id, enabled)`
- `idx_automod_rules_type` on `(rule_type)`

**Notes:** Managed via `/automod` slash commands (top-level group, not nested under `/config`). Toggle individual rules with `/automod set_rule_enabled`. Rules can also be managed via the Alphapy Dashboard (`/api/dashboard/{guild_id}/automod/*`).

---

### `automod_logs`

Audit log of every auto-moderation trigger.

**Columns:**
- `id` (SERIAL, PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL): User who triggered the rule
- `message_id` (BIGINT): Offending message ID (nullable)
- `channel_id` (BIGINT): Channel where the trigger occurred (nullable)
- `rule_id` (INTEGER, FK → `automod_rules.id`)
- `action_taken` (TEXT, NOT NULL): Action that was executed
- `message_content` (TEXT): Captured message text (nullable)
- `ai_analysis` (JSONB): AI analysis result if `rule_type = 'ai'` (nullable)
- `context` (JSONB): Additional context payload (nullable)
- `moderator_id` (BIGINT): Set when action was a manual override (nullable)
- `timestamp` (TIMESTAMPTZ, DEFAULT NOW())
- `appeal_status` (TEXT, DEFAULT `'none'`): `none`, `pending`, `approved`, `denied`

**Indexes:**
- `idx_automod_logs_guild_user` on `(guild_id, user_id)`
- `idx_automod_logs_timestamp` on `(timestamp)`
- `idx_automod_logs_rule` on `(rule_id)`

---

### `automod_stats`

Daily aggregated statistics per guild per rule, populated by internal analytics scaffolding and exposed via `GET /api/dashboard/{guild_id}/automod/stats` (Alphapy Dashboard).

**Columns:**
- `id` (SERIAL, PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `rule_id` (INTEGER, FK → `automod_rules.id`)
- `date` (DATE, NOT NULL)
- `triggers_count` (INTEGER, DEFAULT 0)
- `false_positives` (INTEGER, DEFAULT 0)
- `avg_response_time` (FLOAT)
- `created_at` (TIMESTAMPTZ, DEFAULT NOW())

**Unique constraint:** `(guild_id, rule_id, date)`

**Indexes:** `idx_automod_stats_date` on `(date)`

---

### `automod_user_history`

Running violation totals per user per rule type. Used for escalating action thresholds.

**Columns:**
- `id` (SERIAL, PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `rule_type` (TEXT, NOT NULL)
- `violation_count` (INTEGER, DEFAULT 1)
- `last_violation` (TIMESTAMPTZ, DEFAULT NOW())
- `total_points` (INTEGER, DEFAULT 0)
- `context` (JSONB)
- `updated_at` (TIMESTAMPTZ, DEFAULT NOW())

**Unique constraint:** `(guild_id, user_id, rule_type)`

**Indexes:** `idx_automod_user_history_guild_user` on `(guild_id, user_id)`

---

---

## Engagement Tables

Added in migration `020_engagement_system`. All tables are multi-guild scoped via `guild_id`.

### `engagement_badges`

Per-user, per-guild badge history.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `badge_key` (TEXT, NOT NULL): e.g. `winner`, `og`, `motivator`
- `assigned_at` (TIMESTAMPTZ, DEFAULT NOW())

**Indexes:** `idx_eng_badges_guild_user` on `(guild_id, user_id)`

---

### `engagement_og_claims`

Tracks which users have claimed an OG spot per guild.

**Columns:**
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `claimed_at` (TIMESTAMPTZ, DEFAULT NOW())

**Primary Key:** `(guild_id, user_id)`

**Indexes:** `idx_eng_og_claims_guild` on `(guild_id)`

---

### `engagement_og_setup`

One row per guild storing the OG claim message location.

**Columns:**
- `guild_id` (BIGINT PRIMARY KEY)
- `message_id` (BIGINT)
- `channel_id` (BIGINT)
- `updated_at` (TIMESTAMPTZ, DEFAULT NOW())

---

### `engagement_challenges`

Challenge sessions per guild.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `channel_id` (BIGINT): Channel where messages are counted
- `mode` (TEXT, DEFAULT `'leaderboard'`): `leaderboard` or `random`
- `title` (TEXT)
- `active` (BOOLEAN, DEFAULT TRUE)
- `started_at` (TIMESTAMPTZ, DEFAULT NOW())
- `ends_at` (TIMESTAMPTZ)
- `ended_at` (TIMESTAMPTZ)
- `winner_id` (BIGINT)
- `messages_count` (INT)

**Indexes:** `idx_eng_challenges_guild_active` on `(guild_id, active)`

---

### `engagement_participants`

Per-challenge participant message counts.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `challenge_id` (BIGINT, NOT NULL, FK → `engagement_challenges.id` ON DELETE CASCADE)
- `user_id` (BIGINT, NOT NULL)
- `message_count` (INT, DEFAULT 0)

**Unique:** `(challenge_id, user_id)`

**Indexes:** `idx_eng_participants_challenge` on `(challenge_id)`

---

### `engagement_weekly_messages`

Indexed messages for weekly award computation.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `message_id` (BIGINT, NOT NULL)
- `channel_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `created_at` (TIMESTAMPTZ, NOT NULL)
- `has_image` (BOOLEAN, DEFAULT FALSE)
- `is_food` (BOOLEAN, DEFAULT FALSE)
- `reactions_count` (INT, DEFAULT 0)

**Unique:** `(guild_id, message_id)`

**Indexes:** `idx_eng_weekly_msgs_guild_created` on `(guild_id, created_at)`

---

### `engagement_weekly_awards`

Weekly award period bookkeeping per guild.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `guild_id` (BIGINT, NOT NULL)
- `week_start` (DATE, NOT NULL)
- `week_end` (DATE, NOT NULL)

**Unique:** `(guild_id, week_start, week_end)`

**Indexes:** `idx_eng_weekly_awards_guild` on `(guild_id)`

---

### `engagement_weekly_results`

Per-period winner records.

**Columns:**
- `id` (BIGSERIAL PRIMARY KEY)
- `week_id` (BIGINT, NOT NULL, FK → `engagement_weekly_awards.id` ON DELETE CASCADE)
- `award_key` (TEXT, NOT NULL): e.g. `motivator`, `star`
- `user_id` (BIGINT, NOT NULL)
- `metric` (INT): Score used to determine winner
- `message_id` (BIGINT): Winning message ID (reactions filter only)

**Unique:** `(week_id, award_key)`

---

### `engagement_streaks`

Daily activity streak per user per guild.

**Columns:**
- `guild_id` (BIGINT, NOT NULL)
- `user_id` (BIGINT, NOT NULL)
- `last_day` (DATE): Last date a message was sent
- `current_days` (INT, DEFAULT 0): Current streak length
- `base_nickname` (TEXT): Stored base nickname (without suffix)

**Primary Key:** `(guild_id, user_id)`

**Indexes:** `idx_eng_streaks_guild` on `(guild_id)`

---

## Supabase tables (platform — not Railway)

These tables live in **Supabase Postgres** (Core migrations). Alphapy accesses them via service role only.

### `agent_sessions`

One row per `/agent` invocation. Migration: `Innersync_Core/supabase/0020_agent_sessions_memory.sql`.

**Columns:**
- `id` (UUID, PRIMARY KEY)
- `innersync_user_id` (UUID, NOT NULL): Supabase Auth user id
- `discord_user_id` (TEXT, NOT NULL)
- `guild_id` (TEXT, nullable)
- `agent_name` (TEXT, NOT NULL): e.g. `reflection`
- `status` (TEXT): `active`, `completed`, `failed`, `cancelled`
- `summary` (TEXT): Tier-2-conform distilled labels (not raw LLM output)
- `memory_patch` (JSONB): Patches applied to `agent_memory` after run
- `metadata` (JSONB): e.g. `source: discord_slash`
- `started_at`, `completed_at`, `updated_at` (TIMESTAMPTZ)

**Indexes:** `(innersync_user_id, agent_name, started_at DESC)`; partial index on `status = 'active'`.

**Notes:**
- Written by `agents/memory.py` via Supabase PostgREST.
- Multi-turn turns stored in `agent_session_messages` (Core `0023`) while `status = active`; deleted on session end.
- RLS enabled with zero policies; `anon`/`authenticated` revoked (service role only).
- App reads completed session rows via service-role API routes; users can delete individual history rows.
- GDPR purge on user delete: `purge_agent_user_data()` via `webhooks/supabase.py` and `/delete_my_data`.

---

### `agent_session_messages`

Ephemeral multi-turn message history. Migration: `Innersync_Core/supabase/0023_agent_session_messages.sql`.

**Columns:**
- `id` (UUID, PRIMARY KEY)
- `session_id` (UUID, FK → `agent_sessions.id` ON DELETE CASCADE)
- `turn_index` (INT, NOT NULL): 0-based turn
- `role` (TEXT): `user` or `assistant`
- `content` (TEXT, NOT NULL)
- `created_at` (TIMESTAMPTZ)

**Notes:**
- Alphapy service role only; deleted when session completes or is removed from App history.

---

### `agent_memory`

Durable per-user JSON blob per agent type.

**Columns:**
- `innersync_user_id` (UUID, NOT NULL)
- `agent_name` (TEXT, NOT NULL)
- `memory` (JSONB): Tier 1 prefs (App), Tier 2 `derived_profile`, Tier 3 operational metadata — never journal plaintext
- `updated_at` (TIMESTAMPTZ)

**Primary Key:** `(innersync_user_id, agent_name)`

**Notes:**
- Patched on `/agent end` (Tier 2 distill + Tier 3 metadata). Not on every turn.
- Same RLS / access pattern as `agent_sessions`.
- GDPR purge on user delete: `purge_agent_user_data()` via `webhooks/supabase.py` and `/delete_my_data`.

---

## Backup Recommendations

Regular backups should include:
- All tables (full database backup recommended)
- Focus on `bot_settings`, `reminders`, `support_tickets`, `onboarding` for critical data
- `audit_logs` and `health_check_history` can be excluded from frequent backups (analytics only)
