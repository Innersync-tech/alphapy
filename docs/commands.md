---
title: Command Reference
description: Discord slash commands for the Alphapy bot.
---

# Command Reference

Complete reference for all Discord slash commands available in the Alphapy bot.

## Command Categories

- [Core Utilities](#core-utilities)
- [Innersync identity](#innersync-identity)
- [Reminders](#reminders)
- [Tickets](#tickets)
- [Verification](#verification)
- [Engagement](#engagement)
- [Configuration](#configuration)
- [AI Features](#ai-features)
- [Alphapy Agents](#alphapy-agents)
- [FAQ](#faq)
- [System](#system)
- [Admin](#admin)

---

## Core Utilities

### `/sendto`
Send a message to a specific channel with support for newlines.

**Parameters:**
- `channel` (required): The channel where the message should be sent
- `message` (required): The message to send. Use `\n` for a new line.

**Example:**
```
/sendto channel:#general message:"Hello\ncommunity!"
```

**Permissions:** Owner/Admin

---

### `/embed`
Create and send a simple embed to a channel.

**Parameters:**
- `channel` (required): The channel where the embed should be sent

**Behavior:** Opens a modal to create the embed with title, description, footer, and color fields.

**Permissions:** Owner/Admin

---

### `/clean`
Delete messages from a channel.

**Parameters:**
- `limit` (required): Number of messages to delete (max 100)

**Permissions:** Owner/Admin

---

## Innersync identity

Link your Discord account to your central Innersync (Supabase Auth) user id so Mind, App, and Alphapy share the same identity.

### `/link`

Starts a link session via Core API. You receive an ephemeral message with a browser URL; after you complete the flow in the App, Core calls Alphapy and you get a confirmation DM.

**Rate limit:** Up to 3 uses per minute per user.

**Permissions:** Everyone (ephemeral)

### `/unlink`

Removes the stored mapping in Alphapy for your Discord account. You can run `/link` again later.

**Permissions:** Everyone (ephemeral)

### `/profile`

Shows central profile fields from Core when available (`display_name`, `avatar_url`, Innersync user id). If Core does not return data, shows your Discord display name and whether an Alphapy/Supabase link exists.

**Permissions:** Everyone (ephemeral)

---

## Reminders

### `/add_reminder`
Schedule a recurring or one-off reminder via form or message link.

**Parameters:**
- `name` (required): Name of the reminder
- `channel` (optional): Channel where reminder should be sent (uses default if not set)
- `time` (required): Time in HH:MM format
- `days` (optional): Days of the week (e.g., "Monday, Wednesday, Friday")
- `message` (optional): Reminder message content
- `location` (optional): Event location
- `message_link` (optional): Link to a message/embed to parse details from

**Examples:**
```
/add_reminder name:"Weekly Standup" time:"09:00" days:"Monday, Wednesday, Friday" message:"Daily standup meeting"
/add_reminder name:"Event" time:"19:30" message_link:"https://discord.com/channels/..."
```

### `/add_live_session`
Create a recurring "live session" reminder with a fixed message ("Live session starting now!"). Optional image (Premium required for images).

**Parameters:**
- `days` (required): Days of the week (e.g. "mon,wed,fri")
- `time` (required): Time in HH:MM format
- `channel` (optional): Channel for the reminder (uses default if not set)
- `image_url` (optional): Image URL for the reminder (Premium)
- `image` (optional): Image attachment (Premium; same rate limit as image reminders)

---

### `/edit_live_session`
Edit an existing live-session preset reminder.

**Parameters:**
- `live_session_id` (required): ID of the live session reminder
- `days` (optional): Days of the week (e.g. "mon,wed,fri")
- `time` (optional): Session start time in HH:MM format
- `channel` (optional): Target channel
- `image_url` (optional): New image URL (Premium)
- `image` (optional): New image attachment (Premium)
- `clear_image` (optional): Set to `true` to remove the current image

**Behavior:** Keeps live-session preset defaults (`name` + fixed message), updates only the requested fields.

---

### `/delete_live_session`
Delete an existing live-session preset reminder.

**Parameters:**
- `live_session_id` (required): ID of the live session reminder

**Permissions:** Owner/Admin or live session creator

---

### `/reminder_list`
View your active reminders.

**Parameters:**
- `user` (optional): View reminders for a specific user (admin only)

**Response:** Lists all active reminders with ID, name, time, days, and channel.

---

### `/reminder_edit`
Edit an existing reminder.

**Parameters:**
- `reminder_id` (required): ID of the reminder to edit

**Behavior:** Opens a modal with pre-filled fields (name, time, days, message, channel_id) that can be edited.

**Permissions:** Owner/Admin or reminder creator

---

### `/reminder_delete`
Delete a reminder by ID.

**Parameters:**
- `reminder_id` (required): ID of the reminder to delete

**Permissions:** Owner/Admin or reminder creator

---

## Tickets

### `/ticket`
Create a support ticket (private channel per ticket).

**Parameters:**
- `description` (required): Short description of your issue

**Behavior:** Creates a private channel under the configured ticket category with restricted access (requester + staff role). In the ticket channel, staff can use **Claim** and **Close** buttons (not slash commands) to manage the ticket.

---

### `/ticket_panel_post`
Post a persistent "Create ticket" panel (admins only).

**Behavior:** Posts an embed with a "Create ticket" button that users can click to create tickets.

**Permissions:** Owner/Admin

---

### `/ticket_stats`
Show ticket statistics (admins only).

**Behavior:** Shows interactive buttons to view stats for 7 days, 30 days, or all time.

**Permissions:** Owner/Admin

---

### `/ticket_status`
Update a ticket status (admins only).

**Parameters:**
- `ticket_id` (required): ID of the ticket
- `status` (required): New status (open, claimed, waiting_for_user, escalated, closed, archived)

**Permissions:** Owner/Admin

---

## Engagement

Community gamification features. Each feature is independently enabled per guild via `/engagement toggle`. All features are **off by default**.

### Challenges

#### `/challenge start`
Start a timed message-count contest in a channel.

**Parameters:**
- `duration` (optional): e.g. `10d`, `3h30m`, `900` (seconds) — default 24h
- `mode` (optional): `leaderboard` (most messages wins) or `random` (random draw) — default leaderboard
- `title` (optional): Display title for the challenge
- `channel` (optional): Channel to count messages in — defaults to current channel

**Permissions:** Manage Server

---

#### `/challenge end`
End the active challenge immediately and announce the winner.

**Parameters:**
- `challenge_id` (optional): Specific challenge ID (defaults to most recent)

**Permissions:** Manage Server

---

#### `/challenge cancel`
Cancel the active challenge without determining a winner.

**Parameters:**
- `challenge_id` (optional): Specific challenge ID

**Permissions:** Manage Server

---

#### `/challenge status`
Show remaining time, participant count, progress bar and top 5 leaderboard.

---

#### `/challenge edit`
Edit an active challenge (mode, duration, title, participants, message counts).

**Parameters:**
- `field` (required): `mode` / `duration` / `title` / `add_participant` / `remove_participant` / `set_count`
- `mode` (optional): New mode
- `duration` (optional): New duration
- `member` (optional): Member to add/remove or set count for
- `set_count` (optional): New message count (leaderboard only)
- `title` (optional): New title
- `challenge_id` (optional): Specific challenge ID

**Permissions:** Manage Server

---

### Badges

#### `/badge give`
Grant a badge (and optional linked role) to a member.

**Parameters:**
- `member` (required): Member to award
- `badge_key` (required): Badge key e.g. `winner`, `og`, `motivator`

**Permissions:** Manage Roles

---

#### `/badge list`
List all badges a member has earned in this server.

**Parameters:**
- `member` (optional): Member to look up — defaults to yourself

---

### OG Claims

#### `/og setup`
Post the OG claim message in a channel. Members react with ⚜ to claim their spot.

**Parameters:**
- `channel` (optional): Channel to post in — defaults to current channel

**Permissions:** Manage Server

---

#### `/og status`
Show current OG claim count and remaining spots.

---

### Weekly Awards

#### `/weekly compute`
Manually compute and announce weekly awards for the configured award channel.

**Permissions:** Manage Server

---

### Engagement Configuration

> All `/engagement` commands require Administrator.

- `/engagement show` — Show all engagement settings for this server
- `/engagement toggle <feature> <true|false>` — Enable/disable a feature (`challenges`, `weekly`, `badges`, `streaks`, `og_claims`)
- `/engagement set_challenge_winner_role [@role]` — Role assigned to challenge winners; leave empty to clear
- `/engagement set_weekly_channel [#channel]` — Channel for weekly award announcements; leave empty to clear
- `/engagement set_food_channels [ids]` — Comma-separated channel IDs counted as food channels for weekly awards
- `/engagement set_weekly_awards <json>` — Configure award categories as JSON (key, label, subtitle, filter: `non_food`/`food`/`image`/`reactions`)
- `/engagement set_badge_role <badge_key> [@role]` — Link a Discord role to a badge key; leave empty to clear
- `/engagement set_og_cap <number>` — Maximum OG claim spots (default: 50)
- `/engagement set_og_text [text]` — Message text for the OG claim post; leave empty to reset to default
- `/engagement set_streaks_nicknames <true|false>` — Toggle nickname suffixes for streaks (`Name | 🔥 week 2`)

---

## Configuration

### `/config`

- **`/config start`** — Interactive server setup wizard. Guides you step-by-step through the main settings (log channel, rules channel, onboarding, embed watcher, invites, GDPR, ticket category, staff role). Choose from a dropdown or click **Skip**.
- **`/config scopes`** — List all registered setting scopes.

> Each feature area has its own top-level command group (e.g. `/automod`, `/onboarding`, `/verification`). All require Administrator.

---

### System — `/system`
- `/system show` — Show current system settings
- `/system set_log_channel [#channel]` — Set log channel; leave empty to reset
- `/system set_rules_channel [#channel]` — Set rules/onboarding channel; leave empty to reset
- `/system set_log_level [level]` — Set log verbosity (`verbose`/`normal`/`critical`)

---

### Embed Watcher — `/embedwatcher`
- `/embedwatcher show` — Show current settings
- `/embedwatcher set_announcements [#channel]` — Channel to monitor for auto-reminder embeds
- `/embedwatcher set_offset [minutes]` — Reminder offset before event (0–4320 min)
- `/embedwatcher set_non_embed [true|false]` — Enable parsing of plain-text messages
- `/embedwatcher set_process_bot_messages [true|false]` — Process embeds sent by the bot itself

---

### Reminders — `/reminders`
- `/reminders show` — Show current settings
- `/reminders toggle <true|false>` — Enable or disable reminders
- `/reminders set_default_channel [#channel]` — Default channel for new reminders
- `/reminders set_everyone <true|false>` — Allow @everyone mentions in reminders

---

### TicketBot — `/ticketbot`
- `/ticketbot show` — Show current settings
- `/ticketbot set_category [#category]` — Category for ticket channels
- `/ticketbot set_staff_role [@role]` — Staff role with ticket access
- `/ticketbot set_escalation_role [@role]` — Role for ticket escalation

---

### Grok / AI — `/gpt`
- `/gpt show` — Show current settings
- `/gpt set_model [model]` — AI model (e.g. `grok-3`) — bot owner only
- `/gpt set_temperature [0.0–2.0]` — AI creativity level

---

### Alphapy Agents — `/config agents`
- `/config agents show` — Show whether agents are enabled in this server
- `/config agents toggle <true|false>` — Enable or disable `/agent` for members (requires `ALPHAPY_AGENTS_ENABLED=true` on the deployment)

When enabled, linked members can use [`/agent`](#alphapy-agents). See [configuration.md](./configuration.md) for Railway env vars.

---

### Invites — `/invites`
- `/invites show` — Show current settings
- `/invites toggle <true|false>` — Enable or disable invite tracking
- `/invites set_channel [#channel]` — Invite announcement channel
- `/invites set_template <variant> [template]` — Invite message template (variant: `with`/`without` inviter)

---

### GDPR — `/gdpr`
- `/gdpr show` — Show current settings
- `/gdpr toggle <true|false>` — Enable or disable GDPR features
- `/gdpr set_channel [#channel]` — GDPR document channel
- `/gdpr set_acceptance_role [@role]` — Role assigned when member clicks "I Agree"
- `/gdpr post` — Post and pin the GDPR agreement embed

---

### Onboarding — `/onboarding`
- `/onboarding show` — Show current onboarding configuration
- `/onboarding toggle <true|false>` — Enable or disable onboarding
- `/onboarding set_mode <mode>` — Mode: `Disabled` / `Rules Only` / `Rules + Questions` / `Questions Only`
- `/onboarding add_question <step> <question> [type] [required]` — Add a question (types: `select`, `multiselect`, `text`, `email`)
- `/onboarding delete_question <step>` — Delete question at position
- `/onboarding reset_questions` — Clear all questions
- `/onboarding add_rule <order> <title> <description> [thumbnail_url] [image_url]` — Add a rule
- `/onboarding delete_rule <order>` — Delete rule at position
- `/onboarding reset_rules` — Clear all rules
- `/onboarding set_role [@role]` — Completion role
- `/onboarding set_join_role [@role]` — Temporary join role (removed after onboarding/verification)
- `/onboarding panel_post [#channel]` — Post onboarding panel with Start button
- `/onboarding reorder` — Reorder questions via modal

---

### Verification — `/verification`
- `/verification show` — Show current settings
- `/verification set_verified_role [@role]` — Role assigned after successful verification
- `/verification set_category [#category]` — Category for verification channels
- `/verification set_vision_model [model]` — Vision-capable AI model
- `/verification set_ai_prompt_context [text]` — Extra AI context (what a valid payment looks like)
- `/verification set_reviewer_role [@role]` — Role tagged when manual review is triggered
- `/verification set_max_payment_age [days]` — Max payment screenshot age (1–365, default 35)
- `/verification set_reference_image <image>` — Upload reference payment screenshot for AI comparison
- `/verification reset_reference_image` — Remove reference screenshot

---

### Auto-moderation — `/automod`
- `/automod status` — Current automod status, rule count, premium status
- `/automod show` — Show all automod settings
- `/automod toggle <true|false>` — Enable or disable auto-moderation
- `/automod set_log_channel [#channel]` — Automod violation log channel
- `/automod rules` — List all configured rules
- `/automod add_spam_rule <name> [max_messages] [window_seconds] [action]` — Spam frequency rule
- `/automod add_badwords_rule <name> <words> [action]` — Bad-words rule
- `/automod add_links_rule <name> [allow_links] [whitelist] [blacklist] [action]` — Link filter rule
- `/automod add_mentions_rule <name> [max_mentions] [action]` — Mention spam rule
- `/automod add_caps_rule <name> [min_length] [max_ratio] [action]` — Excessive caps rule
- `/automod add_duplicate_rule <name> [max_duplicates] [action]` — Duplicate message rule
- `/automod add_regex_rule <name> <patterns> [action]` — Regex rule (premium)
- `/automod add_ai_rule <name> [action]` — AI-powered content rule (premium)
- `/automod edit_rule <rule_id> [fields...]` — Edit an existing rule
- `/automod delete_rule <rule_id>` — Delete a rule
- `/automod set_rule_enabled <rule_id> <true|false>` — Enable or disable a rule
- `/automod set_severity <rule_id> <1–10>` — Rule priority (higher = processed first)
- `/automod logs [limit] [user_id] [rule_id] [action] [days]` — Recent automod logs

Notes:
- `action` parameters use fixed slash-command choices: `delete`, `warn`, `mute`, `timeout`, `ban`.
- `rule_id` now supports autocomplete in `/automod delete_rule`, `/automod set_rule_enabled`, `/automod edit_rule`, `/automod set_severity`, and `/automod logs`.

---

### Growth — `/growth`
- `/growth set_channel [#channel]` — Channel for shared Growth Check-ins; leave empty to remove

---

### FYI — `/fyi`
- `/fyi reset <key>` — Clear an FYI flag so the next natural trigger resends the tip
- `/fyi send <key>` — Force-send the tip to the log channel now

**Permissions:** Administrator (all configuration commands)

---

## Custom Commands

Guild admins can define automated message responses triggered by specific message patterns. Supports four trigger types and dynamic variable substitution.

### `/cc add`
Create a new custom command.

**Parameters:**
- `name` (required): Unique slug for this command (e.g. `hello`)
- `trigger_type` (required): `exact` / `starts_with` / `contains` / `regex`
- `trigger` (required): The text or regex pattern to match (max 200 chars)
- `response` (required): The response to send (max 1900 chars, supports variables)
- `case_sensitive` (optional): Match case-sensitively (default: false)
- `delete_trigger` (optional): Delete the triggering message (default: false)
- `reply_to_user` (optional): Reply to the message instead of a plain send (default: true)

**Response variables:**
- `{user}` — mention the member
- `{user.name}` — display name
- `{server}` — server name
- `{channel}` — channel mention
- `{uses}` — how many times this command has been triggered
- `{random:a|b|c}` — random pick from pipe-delimited options

**Limits:** Max 50 commands per server. Invalid regex is rejected at creation.

**Permissions:** Administrator

---

### `/cc edit`
Edit the trigger and response of an existing command via a Discord Modal.

**Parameters:**
- `name` (required): Name of the command to edit

**Permissions:** Administrator

---

### `/cc delete`
Delete a custom command (shows a confirmation button).

**Parameters:**
- `name` (required): Name of the command to delete

**Permissions:** Administrator

---

### `/cc list`
List all custom commands for this server (name, trigger type, trigger preview, use count, enabled status).

**Permissions:** Administrator

---

### `/cc view`
Show full details of a specific custom command (trigger, response, options, use count).

**Parameters:**
- `name` (required): Name of the command to view

**Permissions:** Administrator

---

### `/cc toggle`
Enable or disable a custom command.

**Parameters:**
- `name` (required): Name of the command to toggle

**Permissions:** Administrator

---

## FAQ

### `/faq list`
Show the latest FAQ entries (last 10).

**Parameters:**
- `public` (optional): Post in channel instead of ephemeral (default: false)

**Response:** Embed with last 10 FAQ entries (title and summary). Pagination buttons to browse.

**Permissions:** Public

---

### `/faq view`
View a single FAQ entry by ID.

**Parameters:**
- `id` (required): FAQ entry ID
- `public` (optional): Post in channel instead of ephemeral (default: false)

**Permissions:** Public

---

### `/faq search`
Search FAQ entries by keywords.

**Parameters:**
- `query` (required): Your question or keywords
- `public` (optional): Post in channel instead of ephemeral (default: false)

**Response:** Up to 5 matching FAQ entries.

**Permissions:** Public

---

### `/faq add`
Add a new FAQ entry (admin only).

**Behavior:** Opens a modal to enter title, summary, and optional keywords.

**Permissions:** Owner/Admin

---

### `/faq edit`
Edit an existing FAQ entry (admin only).

**Parameters:**
- `id` (required): FAQ entry ID

**Behavior:** Opens a modal to edit title, summary, and keywords.

**Permissions:** Owner/Admin

---

### `/faq reload`
Reload the FAQ index (admin only). Use after bulk changes or imports.

**Permissions:** Owner/Admin

---

## AI Features

### `/growthcheckin`
Grok-powered check-in for goals, obstacles, and emotions.

**Behavior:** Opens a modal with three fields (goal, obstacle, feeling). Grok responds with coaching feedback. If the user has past reflections, Grok actively references patterns and progress. Premium users receive Mockingbird mode (direct, sharper tone).

After the response, an optional share prompt appears (ephemeral):
- **Share anonymously** — posts the check-in + Grok response as an embed in the growth channel, without name or avatar.
- **Share with my name** — same embed with display name and avatar.
- **Keep private** — dismisses the prompt.

The share option only appears if a growth channel has been configured by an admin (`/growth set_channel`). All sharing is opt-in per interaction.

**Cooldown:** 2 uses per 5 minutes per user per guild.

---

### `/growthhistory`
View your recent Growth Check-ins.

**Behavior:** Shows your last 15 check-ins in a paginated embed (3 per page). Each entry displays the date, goal, and obstacle. Use the dropdown to open a specific check-in in full detail, including Grok's reflection response. Navigation via Previous/Next buttons. In the detail view, a Delete button allows removing the check-in (with confirmation step).

**Cooldown:** 1 use per 30 seconds per user.

---

### `/learn_topic`
Hybrid topic search using local knowledge base + Drive content.

**Parameters:**
- `topic` (required): Topic to learn about

**Behavior:** Searches local `.md` files and Google Drive content, then generates a comprehensive explanation using Grok.

---

### `/create_caption`
Generate social media captions based on topic and style.

**Parameters:**
- `topic` (required): Topic for the caption
- `style` (optional): Caption style/tone

**Behavior:** Generates a caption using Grok based on the provided topic and style.

---

### `/lotquiz`
Test your risk management across 3 forex scenarios.

**Behavior:** Interactive quiz that presents 3 trading scenarios and evaluates risk management decisions.

---

### `/leaderhelp`
AI-powered leadership guidance for challenges, team growth, or doubts.

**Behavior:** Opens a view to choose a challenge type (e.g., disengaged team, burnout, dropoff, self-doubt) or ask a custom question. Responses are generated by Grok.

**Permissions:** Public (with cooldown)

---

## Alphapy Agents

Personal growth agents for linked Innersync users. Requires `/link` and guild enablement via `/config agents toggle true`. Deployment must set `ALPHAPY_AGENTS_ENABLED=true`.

All responses are **ephemeral**. Agents use opt-in shared reflections only — never encrypted App journals. See [agents-safety-guidelines.md](./agents-safety-guidelines.md).

### `/agent list`
List available Alphapy agents (currently **reflection** only).

**Permissions:** Everyone (ephemeral)

---

### `/agent start`
Start a multi-turn reflection agent session (first turn).

**Parameters:**
- `message` (optional): Focus question or topic for the agent

**Behavior:** Creates an `active` session, gathers skill context, runs Grok, stores the turn in `agent_session_messages`, and returns an ephemeral embed. Session stays open until `/agent end`.

**Example:**
```
/agent start message:"What patterns do you see in my recent reflections?"
```

**Permissions:** Linked Innersync users only (ephemeral)

**Note:** Blocked if you already have an active session — use `/agent continue` or `/agent end` first. Free tier: up to **10** `/agent start` calls per user per day; monthly **25**; yearly/lifetime unlimited. `/agent continue` does not count toward the cap.

---

### `/agent continue`
Add a follow-up turn to your active reflection session.

**Parameters:**
- `message` (required): Your follow-up for the agent

**Behavior:** Loads prior turns from `agent_session_messages`, appends your message, runs Grok, and returns an ephemeral embed.

**Permissions:** Linked Innersync users only (ephemeral)

---

### `/agent end`
End your active reflection session.

**Behavior:** Runs Tier 2 distill (if learning enabled + active consents), patches Tier 3 memory (`session_count++`), completes the session, deletes ephemeral messages, and emits a Hermit `gpt_command` event.

**Permissions:** Linked Innersync users only (ephemeral)

---

### `/agent status`
Show your active reflection agent session, if any.

**Behavior:** Returns start time and turn count. Empty when no session is active.

**Permissions:** Linked Innersync users only (ephemeral)

---

## System

### `/innersync`
Show Innersync info and official links.

**Response:** Informational embed with:
- Brief description of Innersync and Alphapy
- Links to Core, App, and Pricing platforms
- Ephemeral response (visible only to the user)

**Permissions:** Public

---

### `/gptstatus`
Check the status of the Grok/LLM API.

**Response:** Shows API health (derived from own logs), current model, uptime, last success time, rolling average latency, last triggering user, last error type and time, interaction counts (success/error), rate limit hits this session, live retry queue size, and total tokens used this session.

---

### `/version`
Show bot version and codename.

**Response:** Current version (e.g., "3.9.0 - Reflection Loop")

---

### `/release`
Show release notes for the current version.

**Response:** Release notes from `RELEASES.md`

---

### `/health`
Show system status and configuration.

**Response:** Bot status, database status, guild count, and configuration overview.

---

### `/commands`
List all available bot commands.

**Parameters:**
- `include_admin` (optional, default: False): Include admin-only commands in the list
- `public` (optional, default: False): Post in channel instead of ephemeral

**Response:** Nicely formatted embed listing commands by category.

---

### `/command_stats`
Show command usage statistics (admin only).

**Parameters:**
- `days` (optional, default: 7): Number of days to look back
- `limit` (optional, default: 10): Maximum number of commands to show
- `guild_only` (optional, default: True): Show stats for current server only, or all servers (owner only)

**Response:** Rich embed showing top commands by usage, total commands executed, period, and scope.

**Permissions:** Administrator (or bot owner for all-server stats)

**Example:**
```
/command_stats days:7 limit:10 guild_only:True
```

---

## Admin

### `/migrate`
Database migration management (admins only).

**Parameters:**
- `action` (required): Action to perform (`status`, `upgrade`, `downgrade`, `history`)

**Examples:**
```
/migrate action:status
/migrate action:upgrade
/migrate action:history
```

**Permissions:** Owner/Admin

---

### `/migrate_status`
Check database migration status (alias for `/migrate status`).

**Permissions:** Owner/Admin

---

### `/reload`
Reload a specific extension (owner only).

**Parameters:**
- `extension` (required): Name of the extension to reload (e.g., "cogs.reminders")

**Permissions:** Owner only

---

### `/export_onboarding`
Export onboarding data as CSV (owner only).

**Response:** CSV file download with all onboarding responses.

**Permissions:** Owner only

---

### `/export_tickets`
Export tickets as CSV (admins only).

**Response:** CSV file download with all ticket data.

**Permissions:** Owner/Admin

---

### `/export_faq`
Export FAQ entries as CSV (admins only).

**Response:** CSV file download with all FAQ entries.

**Permissions:** Owner/Admin

---

### `/delete_my_data`
Permanently delete your personal data stored in Alphapy's Railway database.

**Behavior:** Shows a confirmation flow before deleting user-scoped records (GDPR self-service). This action cannot be undone.

**Permissions:** Public (self-service)

---

### `/inviteleaderboard`
Show invite leaderboard.

**Response:** Leaderboard of users with highest invite counts.

---

### `/setinvites`
Manually set invite count for a user.

**Parameters:**
- `user` (required): User to set invites for
- `count` (required): Number of invites

**Permissions:** Owner/Admin

---

### `/resetinvites`
Reset invite count for a user to 0.

**Parameters:**
- `user` (required): User to reset invites for

**Permissions:** Owner/Admin

---

## Verification

### `/verification_panel_post`
Post a verification panel with a **Start verification** button.

**Behavior:**
- Posts an embed that explains the verification flow and provides a button.
- When a user clicks **Start verification**, the bot creates a private `verify-*` channel in the configured verification category with access for that user and staff.

**Permissions:** Owner/Admin

---

### `/verification_close`
Close a verification channel without approving or rejecting (neutral closure).

**Behavior:**
- Can only be used inside a verification channel (under the configured verification category).
- Marks the related `verification_tickets` row as `closed_manual`, sends a closure embed, and deletes the channel after 5 seconds.
- Sends a standardised summary to the guild log channel: user, outcome, resolver, timestamp.

**Permissions:** Owner/Admin

---

### `/verification set_reviewer_role [@role]`
Set the role that gets tagged when the AI triggers a manual review. Leave `role` empty to clear.

**Options:**
- `role` — A Discord role. Omit to remove the configured reviewer role.

**Behavior:**
- When the AI returns `needs_manual_review`, the bot pings this role in the verification channel alongside the review embed.
- Falls back to no mention if not configured.

**Permissions:** Administrator

---

### `/verification set_max_payment_age [days]`
Set the maximum age (in days) a payment screenshot may be. Leave `days` empty to reset to the default (35 days).

**Options:**
- `days` — Integer between 1 and 365. Omit to reset to default.

**Behavior:**
- The AI prompt is sent today's date and the configured window. The AI must extract the `payment_date` from the screenshot.
- A server-side check validates the extracted date independently — if older than the window, the submission is hard-rejected even if the AI said it was valid.
- If the date is unreadable and the AI was confident, the submission is escalated to manual review instead of auto-approved.

**Permissions:** Administrator

---

### Approve / Reject buttons

When the AI cannot auto-verify a screenshot, it posts a **Manual review required** embed with **Approve** and **Reject** buttons (admin-only).

**Approve:**
- Assigns the configured verified role to the user.
- Removes the onboarding join role if configured.
- Sends an approval embed, deletes the channel after 5 seconds, and logs the outcome.

**Reject:**
- Opens a modal where the admin can enter an optional rejection reason (shown to the user).
- Sends a rejection embed, deletes the channel after 5 seconds, and logs the outcome.

---

### `/debug_parse_embed`
Parse the last embed in the channel for testing.

**Behavior:** Attempts to parse the last embed message in the channel and shows the parsed result.

**Permissions:** Owner/Admin

---

## Permission Levels

- **Public**: Available to all users
- **Admin**: Requires administrator permissions or admin role
- **Owner**: Bot owner only (configured in `config.py`)

Most commands respect Discord's permission system and guild-specific admin roles configured via `/config` commands.
