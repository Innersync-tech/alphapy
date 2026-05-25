---
title: Premium
description: Premium tiers, webhooks, and related environment variables.
---

# Premium Tier

The Alphapy bot supports a Premium tier for mature users. Premium features are gated via `utils/premium_guard.is_premium(user_id, guild_id)`.

**One server per subscription.** Each user has at most one active Premium subscription, and it applies to a single guild. Pay once and choose where you want full power (image reminders, Mockingbird mode, etc.). To move Premium to another server (e.g. your "home" server changed), use `/premium_transfer` in the target server, or request a transfer via support (dashboard coming later). If you have Premium but have not yet chosen a server, `/my_premium` will show "You have Premium but haven't chosen a server yet. Use `/premium_transfer` in the server you want." If your Premium is active in another server, it will show "Your Premium is active in another server. Use `/premium_transfer` here to move it."

## Features (Premium)

- **Reminders with images** – Add an image or banner URL (or attachment) to reminders; sent reminders show the image.
- **Live session presets** – Use `/add_live_session` to create a recurring reminder with fixed message "Live session starting now!" (optional image; premium required for images).
- **Mockingbird spicy mode** – In `/growthcheckin`, premium users get direct, sharp, no-sugar-coating replies.

## Pricing (display only)

- €4.99 / month  
- €29 / year (early bird)  
- €49 lifetime (first 50 members)

Payment and webhooks (Stripe/Lemon Squeezy) are out of scope for the initial release; the guard and `/premium` command are in place.

## Configuration

| Variable | Description |
|----------|-------------|
| `PREMIUM_CHECKOUT_URL` | Checkout page URL for the "Get Premium" button. If unset, the button shows "Coming soon" (disabled). |
| `PREMIUM_CACHE_TTL_SECONDS` | TTL in seconds for the in-memory premium cache (default: 300). |
| `CORE_API_URL` | When set, the guard calls `POST {CORE_API_URL}/api/premium/verify` first (see below). |
| `ALPHAPY_SERVICE_KEY` | API key for Core-API premium verify. |
| `PREMIUM_INVALIDATE_WEBHOOK_SECRET` | Optional. Secret for `POST /webhooks/premium-invalidate` (Core notifies on subscription change). Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`. |
| `FOUNDER_WEBHOOK_SECRET` | Optional. Secret for `POST /webhooks/founder` (founder welcome DM). Falls back to `APP_REFLECTIONS_WEBHOOK_SECRET` / `WEBHOOK_SECRET`. |

## Guard behaviour

1. **Cache** – In-memory cache keyed by `(user_id, guild_id)` with configurable TTL. Cache hit returns immediately.
2. **Core-API** – If `CORE_API_URL` and `ALPHAPY_SERVICE_KEY` are set, the guard sends `POST {CORE_API_URL}/api/premium/verify` with body `{"user_id": int, "guild_id": int}` and header `X-API-Key: ALPHAPY_SERVICE_KEY`. Response is expected as `{"premium": true|false, "tier": "monthly"|"yearly"|"lifetime"|null}`. On 2xx and `premium: true`, the user is treated as premium.
3. **Local fallback** – If Core is not configured or the request fails, the guard queries the local `premium_subs` table: `status = 'active'` and (`expires_at IS NULL` OR `expires_at > NOW()`).
4. **Fail closed** – On any error (timeout, DB failure), the guard returns `False`.

## Webhooks (Core → Alphapy)

When Core (or your payment backend) needs to notify Alphapy of changes, it can call:

- **Premium cache invalidate** – `POST /webhooks/premium-invalidate`  
  Body: `{"user_id": <discord user id>, "guild_id": <optional>}`. Clears the in-memory premium cache so the next `is_premium()` refetches from Core/DB. Use after payment, cancellation, or expiry. Signature: `X-Webhook-Signature` (HMAC-SHA256 of body with `PREMIUM_INVALIDATE_WEBHOOK_SECRET` or fallback).
- **Founder welcome** – `POST /webhooks/founder`  
  Body: `{"user_id": <discord user id>, "message": "<optional custom text>"}`. Sends a welcome DM to the user (e.g. after early-bird founder purchase). Signature: `X-Webhook-Signature` (HMAC-SHA256 with `FOUNDER_WEBHOOK_SECRET` or fallback).

Base URL is your Alphapy API (e.g. `https://alphapy.innersync.tech`). If the API is mounted under a path prefix (e.g. `/api`), use `https://<host>/api/webhooks/premium-invalidate` and `https://<host>/api/webhooks/founder`.

## Core-API contract (for later implementation)

- **Endpoint**: `POST {CORE_API_URL}/api/premium/verify`
- **Headers**: `Content-Type: application/json`, `X-API-Key: ALPHAPY_SERVICE_KEY`
- **Body**: `{"user_id": <discord user id>, "guild_id": <discord guild id>}`
- **Response**: `{"premium": true|false, "tier": "monthly"|"yearly"|"lifetime"|null}`

## Transfer (local DB)

When using the local `premium_subs` table (no Core-API or Core not configured), users can run `/premium_transfer` in the server they want Premium in. The guard updates the single active row’s `guild_id` and clears the cache. When Core-API is the source of truth, there may be no local row; transfer is then done via dashboard or support.

The database enforces at most one active subscription per user via a partial unique index on `premium_subs (user_id) WHERE status = 'active'` (migration 005).

## GDPR

The `premium_subs` table stores only what is needed for access control: `user_id`, `guild_id`, `tier`, `status`, optional `stripe_subscription_id` (external ID for support), `expires_at`, `created_at`. No payment details, email, or other PII are stored in this table.

## Lifetime cap

The "first 50 members" lifetime cap is enforced when processing payment/webhooks (e.g. Stripe or Lemon Squeezy), not in the guard. The guard only reads existing `premium_subs` or Core-API response.
