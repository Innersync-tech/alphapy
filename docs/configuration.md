---
title: Configuration
description: Multi-guild setup and settings for Alphapy.
---

# Multi-Guild Configuration Guide

This document explains how to configure the Innersync • Alphapy bot for each Discord server (guild) where it's added.

## Important: Multi-Guild Architecture

**The bot is completely multi-guild capable.** Each server must be configured independently - there are no automatic fallbacks to hardcoded values. Every channel, role, and setting must be explicitly configured per server.

## First-Time Setup (Required for each new server)

**Quick start:** Use **`/config start`** for an interactive setup. The bot will ask for each important setting (log channel, rules and onboarding channel, embed watcher, invites, GDPR, ticket category, staff role); you choose a channel or role from the dropdown or click **Skip**. All prompts are in English.

Alternatively, run the following commands in order:

### 1. System Configuration
```bash
/system set_log_channel #logs
/system set_rules_channel #rules
```

### 2. Feature-Specific Configuration

#### Embed Watcher (Auto-Reminders)
```bash
/embedwatcher set_announcements #announcements
```

#### Invite Tracker
```bash
/invites set_channel #invites
```

#### GDPR Compliance
```bash
/gdpr set_channel #gdpr
```

#### Ticket System
```bash
/ticketbot set_category [category]
/ticketbot set_staff_role @Staff
/ticketbot set_escalation_role @Moderators
```

#### Verification (AI-assisted payment checks)
```bash
/verification set_category [category]
/verification set_verified_role @Verified

# Optional: override vision model
/verification set_vision_model grok-4

# Optional: tell the AI what valid payments look like
/verification set_ai_prompt_context "Valid payments come from Stripe for the membership."
```

### 3. Optional Settings

#### Reminders
```bash
/reminders set_everyone true
/reminders set_default_channel #general
```

#### Grok / AI Settings
```bash
/gpt set_model grok-3
/gpt set_temperature 0.7
```

#### Engagement
```bash
# Enable the features you want
/engagement toggle challenges true
/engagement toggle weekly true
/engagement set_weekly_channel #awards
/engagement set_challenge_winner_role @Champion
```

## Configuration Commands Reference

> Each feature area has its own top-level command group. All commands require Administrator and are guild-specific.

```
/config scopes    — List all registered setting scopes
/config start     — Interactive setup wizard
/<scope> show     — Show current settings for that scope
```

### System — `/system`
```
/system show
/system set_log_channel [#channel]
/system set_rules_channel [#channel]
/system set_log_level [verbose|normal|critical]
```

### Embed Watcher — `/embedwatcher`
```
/embedwatcher show
/embedwatcher set_announcements [#channel]
/embedwatcher set_offset [minutes]
/embedwatcher set_non_embed [true|false]
/embedwatcher set_process_bot_messages [true|false]
```

### Reminders — `/reminders`
```
/reminders show
/reminders toggle <true|false>
/reminders set_default_channel [#channel]
/reminders set_everyone <true|false>
```

### TicketBot — `/ticketbot`
```
/ticketbot show
/ticketbot set_category [#category]
/ticketbot set_staff_role [@role]
/ticketbot set_escalation_role [@role]
```

### Grok / AI — `/gpt`
```
/gpt show
/gpt set_model [model-name]
/gpt set_temperature [0.0–2.0]
```

### Invites — `/invites`
```
/invites show
/invites toggle <true|false>
/invites set_channel [#channel]
/invites set_template <variant> [template]
```
Variant is `with inviter` or `without inviter` (dropdown).

### GDPR — `/gdpr`
```
/gdpr show
/gdpr toggle <true|false>
/gdpr set_channel [#channel]
/gdpr set_acceptance_role [@role]
/gdpr post
```

### Growth — `/growth`
```
/growth set_channel [#channel]   — omit to clear; share option disappears from /growthcheckin
```

### Onboarding — `/onboarding`
```
/onboarding show
/onboarding toggle <true|false>
/onboarding set_mode <mode>
/onboarding add_question <step> <question> [type] [required]
/onboarding delete_question <step>
/onboarding reset_questions
/onboarding add_rule <order> <title> <description> [thumbnail_url] [image_url]
/onboarding delete_rule <order>
/onboarding reset_rules
/onboarding set_role [@role]
/onboarding set_join_role [@role]
/onboarding panel_post [#channel]
/onboarding reorder
```

