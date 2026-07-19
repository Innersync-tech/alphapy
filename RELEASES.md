# Release Notes

All notable releases of Alphapy will be documented in this file.

---

## [3.11.0] - 2026-07-04 - "Phase 4 Observability"

### Minor Release: Agent session limits, skills, GDPR, Mind telemetry

Phase 4 closes the loop on agent privacy, quotas, App-sourced prefs, and platform observability.

#### What's New
- **Session quotas** — Tier caps on `/agent start` via `agent_session_usage` (migration 024)
- **`inner_voice` + `fatigue_check` skills** — Tier 1 prefs from App Settings injected on reflection sessions
- **Mind observability** — Optional `agent_sessions` on dashboard metrics + `agents:` counts in telemetry ingest notes
- **GDPR** — Full agent memory purge on user delete and `/delete_my_data`

#### PRs
- [#275](https://github.com/Innersync-tech/alphapy/pull/275) telemetry ingest counts
- [#276](https://github.com/Innersync-tech/alphapy/pull/276) structured dashboard metrics
- [#277](https://github.com/Innersync-tech/alphapy/pull/277) docs

---

## [3.10.0] - 2026-06-30 - "Agent Memory & Conversation"

### Minor Release: Privacy-safe agent memory (Phases 2.1–2.3)

Tiered memory model, derived insights, and multi-turn Discord sessions — without storing journal plaintext in durable memory.

#### What's New
- **Phase 2.1** — Tier 1 prefs (`agent_prefs` via App); Tier 3 operational metadata only in `agent_memory`; `[agent_profile]` prompt block
- **Phase 2.2** — Tier 2 distill pipeline (`agents/tier2.py`); consent-linked purge on revoke-share; distilled session summaries
- **Phase 2.3** — `/agent continue`, `/agent end`; ephemeral `agent_session_messages` (Core `0023`); Tier 2/3 updates on session end
- **Discord UX** — Display name in embed title; turn count in footer; `/agent status` shows active turn count

#### Migrations & PRs
- Core `0022` (`agent_prefs`), `0023` (`agent_session_messages`)
- Alphapy [#255](https://github.com/Innersync-tech/alphapy/pull/255) (2.1), [#256](https://github.com/Innersync-tech/alphapy/pull/256) (2.2), [#257](https://github.com/Innersync-tech/alphapy/pull/257) (2.3)

#### Deferred (Phase 4.0)
- App-triggered agent runs via REST — shipped as `/api/agents/sessions*` (not a separate `POST /api/agents/run`)

---

## [3.9.0] - 2026-06-30 - "Reflection Loop"

### Minor Release: Alphapy Agents MVP + Safety Policy

Multi-user personal agents for linked Innersync users, with explicit privacy boundaries and jailbreak-tested safety policy.

#### What's New
- **Alphapy Agents**: `/agent list`, `/agent start`, `/agent status` for the `reflection` agent (journal sync + streaks)
- **Guild admin**: `/config agents show`, `/config agents toggle` (requires `ALPHAPY_AGENTS_ENABLED=true` on deployment)
- **Supabase memory**: `agent_sessions` + `agent_memory` (Core `0020` migration)
- **Safety policy**: `agents/policy.py` — no decryption of App journals; UNTRUSTED skill context; Matrix A jailbreak probes passed on test bot
- **Docs**: `docs/alphapy-agents-architecture.md`, `docs/agents-safety-guidelines.md`, `/agent` in `docs/commands.md`

#### Security & Reliability
- Webhook HMAC fail-closed in production / strict mode
- Embed injection hardening (`safe_embed_text` on additional user-controlled fields)
- `pip-audit` in CI
- Premium verify path fix (`/api/premium/verify`)

#### Deferred
- `trade` agent and `trade_insight` skill (product decision pending)

---

## [3.8.0] - 2026-05-25 - "Enterprise Ready"

### Minor Release: Innersync Identity, Hermit Context, and Hot-Path Caching

This release ships Discord ↔ Innersync account linking (`/link`), Core webhook completion, Hermit strategic context in Grok flows, and caching/observability improvements across engagement, auto-mod, and premium guards.

#### What's New
- **Innersync Identity**: `/link`, `/unlink`, `/profile`; Railway `alphapy_discord_links`; `POST /webhooks/discord-link`; API/Mind JWT → Discord resolution with profile fallback
- **Hermit context**: Core-mediated strategic context consumed in Alphapy GPT flows where configured
- **Auto-mod UX**: Action choices on slash commands; `rule_id` autocomplete on edit/delete/logs commands
- **Cache metrics**: AutoMod, Engagement, and guild premium cache counters on internal observability paths

#### Reliability & Fixes
- **Link URL normalization**: Malformed Core `INNERSYNC_APP_URL` values no longer break Discord link buttons
- **Engagement / auto-mod / premium**: TTL caches, correct settings reads, and invalidation on configuration changes
- **Docs**: Commands, API, and database schema docs synced; Starlight frontmatter for docs.innersync.tech

---

## [3.7.0] - 2026-04-25 - "Enterprise Ready"

### Minor Release: API Observability, CI Quality Gates, and Engagement Stability

This release adds API request observability and idempotent reminder API writes, formalizes migration-driven startup, and improves challenge reliability in the engagement module.

#### What's New
- **API observability endpoint**: New internal `GET /api/observability` with rolling success rate, request volume, and latency percentiles (`p50`, `p95`, `p99`) for API and webhook traffic
  - Audience/scope: Mind/internal observability and ops monitoring (outside Alphapy dashboard configuration endpoints)
- **Request tracing**: API middleware now propagates `X-Request-ID` and tracks per-request latency/outcome
- **Reminder API idempotency**: `POST /api/reminders`, `PUT /api/reminders`, and `DELETE /api/reminders/{reminder_id}/{created_by}` now support `Idempotency-Key` headers
- **Migration 022**: Creates/ensures `audit_logs` and `health_check_history`, ensures analytics indexes, and adds `idx_reminders_event_time`
- **CI quality gates**: Added Ruff linting, Pyright type checking, and pytest coverage in `.github/workflows/bot.yml`
- **Tooling baseline**: Added `pyproject.toml` (Ruff/Pyright config), updated `pytest.ini` coverage defaults, and added `pytest-cov`, `ruff`, and `pyright` to `requirements.txt`

#### Reliability & Fixes
- **API startup schema behavior**: Removed runtime `CREATE TABLE IF NOT EXISTS` from FastAPI lifespan; schema changes are now Alembic-driven
- **Security hardening mode**: Added strict startup checks in production when `STRICT_SECURITY_MODE=1` and required auth/webhook secrets are missing
- **CORS fallback**: Removed wildcard fallback and tightened default fallback to localhost-only development origins
- **Engagement challenge stability**: Fixed `NameError` crash in challenge finalization and corrected challenge progress after restart by rehydrating timestamps from DB

---

## [3.3.0] - 2026-04-12 - "Enterprise Ready"

### Minor Release: Verification Overhaul & Growth Check-in Improvements

This release completes the verification workflow with full admin controls and AI configurability, and ships growth check-in community sharing and history.

#### What's New
- **Verification — manual approve/reject**: `ManualReviewView` with admin Approve/Reject buttons posted when AI flags a screenshot for manual review; reject opens a modal for optional reason
- **Verification — reference image**: Admins can set a reference payment screenshot via `/verification set_reference_image`; the AI compares both images on every submission
- **Verification — AI prompt context**: `/verification set_ai_prompt_context` lets admins tell the AI what a valid payment looks like for their community
- **Verification — channel auto-delete**: Verification channels are deleted 5 seconds after resolution instead of being left locked/renamed
- **Verification — audit trail**: `resolved_by_user_id` and `rejection_reason` columns added to `verification_tickets`
- **`/growthcheckin`**: Optional community sharing (anonymous or named) to a configured growth channel
- **`/growthhistory`**: Paginated view of last 15 check-ins with full detail modal and per-entry deletion
- **`/growth set_channel`**: Configure the community growth channel with optional channel picker

#### Bug Fixes
- **Verification AI pipeline**: `ask_gpt_vision` now respects the caller's explicit model — guild `gpt.model` setting was overriding to a text-only model, silently breaking all image analysis
- **Verification AI pipeline**: Fixed content types `input_image`/`input_text` → `image_url`/`text` (xAI chat completions format); images were never reaching the model
- **Default vision model**: Updated to `grok-4-1-fast-reasoning` (`grok-2-vision-latest` deprecated)

---

## [3.1.0] - 2026-03-31 - "Enterprise Ready"

### Minor Release: Auto-Moderation, App Reflections, and Legal Notifications

This release expands moderation capabilities, improves privacy-aware reflection context handling, and adds automated legal update notifications.

#### What's New
- **Auto-Moderation System:**
  - New moderation engine with configurable rule types and actions (`/automod ...`)
  - Rule coverage for spam, bad words, links, mentions, caps, duplicate content, regex (premium), and AI moderation (premium)
  - Database-backed moderation logs, stats, and user history with migration-backed schema
- **App Reflections Webhooks:**
  - New endpoints: `POST /webhooks/app-reflections` and `POST /webhooks/revoke-reflection`
  - Shared reflections are now available for user-self AI flows (such as `/growthcheckin`) without leaking into ticket admin suggestions
- **Legal Update Notifications:**
  - New webhook `POST /webhooks/legal-update` and workflow automation to post legal update embeds when ToS or Privacy docs change
- **New `/innersync` Command:**
  - Informational command with official Innersync platform links

#### Security & Reliability
- Reflection privacy boundary tightened: ticket "Suggest reply" excludes reflection context
- Legal and reflection webhook handling follows HMAC signature validation patterns

#### Developer Experience
- Version references updated to `3.1.0` in docs
- Changelog reorganized with a fresh `[Unreleased]` section

---

## [3.0.0] - 2026-03-09 - "Enterprise Ready"

### 🎉 Major Release: Premium Monetization, Security Framework & Production Infrastructure

This release delivers an enterprise-grade Discord bot with complete monetization, a hardened security framework, universal database helpers, GDPR compliance, and production-ready observability.

#### What's New
- **Premium Monetization Ecosystem:**
  - Full subscription platform with multi-tier pricing (€4.99/mo, €29/year, €49/lifetime) and automated guild assignment
  - Commands: `/premium`, `/premium_check`, `/my_premium`, `/premium_transfer` — one active subscription per user, transferable between servers
  - Early Bird Founder Program: special lifetime tier (€29) with automatic founder role and welcome messaging
  - Core-API integration with HMAC validation, fallback mechanisms, and transfer sync across guilds
- **Universal Database Helpers:**
  - Centralized `DatabaseManager` class (`utils/database_helpers.py`) used across 14 cogs
  - `acquire_safe()` for consistent, safe database access with automatic pool management and error recovery
  - Eliminates connection pool errors, "Future exception was never retrieved" warnings, and timeouts
- **Advanced Security Framework:**
  - Admin credentials via environment variables (`OWNER_IDS`, `ADMIN_ROLE_IDS`) — no hardcoded credentials
  - HMAC webhook validation, OAuth credential security, comprehensive rate limiting
  - Input sanitization across all user inputs (injection, prompt jailbreaks, malicious URLs)
- **GDPR Compliance Suite:**
  - Terms acceptance flow with database persistence; new `terms_acceptance` table (Alembic 006)
  - Data retention policies (7 years for tax compliance), user data export capabilities
- **Join Role (temporary):** Assign a role on join; removed after onboarding completion or verification (`onboarding.join_role_id`, `/config onboarding set_join_role`).
- **Observability:** Dashboard metrics include `premium_metrics` (premium checks, cache hits, transfers, cache size); `/release` command reads release notes from GitHub with link to full notes (`GITHUB_REPO` env).
- **New `/innersync` Command:** Informational command showing Innersync platform details and official links (Core, App, Pricing) with ephemeral response.

#### Technical Improvements
- **Command Structure:** `requires_owner()` decorator for true owner-only commands vs admin-accessible features
- **Embed & Response Consistency:** Unified embed styling; exports use shared CSV helpers and English-only responses
- **Cache Management:** LRU caches with size limits, TTL, and cleanup across settings, cooldowns, rate limits, command stats
- **Premium UX:** After accepting Terms & Privacy in `/premium`, users immediately see the full pricing embed with checkout buttons

#### Bug Fixes
- **Database:** Resolved all connection pool errors and timeout problems through universal helpers
- **Premium:** Fixed guild assignment, checkout URL generation, transfer sync, founder role edge cases
- **Internationalization:** Removed remaining Dutch strings in reminders, inviteboard, dataquery, embed_watcher
- **Exports:** Fixed interaction flow (no `send_error` before `defer()`) in `/export_tickets` and `/export_faq`
- **Syntax & Permissions:** Corrected indentation/imports and admin vs owner permission logic

#### Security
- Credential management via env vars; HMAC validation; IP-based rate limiting; guild-specific access controls
- Input sanitization; data isolation; enhanced audit logging

#### Performance
- Database pool recycling, health checks, optimized query patterns
- Cache optimization, background task lifecycle, memory leak prevention, graceful shutdown

#### Migration
- Zero downtime deployment; backwards compatibility; data integrity validation; rollback-safe migration paths

---

## [2.1.0] - 2026-02-09 - "Lifecycle Manager"

### Google Cloud Secret Manager & docs

- **Secret Manager:** Google Drive credentials load from Secret Manager in production with env fallback for local dev (`utils/gcp_secrets.py`). Correct env var `GOOGLE_CREDENTIALS_JSON` for fallback; optional `return_source` for accurate logging.
- **Drive/lifecycle:** Drive verification at startup runs in a thread to avoid blocking; Drive client init is thread-safe.
- **Docs:** Security guide (`docs/SECURITY.md`), credentials setup (`docs/GOOGLE_CREDENTIALS_SETUP.md`), Railway Secret Manager (`docs/RAILWAY_SECRET_MANAGER_SETUP.md`). README restructured; Operational Playbook in `docs/OPERATIONAL_PLAYBOOK.md`.

---

## [2.0.0] - 2026-01-22 - "Lifecycle Manager"

### 🎉 Major Feature: Centralized Lifecycle Management & Phased Startup/Shutdown

This release introduces a complete refactoring of bot startup and shutdown sequences with phased initialization, centralized resource management, and comprehensive security enhancements.

#### What's New
- **Lifecycle Manager (`utils/lifecycle.py`):**
  - `StartupManager` with 6-phase startup sequence: Database → Settings → Cogs → Command Sync → Background Tasks → Ready
  - `ShutdownManager` with 4-phase shutdown sequence: Cancel Tasks → Unload Cogs → Close Pools → Final Cleanup
  - Reconnect handling with light resync phase (only guild-only commands if intents missing)
  - Humorous logging: "haha bot dropped the call, morgen lachen we er weer mee"
  - Phase-by-phase logging for easier debugging
- **Centralized Database Pool Creation:**
  - `utils/db_helpers.create_db_pool()` with automatic pool registry
  - All 15 cogs now use centralized pool creation (ticketbot, reminders, embed_watcher, faq, onboarding, dataquery, status, exports, inviteboard, gdpr, importdata, importinvite)
  - Consistent pool configuration across all cogs
  - Automatic cleanup via `close_all_pools()` during shutdown
- **Input Sanitization & Security:**
  - Centralized sanitization utility (`utils/sanitizer.py`) for protecting against injection attacks
  - `escape_markdown()`, `strip_mentions()`, `url_filter()`, `safe_embed_text()`, `safe_prompt()`, `safe_log_message()`
  - Applied sanitization to all user input flows (reminders, tickets, FAQ, onboarding, GPT prompts)
  - Comprehensive test suite with 50+ parametrized tests
- **Memory Leak Fixes & Resource Management:**
  - Command tracker batching with in-memory queue (max 10k entries)
  - Guild settings LRU cache with max 500 entries
  - IP rate limits cleanup with max 1000 entries
  - Command stats TTL cache (30-second TTL, max 50 entries)
  - Sync cooldowns cleanup (every 10 minutes, max 500 entries)
  - Ticket bot cooldowns cleanup (max age 1 hour, max 1000 entries)
  - Cache size monitoring via `CacheMetrics` in dashboard endpoint
- **Command Tree Sync Refactoring:**
  - Centralized command sync utility (`utils/command_sync.py`) with cooldown protection
  - Automatic sync on bot startup (global once, then guild-only per guild in parallel)
  - Automatic sync when bot joins new guilds
  - Cooldown tracking: 60 minutes for global syncs, 30 minutes for per-guild syncs
  - Rate limit protection with graceful error handling
- **Rate Limiting & Abuse Prevention:**
  - Command cooldowns for high-risk operations (`/add_reminder`, `/learn_topic`, `/create_caption`, `/growthcheckin`, `/leaderhelp`)
  - FastAPI IP-based rate limiting middleware (30 reads/min, 10 writes/min per IP)
  - In-memory cooldown for ticket "Suggest reply" button

#### Technical Improvements
- **Startup/Shutdown Improvements:**
  - Fixed "Known guilds: 0" logging issue - guilds load after connect, now shows debug message if not yet available
  - Fixed python-dotenv warning about empty lines in .env file - warnings now suppressed
  - Added shard ID logging - shows debug message for single-shard bots, ready for future multi-shard support
- **Dependency Ordering:**
  - SettingsService initialized before cogs
  - Cogs loaded before command sync
  - Background tasks started after all initialization
- **Resource Cleanup:**
  - All database pools properly closed during shutdown
  - Background tasks cancelled with timeouts
  - Cogs unloaded in reverse order of loading

#### Bug Fixes
- **Race Conditions:**
  - Eliminated DB pool race conditions - sequential phases ensure dependencies are ready
  - Fixed SettingsService dependency - guaranteed initialization before cog `__init__`
  - Fixed command sync timing - sync happens after cogs are fully loaded
- **Double Sync:**
  - Explicit tracking of first startup vs reconnect prevents duplicate syncs
  - Reconnect phase only syncs guild-only commands if intents missing
- **Missing Cleanup:**
  - All background tasks now properly cancelled on shutdown
  - All database pools closed in correct order
  - Bot-level tasks cleaned up (sync cooldowns cleanup, command tracker flush, GPT retry queue)

#### Security Enhancements
- **Input Sanitization:**
  - Protection against markdown injection attacks (15+ patterns tested)
  - Protection against mention spam attempts
  - Protection against prompt injection/jailbreak attempts (15+ patterns tested)
  - URL exploit filtering
  - Length limit attacks prevented
- **Rate Limiting:**
  - Command cooldowns prevent spam and cost explosions
  - API IP-based rate limiting prevents anonymous abuse
  - Health/metrics endpoints excluded from rate limiting

---

## [1.9.0] - 2026-01-21 - "Enhanced Reminders"

### 🎉 Major Feature: Reminder Management & Embed Watcher Improvements

This release focuses on enhancing reminder functionality with editing capabilities, improved embed parsing, and better error handling.

#### What's New
- **Reminder Edit Command:** `/reminder_edit` with full modal interface for editing existing reminders
  - Edit name, time, days, message, and channel ID
  - Pre-fills with current reminder values including event time (T0)
  - Channel ID editing support in modal
- **Embed Watcher Enhancements:**
  - Footer text extraction and inclusion in reminder parsing
  - Bot message processing toggle via `embedwatcher.process_bot_messages` setting
  - Rich Discord embed logging for better visibility
  - Smart duplicate detection to prevent content overlap
  - Full day name display in logs
  - One-off event weekday storage for informational display
- **GPT Retry Queue System:**
  - Automatic retry queue for rate-limited or failed GPT API requests
  - Exponential backoff retry mechanism
  - Fallback message for degraded AI service scenarios
  - Background task for processing queued requests

#### Internationalization
- **Complete English Translation:** All Dutch user-facing text replaced with English
  - Command descriptions and parameter descriptions
  - Error messages and success notifications
  - Log messages and embed content
  - GPT system prompts

#### Technical Improvements
- **Logging Improvements:**
  - Converted plain text logs to structured Discord embeds
  - Reduced console verbosity by removing excessive debug statements
  - Enhanced log formatting with proper day names and structured fields
- **Message Content Handling:**
  - Smart duplicate detection prevents title/description overlap
  - Footer handling in embed watcher for consistent formatting
  - Name field sanitization for Discord modal compliance

#### Bug Fixes
- **Reminder Edit Modal:**
  - Fixed `HTTPException: 400 Bad Request` error caused by newlines in name field
  - Corrected time display to show event time (T0) instead of reminder time (T-60)
  - Fixed message duplication issue when editing reminders
  - Resolved Discord modal field limit by optimizing field usage
- **Embed Watcher:**
  - Fixed footer not being stored correctly in reminders
  - Fixed one-off events missing weekday information
  - Fixed bot message processing loop protection
  - Improved parsing failure logging with detailed error information
- **GPT Integration:**
  - Fixed `RuntimeError: no running event loop` when starting retry queue task
  - Corrected linter error for `status_code` attribute access
  - Improved error handling for rate limits and API failures

---

## [1.7.0] - 2025-11-16 - "Multi-Guild Horizon"

### 🎉 Major Feature: Complete Multi-Guild Support + Advanced Onboarding

Alphapy now supports unlimited Discord servers with complete data isolation, independent configuration, and a comprehensive onboarding system.

#### What's New
- **Guild Isolation**: All features (reminders, tickets, invites, settings, onboarding) work independently per server
- **Modular Onboarding**: Fully configurable onboarding flows with custom questions, rules, and completion roles
- **Onboarding Panels**: Admin commands to post onboarding start buttons in any channel
- **Email/Text Support**: Modal-based input handling for all question types including optional fields
- **Database Schema**: Added `guild_id` columns to all tables with composite primary keys for data separation
- **API Security**: Dashboard endpoints with optional `guild_id` filtering to prevent cross-guild data leakage
- **Migration Tools**: Safe database migration scripts with backup/restore capabilities
- **Error Handling**: Comprehensive guild validation and duplicate record handling

#### Migration Summary
- ✅ **All tables migrated** with `guild_id` support and composite primary keys
- ✅ **Zero downtime** deployment with full backup verification
- ✅ **Complete backwards compatibility** maintained
- ✅ **Security hardening** - no cross-guild data leakage possible

#### Onboarding System Features
- **Question Types**: Support for select, multiselect, text, and email input types
- **Optional Questions**: Users can skip optional fields (like email addresses)
- **Custom Rules**: Guild admins can define custom server rules during onboarding
- **Completion Roles**: Automatic role assignment upon onboarding completion
- **Panel Management**: `/config onboarding panel_post` to place onboarding buttons anywhere
- **Re-onboarding**: Users can update their responses and redo onboarding

#### Technical Improvements
- **Type Safety**: Zero pyright errors with complete type checking
- **Settings Service**: Guild-scoped configuration with per-server overrides
- **Code Architecture**: All cogs updated with `interaction.guild.id` validation
- **Database**: Composite primary keys `(guild_id, user_id)` or `(guild_id, id)` across all tables
- **API Endpoints**: Guild filtering implemented for security in dashboard metrics
- **Modal Handling**: Robust text input modals with optional field support
- **Error Recovery**: Graceful handling of duplicate records and migration edge cases

#### Security Enhancements
- **Data Isolation**: Complete separation between guild data preventing unauthorized access
- **Input Validation**: Guild existence checks prevent DM usage errors
- **API Filtering**: Dashboard endpoints properly filter by guild context
- **Migration Safety**: Backup verification and rollback capabilities

#### Bug Fixes
- **Onboarding Flow**: Fixed crashes when processing email/text questions
- **Database Constraints**: Resolved duplicate key violations in onboarding records
- **Modal Handling**: Added support for optional text input fields
- **Type Checking**: Eliminated all pyright errors across the codebase
- **Syntax Errors**: Fixed import and compilation issues

---

## [1.6.0] - 2025-10-17 - "Dashboard Foundations"

### Added
- FastAPI dashboard endpoint `/api/dashboard/metrics` exposing live bot telemetry
- Service health probe `/health` returning service status and database connectivity
- Supabase Auth integration with automatic profile bootstrap and OAuth flows

### Changed
- Project branding updated to **Innersync • Alphapy** across all documentation
- CORS origins and base URLs aligned with Innersync domain suite
- Supabase JWT validation moved to server-side verification

---

## [1.5.0] - 2025-10-04 - "Dynamic Config"

### Added
- Runtime configuration system with per-guild settings management
- Settings commands: `/config system show`, `/config embedwatcher show`, etc.
- Invite tracker with enable/disable toggle and customizable templates
- GDPR announcements with runtime settings control

### Changed
- Bot startup validation improved with early token checks
- Import flows made more robust with embed existence checks

---

## [1.4.0] - 2025-09-13 - "TicketBot & FAQ"

### Added
- Complete TicketBot system with private channels, status management, and AI assistance
- FAQ search system with autocomplete and admin management tools
- Ticket statistics and export functionality
- AI-assisted reply suggestions for support tickets

---

## Previous Releases

See [CHANGELOG.md](changelog.md) for detailed change history of all versions.
