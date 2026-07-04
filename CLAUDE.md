# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Alphapy is a modular, multi-guild Discord bot for the Innersync • Alphapips community. It combines Discord slash commands, a FastAPI HTTP layer, PostgreSQL (via Supabase/asyncpg), Alembic migrations, and Grok/OpenAI AI features.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env   # set BOT_TOKEN, DATABASE_URL, and optional vars

# Apply database migrations
alembic upgrade head   # or: alembic stamp head if DB already has schema

# Run the bot
python bot.py

# Run all tests (repo root on PYTHONPATH — see pytest.ini pythonpath = .)
# Equivalent: PYTHONPATH=. pytest tests/ -v  (matches CI)
pytest tests/ -v

# Run a single test file
pytest tests/test_embed_watcher_parsing.py -v

# Run a single test function
pytest tests/test_sanitizer.py::test_function_name -v

# Create a new migration
alembic revision --autogenerate -m "description"
```

## Architecture

### Entry points
- **`bot.py`** — initializes the Discord bot, loads cogs via `utils/lifecycle.py` (phased startup: DB → settings → cogs → command sync), and starts background tasks
- **`api.py`** — FastAPI server for HTTP endpoints (reminders CRUD, dashboard metrics/logs including optional `agent_sessions`, webhooks from Core-API)
- **`config.py`** — all environment variable loading; required vars are `BOT_TOKEN` and `DATABASE_URL`

### Core layers

**`cogs/`** — each file is a Discord Cog with slash commands. Key cogs:
- `configuration.py` — guild settings wizard (largest file, ~139KB)
- `ticketbot.py` — support tickets with Grok summaries and FAQ proposals
- `reminders.py` — manual reminder management
- `embed_watcher.py` — auto-detects event embeds and creates reminders from them
- `onboarding.py` — configurable multi-step onboarding flow
- `automod.py` — content moderation with rule engine
- `premium.py` — subscription tier management

**`utils/`** — shared infrastructure:
- `lifecycle.py` — phased startup manager
- `settings_service.py` — guild settings CRUD (channels, roles, feature flags)
- `db_helpers.py` — connection pool management; always use `acquire_safe` and parameterized queries
- `sanitizer.py` — sanitize user input before embedding in Discord messages (use `safe_embed_text()`)
- `premium_guard.py` — `is_premium()` with Core-API fallback, local cache, TTL; use `invalidate_premium_cache()` on webhook
- `logger.py` — centralized logging; use `from utils.logger import logger`, never `print()` for operational logging

**`gpt/`** — AI integration: `helpers.py` (Grok/OpenAI calls), `context_loader.py` (conversation context + user reflections), `dataset_loader.py` (learning data for `/learn_topic`)

**`webhooks/`** — inbound webhooks from Core-API (premium invalidation, reflections, founder DMs, Supabase events). All use HMAC via `X-Webhook-Signature`.

**`alembic/versions/`** — migration files. See `docs/migrations.md` for workflow.

### Multi-guild isolation

All data and configuration is scoped per guild (`guild_id`). `SettingsService` is the canonical way to read/write guild settings — do not bypass it with raw DB queries for config.

### Premium gating

Premium is per-user, applied to one guild. `utils/premium_guard.py` exposes `is_premium(user_id, guild_id)`. Some features are guild-level premium (`guild_has_premium`). Invalidation is webhook-driven from Core-API.

## Code conventions

- **Language**: All code, comments, docstrings, user-facing strings, and log messages must be in **English**. No Dutch anywhere, even if the user communicates in Dutch. Exception: intentional display labels like language names in onboarding ("Nederlands", "Español").
- **Logging**: `from utils.logger import logger`; use `log_with_guild` / `log_database_event` helpers where appropriate.
- **Embeds**: Follow embed styling from `AGENTS.md` (colors, timestamps, footers, field limits). Always pass user-supplied content through `safe_embed_text()`.
- **Database**: Use `acquire_safe` from `db_helpers.py`; parameterized queries only; no raw string interpolation in SQL.
- **Admin commands**: Use `validate_admin` / permission checks for any admin-only commands.
- **Branches**: `main` is the default branch (not `master`) — work on `feature/` or `fix/` branches and open PRs targeting `main`.

## Key reference docs

| Topic | Location |
|-------|----------|
| Agent manifest, embed style, DB overview | `AGENTS.md` |
| Slash command reference | `docs/commands.md` |
| Env vars and multi-guild config | `docs/configuration.md` |
| API endpoints | `docs/api.md` |
| Database schema | `docs/database-schema.md` |
| Migration workflow | `docs/migrations.md` |
| Security practices | `docs/SECURITY.md` |

When adding or changing commands, config, or API endpoints, update `AGENTS.md` and the relevant docs to keep them in sync.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **alphapy** (3541 symbols, 12543 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/alphapy/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/alphapy/context` | Codebase overview, check index freshness |
| `gitnexus://repo/alphapy/clusters` | All functional areas |
| `gitnexus://repo/alphapy/processes` | All execution flows |
| `gitnexus://repo/alphapy/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