**Onboarding modes:**
- **Disabled** — No onboarding
- **Rules Only** — Role assigned when rules are accepted
- **Rules + Questions** — Role assigned after all questions and personalization steps
- **Questions Only** — Role assigned after all questions and personalization steps

When questions are used, users also complete two fixed personalization steps: opt-in for personalized reminders and preferred language (`personalized_opt_in`, `preferred_language` in `onboarding.responses`).

Rules support optional images: `thumbnail_url` (shown right) and `image_url` (shown at bottom).

### FYI — `/fyi`
```
/fyi reset <key>   — Clear the flag so the next trigger will resend the tip
/fyi send <key>    — Force-send the tip to the log channel now
```
Key chosen from a dropdown (e.g. `first_onboarding_done`, `first_guild_join`).

### Verification — `/verification`
```
/verification show
/verification set_verified_role [@role]
/verification set_category [#category]
/verification set_vision_model [model]
/verification set_ai_prompt_context [text]
/verification set_reviewer_role [@role]
/verification set_max_payment_age [days]
/verification set_reference_image <image attachment>
/verification reset_reference_image
```

**Notes:**
- `set_vision_model` — specific vision-capable model (e.g. `grok-4`); defaults to `LLM_PROVIDER` model if unset.
- `set_ai_prompt_context` — extra context appended to every AI screenshot review (max 1000 chars). Describe what a valid payment looks like for your community.
- `set_reference_image` — the bot stores the image in the log channel for URL persistence across restarts. The AI compares user screenshots against it. Clear with `reset_reference_image`.

### Auto-moderation — `/automod`
```
/automod status
/automod show
/automod toggle <true|false>
/automod set_log_channel [#channel]
/automod rules
/automod add_spam_rule <name> [max_messages] [window_seconds] [action]
/automod add_badwords_rule <name> <words> [action]
/automod add_links_rule <name> [allow_links] [whitelist] [blacklist] [action]
/automod add_mentions_rule <name> [max_mentions] [action]
/automod add_caps_rule <name> [min_length] [max_ratio] [action]
/automod add_duplicate_rule <name> [max_duplicates] [action]
/automod add_regex_rule <name> <patterns> [action]    — premium
/automod add_ai_rule <name> <policy> [action] [threshold]  — premium (policy required; threshold 0.5–1.0, default 0.7)
/automod edit_rule <rule_id> [fields...]
/automod delete_rule <rule_id>
/automod set_rule_enabled <rule_id> <true|false>
/automod set_severity <rule_id> <1–10>
/automod logs [limit] [user_id] [rule_id] [action] [days]
```

### Alphapy agents — `/config agents`

Requires `ALPHAPY_AGENTS_ENABLED=true` on the deployment (Railway env). Per-guild toggle:

```
/config agents show
/config agents toggle <true|false>
```

When enabled, linked members can use `/agent list`, `/agent start`, `/agent continue`, `/agent end`, and `/agent status`.

Optional env vars:

```
ALPHAPY_AGENTS_MEMORY_BACKEND=supabase   # default; uses Supabase agent_sessions + agent_memory
ALPHAPY_AGENTS_MEMORY_BACKEND=memory     # in-process only (local dev)
```

### Engagement — `/engagement`
```
/engagement show
/engagement toggle <feature> <true|false>
/engagement set_challenge_winner_role [@role]
/engagement set_weekly_channel [#channel]
/engagement set_food_channels [ids]
/engagement set_weekly_awards <json>
/engagement set_badge_role <badge_key> [@role]
/engagement set_og_cap <number>
/engagement set_og_text [text]
/engagement set_streaks_nicknames <true|false>
```

Features for `toggle`: `challenges`, `weekly`, `badges`, `streaks`, `og_claims`.

Badge keys for `set_badge_role`: `winner`, `og`, `motivator`, `foodfluencer`, `sharpshooter`, `star`.

`set_weekly_awards` accepts a JSON list of award category objects:
```json
[
  {"key": "motivator", "label": "📣 Motivator", "subtitle": "Most non-food messages", "filter": "non_food"},
  {"key": "star",      "label": "⭐ Star",       "subtitle": "Most reactions on a photo", "filter": "reactions"}
]
```
Filters: `non_food`, `food`, `image`, `reactions`. Defaults to 4 awards (Motivator, Foodfluencer, Sharpshooter, Star) if not configured (Foodfluencer only included when food channels are set).

