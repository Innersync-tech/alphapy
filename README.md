# Alphapy Discord Bot

Modular Discord bot for conscious communities: server tools, AI coaching, knowledge search, tickets, and reminders.

**Related:** [alphapy-dashboard](https://github.com/bryntje/alphapy-dashboard) — Next.js config UI and metrics.

---

## Overview

Alphapy powers the **Innersync • Alphapips** community with:

- **AI:** growth coaching (`/growthcheckin`), topic learning (`/learn_topic`), captions (`/create_caption`)
- **Tickets:** support channels with claim/close, Grok summaries, FAQ suggestions
- **Reminders:** one-off and recurring, including auto-detection from announcement embeds
- **Infra:** PostgreSQL (Supabase), Alembic migrations, FastAPI metrics, pytest

See [docs/commands.md](docs/commands.md) for the full command list.

---

## Quick start

```bash
git clone https://github.com/bryntje/alphapy.git && cd alphapy
pip install -r requirements.txt
cp .env.example .env   # edit: BOT_TOKEN, DATABASE_URL, optional GROK_API_KEY / Google vars
alembic upgrade head   # or: alembic stamp head (existing DB)
python bot.py
```

**Tests:** `pytest tests/ -v`

**Config:** [docs/configuration.md](docs/configuration.md) — env vars, multi-guild setup, feature config.

---

## Project structure

```
alphapy/
├── bot.py              # Entrypoint
├── api.py              # FastAPI (health, metrics, reminders API)
├── config.py
├── cogs/               # Slash commands & features (growth, learn, ticketbot, reminders, …)
├── utils/              # DB, metrics, timezone, drive_sync, …
├── gpt/                # Grok/LLM helpers, dataset_loader (learn_topic)
├── tests/
├── alembic/            # Migrations
├── data/prompts/       # Local knowledge (.md)
└── docs/               # Configuration, API, migrations, security, playbook
```

---

## Configuration

| Required | Optional |
|----------|----------|
| `BOT_TOKEN` | `GROK_API_KEY` / `OPENAI_API_KEY` |
| `DATABASE_URL` | `GOOGLE_CREDENTIALS_JSON` (Drive, optional) |
| | `API_KEY` (API auth), Supabase vars, ticket/reminder config |

Full list and multi-guild setup: [docs/configuration.md](docs/configuration.md).  
Google Drive credentials: [docs/GOOGLE_CREDENTIALS_SETUP.md](docs/GOOGLE_CREDENTIALS_SETUP.md), [docs/SECURITY.md](docs/SECURITY.md).

---

## Features (summary)

### System & Information

- **Status commands:** `/innersync` (platform info), `/version`, `/health`, `/gptstatus`, `/release`
- **Command listing:** `/commands` with categorized display and admin options

### Reminders

- **One-off:** from embeds (event time) or `/add_reminder`; trigger at T−60 and T0; deleted after send.
- **Recurring:** by weekday + time; not deleted. Idempotency via `last_sent_at`.
- Logs to `WATCHER_LOG_CHANNEL`. Details: [AGENTS.md](AGENTS.md) (EmbedReminderWatcher, ReminderManager).

### Ticket system

- Channels under a ticket category; buttons: **Claim**, **Close** (Grok summary), **Delete** (after close).
- FAQ suggestions from similar closed tickets; staff role and escalation config.
- [AGENTS.md](AGENTS.md) and [docs/configuration.md](docs/configuration.md).

### API

- Health: `GET /api/health`, `GET /api/health/history`
- Metrics: `GET /api/dashboard/metrics`, `GET /top-commands`
- Reminders CRUD: `GET/POST/PUT/DELETE /api/reminders` (API key + `X-User-Id`)

Full reference: [docs/api.md](docs/api.md).

### Migrations & analytics

- **Migrations:** Alembic; Discord commands `/migrate`, `/migrate_status`. [docs/migrations.md](docs/migrations.md).
- **Analytics:** Commands in `audit_logs`; telemetry to Supabase for dashboard.

---

## Architecture

```
┌─────────────────┐         ┌──────────────────┐
│   alphapy       │         │ alphapy-dashboard│
│   (Discord Bot) │ ◄─────► │  (Next.js Web)   │
│ Commands, AI,   │         │ Config, metrics, │
│ Tickets, etc.   │         │ charts, API proxy│
└────────┬────────┘         └────────┬─────────┘
         └────────── Supabase ───────┘
```

---

## After adding the bot to a new server

1. **Configure channels and features:** [docs/configuration.md](docs/configuration.md) (multi-guild setup).
2. **Checklist and tests:** [docs/OPERATIONAL_PLAYBOOK.md](docs/OPERATIONAL_PLAYBOOK.md).

---

## Contributing

- Fork, create a branch (`feature/…` or `fix/…`), commit, push, open a PR.
- Keep the modular structure and test coverage in mind.

---

## License & contact

- **License:** [Apache-2.0](LICENSE)
- **Legal:** [Terms of Service](docs/terms-of-service.md), [Privacy Policy](docs/privacy-policy.md)
- **Contact:** `support@innersync.tech` or open an issue.
