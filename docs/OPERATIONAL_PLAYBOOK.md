---
title: Operational Playbook
description: Post-setup verification checklist for Alphapy in a new Discord server.
---

# Operational Playbook

Quick checklist and verification steps after adding the bot to a new server.

## Related docs

- **Multi-guild configuration** (required channels, feature config): [Configuration](../configuration/)
- **Reminders** (one-off vs recurring, embed watcher): [AGENTS.md (GitHub)](https://github.com/Innersync-tech/alphapy/blob/main/AGENTS.md) (EmbedReminderWatcher, ReminderManager)
- **Ticket system**: [AGENTS.md (GitHub)](https://github.com/Innersync-tech/alphapy/blob/main/AGENTS.md) and [Configuration](../configuration/)
- **Alphapy Agents**: [Alphapy agents architecture](../alphapy-agents-architecture/), [Agent safety guidelines](../agents-safety-guidelines/)

## Pre-flight checklist

- [ ] `DATABASE_URL` environment variable is set
- [ ] Bot has administrator permissions in the server
- [ ] All required channels exist and bot can read/send messages
- [ ] Bot can create channels and roles (for ticket system)

## Startup verification

After starting the bot, confirm in the logs:

- [ ] "DB pool created"
- [ ] "audit_logs table created" or "audit_logs table verified"
- [ ] "health_check_history table created" or "health_check_history table verified"
- [ ] "Command tracker: Database pool set"
- [ ] "Bot has successfully started and connected to X server(s)!"
- [ ] Guild enumeration with server names and IDs

## Testing functionality

### 1. Embed-driven reminder

- Post an embed in the announcements channel with date/time.
- Bot should detect it and schedule a reminder.
- Check `/config system show` to verify channel settings.

### 2. Manual reminder

- Use `/add_reminder`.
- Verify the reminder appears in the list and triggers at the correct time.
- Test `/reminder_edit` to modify it.

### 3. Import (removed)

Owner import slash commands (`/import_onboarding`, `/import_invites`) were removed in the ballast cut. Do not use them.

### 4. Recurring reminder

- Create a recurring reminder (days + time).
- It should send only on the matching weekday at the configured time and not be deleted afterward.

### 5. Idempotency

- Restart the bot within the same minute window of a scheduled send.
- Verify only one send occurs (duplicates prevented via `last_sent_at`).

### 6. Alphapy Agents (if enabled)

Requires `ALPHAPY_AGENTS_ENABLED=true` on the deployment and `/config agents toggle true` in the guild.

- [ ] `/link` completes for a test user
- [ ] `/agent list` shows `reflection`
- [ ] `/agent start message:Hello` returns ephemeral embed; session stays `active`
- [ ] `/agent continue message:Follow up` returns second turn; footer shows turn count
- [ ] `/agent end` completes session; row in `agent_sessions` has status `completed`
- [ ] Core `0023` applied: `agent_session_messages` empty after end (ephemeral purge)
- [ ] Optional: run Matrix A probes from [Agent safety guidelines](../agents-safety-guidelines/)

### 7. Phase 5A check-in DMs (if agents enabled)

Requires `/link`, `ALPHAPY_AGENTS_ENABLED=true`, guild `agents.enabled`, and Alembic head `027_agent_nudge_state`.

- [ ] Default is off: user without the pref does not receive DMs
- [ ] `/agent nudges enable` (or App Settings → Alphapy → Check-ins) persists `agent_prefs.agent_nudges_enabled: true`
- [ ] Linked user receives the **fixed English** invite DM within ~1h (or after a staging force tick) — **no journal / Grok text**
- [ ] Deploy logs show `Nudge tick: opted_in=… due=…` (not silent empty ticks); after send: `Agent nudge tick done: due=… sent=…`
- [ ] `/agent nudges disable` → no further DMs
- [ ] Closed DMs are skipped; the hourly loop does not crash
- [ ] GDPR / `/delete_my_data` clears Railway `agent_nudge_state` for that user

**Ops note (delivery):** opt-in listing uses jsonb contains `cs.{"agent_nudges_enabled":true}` (not `->>eq.true`). Membership uses cache then `fetch_member`. Guild gate uses `is_module_enabled(bot, guild_id, "agents")` on `bot.settings` (not reversed `get(guild_id, …)`). If `opted_in>0` but `skipped_guild` high, confirm `agents.enabled` on a mutual guild via `/config agents show`.

## Troubleshooting reminders

- **No sends?** Verify timezone is Brussels and system clock is correct.
- Check that `time` in the DB equals the intended trigger minute (HH:MM).
- Inspect `WATCHER_LOG_CHANNEL` for parsing or SQL errors.
- **Optional indexes** for performance:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders (time);
  CREATE INDEX IF NOT EXISTS idx_reminders_reminder_date ON reminders ((event_time - interval '60 minutes')::date);
  ```