## Template Placeholders

For invite templates, you can use:
- `{member}` - Mention the new member
- `{member_name}` - Name of the new member
- `{inviter}` - Mention the inviter
- `{inviter_name}` - Name of the inviter
- `{count}` - Total invite count

Example:
```
/invites set_template "with" "{member} joined! {inviter} now has {count} invites."
```

## Troubleshooting

### "Channel not configured" errors
- Run the setup commands above for your server
- Check `/system show` to verify settings

### Bot not responding to embeds/announcements
- Verify `/config embedwatcher set_announcements` is set to the correct channel
- Ensure bot has read permissions in that channel

### Import commands failing
- Set appropriate channels first (log channel for imports)
- Owner permissions required for import commands

## Environment Variables

The following environment variables are required/optional for bot operation:

### Required
- `BOT_TOKEN`: Discord bot token
- `DATABASE_URL`: PostgreSQL connection string

### Optional - Local testing (separate dev bot)
- `BOT_TOKEN_TEST`: Discord token for a separate test/dev bot. Used only when `USE_TEST_BOT=1`.
- `USE_TEST_BOT`: Set to `1` (or any non-empty value) to run the bot with `BOT_TOKEN_TEST` instead of `BOT_TOKEN`. Use this for local testing without touching the production bot.

### Optional - Google Cloud (for Drive integration)
- `GOOGLE_CREDENTIALS_JSON`: Google service account key as a JSON string (Railway env var or `.env`). Required for Drive PDF features (`/learn_topic`, etc.). See [Google credentials setup](../google_credentials_setup/).

### Optional - API & Authentication
- `API_KEY`: Internal API key for API endpoints
- `SUPABASE_URL`: Supabase project URL
- `SUPABASE_ANON_KEY`: Supabase anonymous key
- `SUPABASE_SERVICE_ROLE_KEY`: Supabase service role key
- `APP_ENV`: Runtime environment (`development` or `production`)
- `STRICT_SECURITY_MODE`: Set to `1` to enforce production security requirements at startup (fails fast when critical auth/webhook secrets are missing in production)
- `APP_ENV`: Runtime environment (`development` by default). Use `production` on production deployments.
- `STRICT_SECURITY_MODE`: Set to `1` to enforce production hardening checks at startup (`APP_ENV=production` required). Startup fails when auth and webhook secret requirements are not met.
- `ALLOWED_ORIGINS`: Comma-separated CORS origins. If omitted, defaults to trusted application origins from config.

### Optional - AI/LLM
- `GROK_API_KEY`: Grok API key (or `OPENAI_API_KEY` for OpenAI)
- `LLM_PROVIDER`: "grok" or "openai" (default: "grok")

### Optional - Core API (telemetry / operational events)
- `CORE_API_URL`: Base URL of the Core API for centralised telemetry and operational event ingress. When set, operational events and telemetry are sent to Core instead of directly to Supabase.
- `ALPHAPY_SERVICE_KEY`: Service key for authenticating with the Core API (`X-API-Key`), including premium verify, Discord link session, and bot profile endpoints.
- `CORE_DISCORD_LINK_SESSION_PATH`: Optional override for the POST path used to start a Discord link session (default: `/integrations/discord/link-session`).
- `CORE_DISCORD_BOT_PROFILE_PATH`: Optional override for the GET path used to fetch a profile for a Discord user (default: `/integrations/discord/bot-profile`).
- `HERMIT_CONTEXT_ENABLED`: Enable Core-mediated Hermit strategic context injection in GPT system prompts (`false` by default; recommended to enable only for pilot users first).
- `HERMIT_CONTEXT_TTL_SECONDS`: In-memory cache TTL for Hermit context fetches (default: `1800`).
- `HERMIT_CONTEXT_TIMEOUT_SECONDS`: Request timeout (seconds) for Core Hermit context calls (default: `2.0`).
- `CORE_HERMIT_CONTEXT_PATH`: Optional override for the Core endpoint path (default: `/integrations/hermit/strategic-context`).
- `HERMIT_STRATEGY_PACKET_ENABLED`: When `true`, inject structured `strategy_packet` JSON from Core into GPT system prompts (pilot; requires Core + Hermit push).
- `HERMIT_EVENTS_ENABLED`: When `true` (default), emit lightweight events to Core `POST /integrations/hermit/events` (e.g. after `/growthcheckin`, Discord link complete).
- `HERMIT_EVENTS_WEBHOOK_SECRET`: Optional HMAC secret for event ingest (defaults to shared webhook secret if unset).
- `ALPHAPY_AGENTS_ENABLED`: Enable multi-user `/agent` commands (`false` by default).
- `ALPHAPY_AGENTS_MEMORY_BACKEND`: `supabase` (default, persistent sessions via Supabase) or `memory` (in-process, dev/tests).

