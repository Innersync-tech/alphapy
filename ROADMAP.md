# 🧬 Innersync • Alphapy Roadmap v3.9.0 "Reflection Loop"

**Release v3.9.0 Complete!** 🤖 Alphapy Agents MVP — `/agent` for linked users, Supabase sessions/memory, safety policy, jailbreak-tested.

This document outlines the evolution from v3.9.0 forward.

## ✅ COMPLETED: Alphapy Agents MVP (v3.9.0)

**Status:** ✅ **Merged to main** (PR #246, 30 jun 2026)

- **Multi-user agents**: `reflection` agent with `journal_sync` skill; `/agent list|start|status`
- **Gates**: `ALPHAPY_AGENTS_ENABLED` (global), `/config agents toggle` (per guild), `/link` required
- **Memory**: Supabase `agent_sessions` + `agent_memory` (Core `0020`)
- **Safety**: `agents/policy.py`, Matrix A manual probes passed on Innersync Dev
- **Cross-repo**: [Innersync-meta/AGENT-SAFETY-POLICY.md](https://github.com/Innersync-tech/Innersync-meta/blob/main/AGENT-SAFETY-POLICY.md), [ROADMAP-ALPHAPY-AGENTS.md](https://github.com/Innersync-tech/Innersync-meta/blob/main/ROADMAP-ALPHAPY-AGENTS.md)

**Next (Phase 3+):** skills expansion, App agent runs (Phase 4.0). **Phase 4 GDPR purge** for agent Supabase tables shipped in Unreleased.

---

## ✅ COMPLETED: Agent Memory & Conversation (v3.10.0 — Phases 2.1–2.3)

**Status:** ✅ **Shipped** (30 jun 2026) — Alphapy [#255–#257](https://github.com/Innersync-tech/alphapy/pulls?q=is%3Apr+agent+memory), App [#101–#106](https://github.com/Innersync-tech/innersync-dashboard/pulls?q=is%3Apr+agent), Core [#58–#59](https://github.com/Innersync-tech/Innersync_Core/pulls?q=0022+OR+0023)

- **Tier 1 prefs** — `display_name`, persona, focus via App `agent_prefs`
- **Tier 2 derived profile** — consent-gated distill; purge on revoke
- **Tier 3 metadata** — `session_count`, `last_session_at` (no journal text)
- **Multi-turn** — `/agent start` → `/agent continue` → `/agent end`; `agent_session_messages` (Core `0023`)
- **App** — Settings hub; Alphapy section (sharing + agent memory); session history with per-row delete

---

## ✅ COMPLETED: Innersync Identity & Hermit Context (v3.8.0)

**Status:** ✅ **Fully Implemented & Released**

- Discord ↔ Innersync linking (`/link`), Hermit strategic context, auto-mod UX, cache observability

---

## ✅ COMPLETED: API Observability & CI Quality Gates (v3.7.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release focused on operational visibility, reliability, and quality baseline improvements:
- **API observability**: New internal `GET /api/observability` endpoint with rolling request counts, success rate, and latency percentiles for API + webhook traffic
- **Request tracing**: Added/propagated `X-Request-ID` and per-request outcome/latency tracking middleware
- **Reminder API idempotency**: `POST`/`PUT`/`DELETE` reminders now support `Idempotency-Key` to prevent duplicate writes on client retries
- **Schema governance**: Removed runtime schema-create behavior from API startup; schema changes are now migration-driven via Alembic
- **CI quality gates**: Ruff, Pyright, and pytest coverage checks wired into CI
- **Engagement stability fixes**: challenge finalization crash and post-restart progress regressions resolved

---

## ✅ COMPLETED: Settings Hot-Reload & Dashboard Parity (v3.6.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release completing bot–dashboard schema parity and polishing the settings infrastructure:
- **Settings hot-reload**: `GlobalSettingListener` + `add_global_listener()` in `SettingsService`; `CachedSettingsHelper` auto-evicts stale LRU entries on every change — no restart needed for config changes to take effect
- **Config audit trail**: `config_audit_log` table (migration 019); `SettingsService.set()` writes audit rows; dashboard history/rollback endpoints now backed by real data
- **Automod analytics**: `false_positive_rate`, `avg_response_time`, `trend` (7-day daily counts), `export_metrics()`, and `export_logs()` implemented with real DB queries
- **Growth checkins**: `growth_checkins` table added (migration 020), resolving dashboard growth tab error
- **`/config show` embeds**: All 10 show handlers now return paginated embeds with `SettingsPageView` (Prev/Next buttons when > 10 settings)
- **TicketBot pool**: Migrated to shared `get_bot_db_pool()`, removing private `DatabaseManager`
- **English strings**: 8 Dutch strings fixed across 6 cog files
- **Bug fix**: Ticket metrics snapshot `$6` parameter type error resolved
- **Dashboard refactor**: `page.tsx` split from 3806 → 1034 lines into 35 focused components

---

## ✅ COMPLETED: GDPR Slash Commands & Reliability Fixes (v3.5.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release converting GDPR management to slash commands and hardening the ticket and DB layers:
- **GDPR**: `/config gdpr post` replaces `!postgdpr`; `/config gdpr set_acceptance_role` configures auto-role on consent; `utils/gdpr_helpers.py` extracted to avoid circular imports; migration 018 adds `guild_id` to `gdpr_acceptance`
- **Tickets**: Transcript (up to 500 messages) exported as `.txt` to log channel before channel deletion on archive
- **Tickets**: `guild.fetch_channel()` replaces stale-cache `guild.get_channel()` for category validation; `HTTPException` guard on channel creation
- **Tickets**: Panel embed copy made generic (community-agnostic)
- **DB**: `acquire_safe` catches `TimeoutError` from asyncpg connection reset; reminder loop logs it at `WARNING` instead of `ERROR`
- **API**: `GET /api/dashboard/{guild_id}/gdpr` returns acceptance count for the guild

---

## ✅ COMPLETED: Config Consolidation & GDPR Compliance (v3.4.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release consolidating `/config` commands and shipping GDPR compliance features:
- **Config consolidation**: `/config` tree reduced from 101 to 71 subcommands; `reset_X` merged into `set_X` (no-value clears); `enable`/`disable` pairs replaced by `toggle <bool>`; `automod enable_rule`/`disable_rule` replaced by `set_rule_enabled`
- **`/delete_my_data`**: Self-service GDPR erasure command for users
- **Retention cleanup**: Daily background task deletes `audit_logs` and `faq_search_logs` older than 90 days
- **Migration 016**: Formalises `gdpr_acceptance` table; drops unused `ip_address` column
- **Verification**: Payment recency validation (configurable window, default 35 days); reviewer role; identity check (name/username match via AI)
- **Migration 017**: Adds `payment_date DATE` to `verification_tickets`
- **Bug fixes**: `on_app_command_error` decorator corrected; GDPR accept button `ctx` NameError; GDPR erasure gaps; terms version mismatch

---

## ✅ COMPLETED: Security Hardening (v3.3.1)

**Status:** ✅ **Fully Implemented & Released**

Patch release focused entirely on security remediation from the 2026-04-13 internal security audit:
- **Auth bypass fixed**: `X-User-ID` header fallback removed — identity exclusively from verified JWT
- **Async JWT**: `verify_supabase_token` no longer blocks the FastAPI event loop
- **CVE deps**: 7 CVEs resolved across `cryptography`, `pyopenssl`, `PyMuPDF`, `requests`
- **Privilege escalation**: `/migrate downgrade` restricted to bot owners only
- **Error disclosure**: 28 user-facing exception strings replaced with generic messages across 8 cog files
- **SQL safety**: Alembic migration 013 uses `sa.text()` + bind params
- **Config hygiene**: `OWNER_IDS` and `ADMIN_ROLE_ID` moved to env vars
- **Rate limiting**: Health/metrics endpoints now included in rate limiting
- **Observability**: Startup warnings for unauthenticated API mode and unset webhook secrets
- **Docs**: Application-level security reference added to `docs/SECURITY.md`

## ✅ COMPLETED: Verification Overhaul & Growth Check-in Improvements (v3.3.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release completing the verification workflow and growth community features:
- **Verification**: Manual approve/reject buttons for admin review, reference image comparison, AI prompt context setting, channel auto-delete on resolution, audit trail columns
- **Verification AI pipeline**: Fixed vision model override bug (guild `gpt.model` was silently overriding vision model), fixed content types for xAI chat completions, updated default to `grok-4-1-fast-reasoning`
- **Growth check-in**: Community sharing (anonymous/named) to configured channel, `/growthhistory` with paginated view + deletion, `/config growth set_channel` with picker

## ✅ COMPLETED: Premium Tier Differentiation & GPT Quotas (v3.2.0)

**Status:** ✅ **Fully Implemented & Released**

Minor release adding real tier differentiation, quota enforcement, and improved /premium UX:
- **Premium tiers**: free/monthly/yearly/lifetime with GPT daily quotas (free: 5, monthly: 25, yearly/lifetime: unlimited) and reminder limits (free: 10, others: unlimited)
- **Ticket GPT summaries**: gated on guild-level premium; non-premium guilds skip AI summary
- **Expiry warnings**: background task sends DM 7 days before subscription expires
- **Early bird pricing**: live availability check via Core-API (`/billing/early-bird/validate`, cached 5 min); buttons show early bird vs regular prices dynamically
- **`/premium` buttons**: fixed 404 — now links to `PREMIUM_CHECKOUT_URL` directly (pricing site handles OAuth + Mollie checkout)
- **`/my_premium`**: extended with tier badge, expiry countdown, GPT quota (X/Y), reminder count (X/Y)
- **`/gptstatus`**: complete overhaul — uses own in-memory logs instead of OpenAI status page; rolling latency average; rate limit tracking; retry queue visibility
- **set_model**: restricted to bot owner only for billing protection; added per-guild override via `guild_id` param

## ✅ COMPLETED: Performance & Stability Hardening (v3.1.2)

**Status:** ✅ **Fully Implemented & Released**

Patch release focused on runtime performance and operational stability:
- **Performance**: Persistent `httpx.AsyncClient` in premium guard — eliminates TCP connection churn on Core-API calls
- **Performance**: GPT retry queue — concurrent processing with `asyncio.Lock` (TOCTOU race fixed); full queue drains in ~16s vs ~25 minutes
- **Performance**: `set_bulk` rewritten with single `acquire_transactional` + `executemany` — N DB roundtrips → 1 transaction
- **Reliability**: Fire-and-forget channel log tasks now tracked; exceptions surface instead of being silently dropped
- **Bug fixes**: Onboarding `TypeError` on summary send when view is `None`; Dutch error message in configuration
- **Observability**: Suppressed `UNKNOWN_SETTING` log noise for `fyi.*` keys; migration 013 cleans up stale `bot_settings` rows

## ✅ COMPLETED: Codebase Health & Security Hardening (v3.1.1)

**Status:** ✅ **Fully Implemented & Released**

Patch release focused on internal quality, security, and correctness:
- **Security**: Dashboard settings and onboarding endpoints now require guild admin access (was auth-only)
- **GDPR**: User data erasure on `USER_DELETED` event — fully compliant with GDPR right to erasure
- **Refactoring**: Repository pattern (`reminder_repository`), `AlphaCog` base class, `embed_parser` service, `configuration.py` split
- **Performance**: `guild_id` indexes on high-traffic tables via Alembic migration 012
- **Bug fixes**: Onboarding modal Discord limits, embed watcher one-off detection, location field in reminders
- **Test coverage**: 17 new API endpoint tests (300 total)

## ✅ COMPLETED: Auto-Moderation + Legal/App Reflection Integrations (v3.1.0)

**Status:** ✅ **Fully Implemented & Released**

Recent platform extensions shipped in v3.1.0:
- **Auto-Moderation**: full `/config automod` command suite, moderation rule engine, DB-backed logs/stats/history, premium-gated advanced actions and AI analysis
- **Legal update automation**: workflow + webhook flow for Terms/Privacy updates (`POST /webhooks/legal-update`) with signed payload handling
- **App reflections integration**: inbound reflection/revoke webhooks with privacy-safe context usage (user-self flows only; excluded from ticket suggestion flow)
- **Operational cleanup**: branch naming/docs alignment to `main`, and tightened release hygiene around version references

## ✅ COMPLETED: Multi-Guild Architecture + Advanced Onboarding (Phase 1.5)

**Status:** ✅ **Fully Implemented & Deployed**

The bot now supports unlimited Discord servers with complete data isolation between guilds. All features (reminders, tickets, invites, settings, onboarding) work independently per server, enhanced with a comprehensive modular onboarding system.

### What Was Implemented:
- **Database Schema:** Added `guild_id` columns to all tables with composite primary keys
- **Code Isolation:** Updated all cogs to use `interaction.guild.id` for guild-specific operations
- **Settings Service:** Guild-aware configuration with per-server overrides
- **API Security:** Dashboard endpoints now support optional `guild_id` filtering
- **Migration Tools:** Safe database migration scripts with backup/restore capabilities
- **Error Handling:** Guild validation checks across all commands
- **Modular Onboarding:** Complete onboarding system with configurable questions, rules, and completion roles
- **Panel Management:** Admin commands to post onboarding start buttons in any channel
- **Question Types:** Support for select, multiselect, text, and email input with modal handling
- **Type Safety:** Type debt reduction is in progress; Pyright currently runs in advisory mode while targeted cleanup phases reduce legacy errors.

### Deployment Summary:
- ✅ **135 data entries** successfully migrated to guild `1160511689263947796`
- ✅ **Zero downtime** deployment with backup verification
- ✅ **Full backwards compatibility** maintained
- ✅ **Security hardening** - no cross-guild data leakage

---

## 🔄 **Phase 1.75: Web Configuration Interface**

**Status:** 📋 **Planned - Post Multi-Guild Release**

Transform the extensive slash command configuration system into a user-friendly web interface for server administrators.

### Why This Matters:
- **20+ settings** currently managed via slash commands
- Not all admins prefer Discord command-line interfaces
- Web interface enables better UX, batch operations, and mobile access
- Aligns with modern admin dashboard expectations

### Implementation Scope:
- **Discord OAuth2 Integration** - Secure login with guild permissions
- **Settings Dashboard** - Visual overview of all guild configurations
- **Batch Operations** - Update multiple settings simultaneously
- **Rule/Question Builders** - Drag-and-drop onboarding customization
- **Visual Previews** - See how changes affect user experience
- **Settings History** - Audit trail and rollback capabilities
- **Community Templates** - Pre-built configurations for common use cases

### Technical Foundation:
- **Frontend:** Next.js (separate repository: `alphapy-dashboard`)
- **Backend:** Extend FastAPI `/api/dashboard/*` endpoints
- **Database:** Direct access to guild settings tables
- **Auth:** Discord OAuth2 with per-guild permission checks
- **Hosting:** Railway (same platform as bot)

### Success Metrics:
- Reduced configuration time for server admins
- Increased adoption of advanced features
- Positive feedback from mobile users
- Fewer support questions about configuration

---

## Themes
- Faster answers: FAQ search and autocomplete directly in Discord
- Better workflows: richer ticket statuses and handoffs
- Smarter support: AI-assisted replies grounded in your docs (light RAG)
- Human-first tone: empathetic responses on sensitive signals
- Visibility: export tools, metrics, and dashboards
- ✅ **Multi-tenant:** Complete guild isolation and per-server configuration

---

## Whitepaper Prep – Runtime Configuration Roadmap

| Phase | Status | Scope |
| --- | --- | --- |
| 0 | ✅ Done | Settings foundation (`SettingsService`, `/config` shell, embed watcher + reminders wired in) |
| 1 | ✅ Done | Expand setting registrations (reminder scheduler options, Grok/LLM throttles, invite templates, GDPR toggles) |
| 2 | ✅ Done | Slashcommand UX polish: paginated `/config … show` with `SettingsPageView`, embed-based output for all 10 show handlers |
| 3 | ✅ Done | Service listeners + cache refresh (`GlobalSettingListener`, auto-invalidating LRU); TicketBot migrated to shared DB pool |
| 4 | ✅ Done | Observability: config audit trail to DB (migration 019); automod analytics implemented; growth checkins table (migration 020) |
| 5 | 🔄 Next | Launch checklist + docs update: whitepaper excerpt, admin hand-off guide |

### Implementation Checklist
- [x] Create `SettingsService` with typed definitions and DB-backed overrides.
- [x] Register core settings (log channel, embed watcher channel + offset) and expose `/config` commands.
- [x] Refactor remaining cogs to consume `bot.settings` (TicketBot logs, Grok config, invite tracker templates, GDPR announcements).
- [x] Add onboarding helpers: `/config reminders set-default-channel`, `/config gpt set-model`, `/config invites set-message`.
- [x] Implement settings listeners for hot-reload behaviour (e.g., reminder interval, Grok rate limits).
- [x] Add tests (`tests/test_settings_service.py`) covering coercion, persistence and permission checks.
- [x] Document admin workflow in `docs/configuration.md` and surface summary in CHANGELOG.

### Key Decisions & Open Questions
- **Database migrations:** For now we stay with Alembic for all schema changes; we revisit if the migration chain grows unwieldy.
- **Permission tiers:** No extra role needed; owners/admins remain the only configuration managers.
- **Secret storage:** Secrets (`BOT_TOKEN`, `OPENAI_API_KEY`, DB credentials) stay in `.env`/secrets manager; everything that requires runtime control (channels, offsets, toggles) goes via the settings-service.
- **Rollout order:** We keep the order embed/reminders → ticketbot → Grok → invites → GDPR → other utilities.

---

## Milestones

### M1 — FAQ Search & Autocomplete (Discord)
- Commands:
  - `/faq` root: subcommands
    - `/faq search query:<text>` – returns top 5 FAQ entries, link/embed
    - `/faq view id:<int>` – show single entry
    - `/faq list` – recent or pinned
- Autocomplete: suggest entries while typing `query` (based on title/keywords)
- Storage: reuse `faq_entries (id, similarity_key, summary, created_by, created_at)`
- Scoring: simple TF-IDF-like sim or token overlap; later pluggable for embeddings
- Observability: log searches (term, hit_count) for later analytics
- Risk/Notes: rate-limit safe, ephemeral responses for privacy

### M2 — Ticket Status Model & Workflows
- New statuses: `open`, `claimed`, `waiting_for_user`, `escalated`, `closed`
- UI:
  - Add "Wait for user" and "Escalate" buttons (staff-only)
  - Visual chip in the ticket embed to reflect status
  - Optional escalation target role/channel
- DB:
  - `support_tickets.status` (already exists)
  - Add `escalated_to BIGINT NULL` (role or user ID)
  - Add `updated_at TIMESTAMPTZ` for SLA/aging
- Commands:
  - `/ticket status <id> <status>` (admin)
- Notifications: optional ping on status change (configurable)

### M3 — AI Agent Replies (Light RAG)
- Goal: assistant provides draft replies in the ticket channel
- Source: small doc set (local `data/faq/*.md` + `faq_entries` + pinned context)
- Flow:
  - Button "💬 Suggest reply" (staff-only) → ephemeral draft
  - Staff can edit and post; bot never auto-sends without approval
- Implementation:
  - Add `gpt/rag_index.py` (build small in-memory index per boot)
  - Helper `generate_support_reply(context, question)` using `ask_gpt`
  - Safety: redact tokens/PII when echoing user text, respect blocked prompts
- Observability: track acceptance rate of suggestions, time saved proxy

### M4 — Exports, Metrics, Dashboards
- Exports:
  - `/export tickets <from> <to>` → CSV (id, user, status, age, claimed_by, timestamps)
  - `/export faq` → CSV of knowledge base entries
- Metrics & dashboards (first cut):
  - Ticket volume by day, avg time-to-claim/close, status distribution
  - FAQ coverage: top search terms without hits
  - Implementation path: simple FastAPI endpoints consumed by a lightweight dashboard (e.g., Observable/Streamlit) or CSVs to GDrive
  - ✅ Initial delivery: `/api/dashboard/metrics` serves live bot/Grok/reminder/ticket telemetry backed by `utils/runtime_metrics`
  - ✅ `/api/dashboard/logs` operational logs with guild-specific filtering (7 event types: BOT_READY, BOT_RECONNECT, BOT_DISCONNECT, GUILD_SYNC, ONBOARDING_ERROR, SETTINGS_CHANGED, COG_ERROR) — v2.2.0
  - ✅ Infrastructure now has `/health` and shared `*.innersync.tech` base URLs exposed via `config.py`

---

## Detailed Specs & Changes

### Database
- `support_tickets`
  - Add `updated_at TIMESTAMPTZ DEFAULT NOW()` (update on any status change)
  - Add `escalated_to BIGINT NULL`
  - Indexes: `idx_support_tickets_status`, `idx_support_tickets_updated_at`
- `faq_entries`
  - Optional: add `title TEXT`, `keywords TEXT[]` for better UX
- New (optional later): `faq_search_logs(id, query, matched_ids INT[], created_at)`

### Commands & Permissions
- All admin/staff checks continue to use `is_owner_or_admin_interaction`
- New commands: `/faq search|view|list`, `/ticket status`, `/export tickets|faq`

### UI & UX
- Ticket embed shows status chip and last updated timestamp
- Buttons: Claim, Close, Delete (existing) + Wait for user, Escalate, Suggest reply
- Autocomplete: for `/faq search query` on keyup

### AI & RAG
- Docs: `data/faq/*.md` + DB entries
- Index: build on startup; reload via `/faq reload` (admin)
- Guardrails: never send replies automatically; always draft → confirm

### Observability
- Structured logs for `/faq` queries
- Simple counters per feature (prometheus-ready if needed later)
- Error reporting remains via `WATCHER_LOG_CHANNEL`

### Security & Privacy
- Ephemeral drafts for AI suggestions
- Respect `OWNER_IDS` and `ADMIN_ROLE_ID` for all staff operations
- PII redaction in prompts where feasible

---

## Rollout Plan
- Week 1–2: M1 (FAQ search/autocomplete) + storage and logs
- Week 3: M2 (status model + buttons + UI)
- Week 4: M3 (AI draft replies, light RAG)
- Week 5: M4 (exports + simple dashboard wiring)

Each milestone is releasable independently. Start with guild-scoped commands for fast iteration, then promote to global.

---

## Nice-to-haves (Backlog)
- Embedding-based search upgrade for FAQ (OpenAI embeddings or local)
- SLA timers and alerts (e.g., no reply in X hours)
- ✅ **Multi-tenant guild configuration and per-guild FAQ sets** - COMPLETED in Phase 1.5!
- Attachment ingestion (images/PDF) for RAG with safety checks
- Auto-translation layer (UI + summaries)
