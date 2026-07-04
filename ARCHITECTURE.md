# 🧬 Innersync • Alphapy Architecture

## High-level overview
- Discord bot (discord.py) with modular cogs + FastAPI layer (`api.py`)
- PostgreSQL + Alembic migrations for all persistent data
- Supabase for telemetry (Mind dashboard) and auth
- Closed loop: Alphapy catches input → Core-API processes → Mind monitors → App reflects

## Key modules
- `cogs/onboarding.py` + `reaction_roles.py`: configurable onboarding + rules
- `cogs/reminders.py` + `embed_watcher.py`: manual + auto reminders (NLP parsing)
- `cogs/verification.py`: AI vision payment verification
- `cogs/ticketbot.py`: support tickets with GPT summary + FAQ proposal
- `cogs/premium.py`: tier guard + transfer
- `cogs/automod.py` + `configuration.py` (`automod_group`): automated content moderation with rule engine
- `cogs/engagement.py` + `utils/engagement_service.py`: community gamification (challenges, weekly awards, streaks, badges, OG claims)
- `api.py`: dashboard endpoints + telemetry ingest job (subsystem='alphapy'; optional `agent_sessions` on metrics + `agents:` counts in snapshot notes)
- `utils/`: lifecycle, db_helpers, sanitizer, embed_builder, command_tracker, fyi_tips, automod_rules, automod_logging, automod_analytics, engagement_service

## Command structure
- All configuration groups are **standalone top-level command groups** with `default_permissions=administrator` — `/automod`, `/verification`, `/onboarding`, `/engagement`, `/system`, `/embedwatcher`, `/ticketbot`, `/gpt`, `/invites`, `/reminders`, `/gdpr`, `/growth`, `/fyi`
- `/config` is reserved for cross-scope utilities: `start` (setup wizard) and `scopes`
- All groups are defined as class-level `app_commands.Group` attributes in `cogs/configuration.py` and loaded via `cogs.configuration`

## Database Architecture
- Multiple asyncpg pools (FastAPI + per-cog dedicated)
- Central `bot_settings` + `audit_logs` + feature tables (reminders, onboarding, support_tickets, automod_rules, automod_logs, etc.)
- Auto-moderation: 5 tables (`automod_actions`, `automod_rules`, `automod_logs`, `automod_stats`, `automod_user_history`) with indexes for performance
- Engagement: 9 tables (`engagement_badges`, `engagement_og_claims`, `engagement_og_setup`, `engagement_challenges`, `engagement_participants`, `engagement_weekly_messages`, `engagement_weekly_awards`, `engagement_weekly_results`, `engagement_streaks`) — all multi-guild scoped
- Command tracking via batch queue in the bot event loop
- See `docs/database-schema.md` for full tables

## Secrets & Infrastructure
- Google Drive: `GOOGLE_CREDENTIALS_JSON` env var (`utils/drive_sync.py`)
- All other secrets via environment variables (Railway / `.env`), zero hard-coded values

## Control flow
1. Startup → `StartupManager` (phased: DB → settings → cogs → sync → background tasks)
2. New guild / reconnect → minimal resync of commands
3. User input → cog → sanitizer → database and/or GPT
4. Background jobs → reminders, telemetry push to Supabase, cleanup tasks
5. Observability → operational logs + `/api/dashboard/*` for Mind

## Observability, Security & Testing
- Request observability in FastAPI via `RequestObservabilityMiddleware`:
  - `X-Request-ID` propagation for correlation
  - rolling latency percentiles (p50/p95/p99)
  - success-rate tracking for API routes and webhook routes
- Operational logs (in-memory buffer) + telemetry to Mind dashboard
- Input sanitization via `utils/sanitizer.py` to guard against injection/spam
- Rate limiting on Discord side and FastAPI IP middleware
- Idempotency protection on reminder write endpoints (`POST/PUT/DELETE /api/reminders*`) via `Idempotency-Key` header
- 50+ pytest tests covering parsing, security, and edge cases

## Shared References
- Full schema: `docs/database-schema.md`
- Detailed changes: `docs/CHANGELOG.md` (or `docs/DETAILS.md`)
- Tests: `tests/`
- Embed guide: `EMBEDS.md`