### Optional - Innersync identity (Discord link webhook)
- `DISCORD_LINK_WEBHOOK_SECRET`: Secret for HMAC validation of `POST /webhooks/discord-link`. Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET` / `SUPABASE_WEBHOOK_SECRET`.

### Optional - Premium tier
- `PREMIUM_CHECKOUT_URL`: Checkout page URL for the "Get Premium" button in `/premium`. If unset, buttons are disabled.
- `PREMIUM_CACHE_TTL_SECONDS`: TTL in seconds for the in-memory premium cache (default: 300). See [Premium](../premium/) for guard behaviour and Core-API contract.
- `CORE_API_PAYMENTS_TOKEN`: Value of `INNERSYNC_CORE_PAYMENTS_TOKEN` from Core-API. Required for early bird availability checks (`POST /billing/early-bird/validate`). If unset, the embed assumes early bird is available (fail-open).
- `EARLY_BIRD_CODE`: Early bird redemption code to validate against (default: `EARLYBIRD50`).
- `EARLY_BIRD_TOTAL_SPOTS`: Total early bird spots shown in the embed text (default: `50`).
- `PRICE_MONTHLY`: Monthly plan price label in the embed (default: `€4.99`).
- `PRICE_YEARLY_EARLY_BIRD`: Annual plan early bird price label (default: `€29`).
- `PRICE_YEARLY_REGULAR`: Annual plan regular price label, shown after early bird sells out (default: `€59.99`).
- `PRICE_LIFETIME_EARLY_BIRD`: Lifetime plan early bird price label (default: `€49`).
- `PRICE_LIFETIME_REGULAR`: Lifetime plan regular price label, shown after early bird sells out (default: `€99.99`).

### Optional - App reflections (Core-API webhooks)
- `APP_REFLECTIONS_WEBHOOK_SECRET`: Secret for HMAC validation of `POST /webhooks/app-reflections` and `POST /webhooks/revoke-reflection`. If unset, falls back to `WEBHOOK_SECRET` or `SUPABASE_WEBHOOK_SECRET`.

### Optional - Premium / Founder webhooks (Core → Alphapy)
- `PREMIUM_INVALIDATE_WEBHOOK_SECRET`: Secret for `POST /webhooks/premium-invalidate` (cache invalidation on subscription change). Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`.
- `FOUNDER_WEBHOOK_SECRET`: Secret for `POST /webhooks/founder` (founder welcome DM). Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`.

### Optional - Legal update notifications
- `LEGAL_UPDATE_WEBHOOK_SECRET`: Secret for `POST /webhooks/legal-update` (GitHub Action notifies on PP/ToS change). Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`.
- `LEGAL_UPDATES_CHANNEL_ID`: Channel ID in the main guild where legal update embeds are posted. Falls back to `system.log_channel_id` for `MAIN_GUILD_ID` if unset.

### Optional - API observability and idempotency
- `GET /api/observability` (internal endpoint) returns rolling request success-rate and p50/p95/p99 latency for API and webhook traffic.
- Write endpoints under `/api/reminders` support `Idempotency-Key` header to prevent duplicate writes during client retries (cached for 10 minutes).

### Optional - GitHub
- `GITHUB_TOKEN`: Optional token for GitHub API (e.g. `/release`, repo links when `GITHUB_REPO` is set) to avoid rate limits.

## Migration Notes

If upgrading from single-guild to multi-guild:
1. All old config.py values are now deprecated
2. Each server needs fresh `/config` setup
3. No automatic migration of old settings
4. Bot automatically detects all joined servers