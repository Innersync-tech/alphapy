---
title: Database Migrations
description: Alembic migration workflow for Alphapy.
---

# Database Migrations Guide

This project uses [Alembic](https://alembic.sqlalchemy.org/) for database schema migrations.

## Overview

Alembic provides a way to version control database schema changes, making it easier to:
- Track schema evolution over time
- Apply migrations consistently across environments
- Rollback changes if needed
- Collaborate on schema changes

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   
   This will install Alembic and SQLAlchemy (required for Alembic).

2. Configure database URL:
   The migration system uses `DATABASE_URL` from `config.py` (loaded from environment variables).
   
   Ensure your `.env` file contains:
   ```
   DATABASE_URL=postgresql://user:password@host:port/database
   ```

3. **Important for existing databases:**
   
   If your database already has tables (production), you need to mark the baseline migration as applied:
   ```bash
   alembic stamp head
   ```
   
   This tells Alembic that the current database state matches the baseline migration, without actually running it.
   
   If your database is empty, you can run:
   ```bash
   alembic upgrade head
   ```

## Running Migrations

### Check Migration Status

```bash
alembic current
```

Shows the current revision of the database.

```bash
alembic history
```

Shows all available migrations and their revision chain.

### Apply Migrations

```bash
alembic upgrade head
```

Applies all pending migrations up to the latest version.

```bash
alembic upgrade +1
```

Applies the next migration only.

### Rollback Migrations

```bash
alembic downgrade -1
```

Rolls back the last migration.

```bash
alembic downgrade base
```

Rolls back all migrations (⚠️ **DESTRUCTIVE** - use with caution).

## Creating New Migrations

### Auto-generate Migration

```bash
alembic revision --autogenerate -m "Description of changes"
```

This will analyze the current database state and generate a migration file. **Always review the generated migration before applying it.**

### Manual Migration

```bash
alembic revision -m "Description of changes"
```

Creates an empty migration file that you can fill in manually.

## Migration Files

Migrations are stored in `alembic/versions/` with the format:
- `001_initial_schema.py` - Baseline migration
- `002_add_feature_x.py` - Feature-specific migrations
- etc.

Each migration file contains:
- `revision`: Unique identifier for this migration
- `down_revision`: The previous migration (forms a chain)
- `upgrade()`: Function that applies the migration
- `downgrade()`: Function that rolls back the migration

## Best Practices

1. **Always test migrations** on a development/staging database first
2. **Review auto-generated migrations** - Alembic may not detect all changes correctly
3. **Keep migrations small** - One logical change per migration
4. **Never edit applied migrations** - Create a new migration to fix issues
5. **Backup before major migrations** - Especially when dropping tables or columns
6. **Use transactions** - Alembic wraps migrations in transactions by default

## Integration with Bot

The bot can check migration status on startup. To enable automatic migration checks:

1. Add migration check to `bot.py` startup hook
2. Use `/migrate status` command to check status manually
3. Use `/migrate` command (admin only) to apply migrations

## Troubleshooting

### Migration conflicts

If migrations are out of sync:
```bash
# Check current state
alembic current

# See what's pending
alembic heads

# If needed, mark current state manually
alembic stamp head
```

### Database connection issues

Ensure `DATABASE_URL` is set correctly:
```bash
export DATABASE_URL="postgresql://user:pass@localhost/dbname"
```

### Migration fails mid-way

If a migration fails partway through:
1. Check the error message
2. Fix the migration file if needed
3. Manually fix the database state if necessary
4. Use `alembic stamp` to mark the correct revision

## Migration Workflow

1. **Development**: Create migration locally
   ```bash
   alembic revision --autogenerate -m "Add new feature"
   ```

2. **Review**: Check the generated migration file

3. **Test**: Apply migration on dev database
   ```bash
   alembic upgrade head
   ```

4. **Commit**: Add migration file to git

5. **Deploy**: Apply migrations on production
   ```bash
   alembic upgrade head
   ```

## Current Schema

**Current migration head:** `024_agent_session_usage`

Tables added across all migrations:

| Migration | Tables / Changes |
|---|---|
| `001_initial_schema` | `bot_settings`, `settings_history`, `reminders`, `onboarding`, `guild_onboarding_questions`, `guild_rules`, `support_tickets`, `faq_entries`, `faq_search_logs`, `audit_logs`, `health_check_history` |
| `002` | `guild_rules` image columns |
| `003` | `premium_subs` |
| `004` | `reminders.image_url` |
| `005` | Premium one-active-per-user constraint |
| `006` | `terms_acceptance` |
| `007` | Premium RLS policies |
| `008` | `app_reflections` |
| `009` | `automod_rules`, `automod_actions`, `automod_logs`, `automod_stats`, `automod_user_history` |
| `010` | `custom_commands` |
| `011` | `reminders.sent_message_id` |
| `012` | Guild ID indexes |
| `013` | Cleanup stale `bot_settings` rows (embedwatcher, guild.module_status, module_status.gdpr, system.onboarding_channel_id) |
| `014` | `gpt_usage` |
| `015` | `premium_subs.expiry_warning_sent_at` |
| `016` | `gdpr_acceptance`, `config_audit_log` |
| `017` | `verification_tickets.payment_date` |
| `018` | `gdpr_acceptance.guild_id` |
| `019` | `growth_checkins` |
| `020_engagement_system` | `engagement_badges`, `engagement_og_claims`, `engagement_og_setup`, `engagement_challenges`, `engagement_participants`, `engagement_weekly_messages`, `engagement_weekly_awards`, `engagement_weekly_results`, `engagement_streaks` |
| `021_cleanup_module_status` | Removes all remaining `module_status.*` rows from `bot_settings` (scope fully obsolete) |
| `022_api_observability_tables` | Creates/ensures `audit_logs` and `health_check_history` + indexes; adds `idx_reminders_event_time` for scheduler/filter performance. Also aligns startup so schema creation is migration-driven (no runtime DDL in API lifespan). |
| `023_alphapy_discord_links` | Adds `alphapy_discord_links` table for Innersync UUID ↔ Discord snowflake mapping used by `/link`, API reminder resolution, and Discord identity webhooks. |
| `024_agent_session_usage` | Adds `agent_session_usage` for per-user daily `/agent start` quota (tier-based limits in `utils/premium_tiers.py`). |

## References

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
