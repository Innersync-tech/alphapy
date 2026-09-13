# Contributing to Alphapy

Thanks for your interest in contributing. This document explains how to get set up and what we expect from contributions.

By participating in this project, you agree to uphold our [Code of Conduct](CODE_OF_CONDUCT.md).

## Codebase language

**All code and in-app text are in English.** Do not add Dutch (or other languages) in source code, comments, docstrings, user-facing strings, or log messages. See [AGENTS.md](AGENTS.md) for the full rule. Exceptions: intentional display labels (e.g. language names in onboarding) may be in another language.

## Branch and pull request workflow

- **`main` is protected.** Do not push directly to `main`.
- Work on a **feature or fix branch** (e.g. `feature/your-feature`, `fix/bug-name`).
- Open a **pull request** into `main` when ready.
- If your branch was already merged or deleted on the remote, create a new branch from an up-to-date `main` and move your changes there (e.g. cherry-pick or re-apply), then open a new PR.

## Setting up locally

```bash
git clone https://github.com/Innersync-tech/alphapy.git && cd alphapy
pip install -r requirements.txt
cp .env.example .env   # set BOT_TOKEN, DATABASE_URL, and any optional vars
alembic upgrade head   # or alembic stamp head if DB already has schema
```

- **Tests:** `pytest tests/ -v`
- **Config:** [docs/configuration.md](docs/configuration.md) for env vars and multi-guild setup.

## Code and documentation standards

- **AGENTS.md** – Main reference for agents, commands, embed styling, and database architecture. New cogs, commands, or behaviour should be reflected there where relevant.
- **Embeds** – Follow the Embed Styling Guide in AGENTS.md (colors, timestamps, footers, field limits). Use `safe_embed_text()` for user-supplied content in embeds.
- **Logging** – Use `from utils.logger import logger` (and project helpers like `log_with_guild`, `log_database_event` where appropriate). No `print()` for operational logging.
- **Database** – Use connection pools and `acquire_safe`; parameterized queries only. See [docs/database-schema.md](docs/database-schema.md) and [docs/migrations.md](docs/migrations.md) for schema and migrations.
- **Security** – No hardcoded secrets. Use `validate_admin` / permission checks for admin-only commands. Sanitize input where needed (see `utils/sanitizer`).

When you add or change commands, config, or API endpoints, update the relevant docs (e.g. [docs/commands.md](docs/commands.md), [docs/configuration.md](docs/configuration.md), [docs/api.md](docs/api.md)) and AGENTS.md so they stay in sync with the code.

## Submitting changes

1. Run tests: `pytest tests/ -v`.
2. Ensure new or changed behaviour is documented (AGENTS.md, docs, and optionally [changelog.md](changelog.md)).
3. Open a PR from your branch to `main` with a clear description of the change.
4. Address any review feedback.

## Where to look for more

| Topic | Location |
|-------|----------|
| Agents, commands, embed style, DB overview | [AGENTS.md](AGENTS.md) |
| Slash commands reference | [docs/commands.md](docs/commands.md) |
| Env vars and multi-guild config | [docs/configuration.md](docs/configuration.md) |
| API endpoints | [docs/api.md](docs/api.md) |
| Database schema and migrations | [docs/database-schema.md](docs/database-schema.md), [docs/migrations.md](docs/migrations.md) |
| Security and secrets | [docs/SECURITY.md](docs/SECURITY.md) |
| Operational runbook | [docs/OPERATIONAL_PLAYBOOK.md](docs/OPERATIONAL_PLAYBOOK.md) |

If you have questions, open an issue or discuss in a PR. You can also email **support@innersync.tech** if you prefer.
