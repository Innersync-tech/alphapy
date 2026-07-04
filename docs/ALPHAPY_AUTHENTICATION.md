---
title: Alphapy API Authentication
description: How Alphapy API authentication works for Mind and service integrations.
---

## Overview

All Alphapy API endpoints require authentication. This document explains how authentication works and how to configure it correctly for the Mind dashboard.

**Endpoints that Mind uses:**
- `/api/dashboard/metrics` (or `/api/metrics` alias) - Live metrics
- `/api/health` - Health checks
- `/api/dashboard/settings/{guild_id}` - Get/update guild settings
- `/api/dashboard/{guild_id}/onboarding/questions` - Manage onboarding questions
- `/api/dashboard/{guild_id}/onboarding/rules` - Manage onboarding rules
- `/api/dashboard/{guild_id}/settings/history` - Settings change history
- `/api/dashboard/{guild_id}/settings/rollback/{history_id}` - Rollback settings
- `/api/dashboard/logs` - Operational logs (reconnect, disconnect; requires guild admin)

## Authentication Methods

The endpoint supports **two authentication methods**:

### 1. Supabase JWT Token (Recommended)

**Required Headers:**
```
Authorization: Bearer <supabase-jwt-token>
```

**How it works:**
- Alphapy validates the JWT token by calling `{SUPABASE_URL}/auth/v1/user`
- The token must be valid for the **same Supabase project** that Alphapy is configured to use
- Alphapy uses `SUPABASE_URL` and `SUPABASE_ANON_KEY` from its environment variables

**Configuration in Alphapy:**
```bash
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>
```

**Important:** The Supabase project used by Alphapy **must be the same** as the one used by Mind. If they use different Supabase projects, the JWT token from Mind will not be valid for Alphapy.

### 2. API Key + User ID (Fallback)

**Required Headers:**
```
X-API-Key: <api-key>
X-User-Id: <user-id>
```

**How it works:**
- If Supabase JWT validation fails, Alphapy falls back to API key authentication
- Requires `API_KEY` to be configured in Alphapy's environment variables
- The `X-User-Id` header is required to identify the user

**Configuration in Alphapy:**
```bash
API_KEY=<your-secret-api-key>
```

## Error: "Missing authentication context"

This error occurs when:
1. **No Authorization header** is provided, AND
2. **No X-User-Id header** is provided

**Solution:**
- Ensure you're sending either:
  - `Authorization: Bearer <supabase-jwt-token>` header, OR
  - `X-User-Id: <user-id>` header (along with `X-API-Key` if API key auth is configured)

## Common Issues

### Issue 1: Different Supabase Projects

**Symptom:** JWT token is valid in Mind but returns 401 in Alphapy

**Cause:** Mind and Alphapy are using different Supabase projects

**Solution:**
1. Verify that Alphapy's `SUPABASE_URL` matches Mind's `NEXT_PUBLIC_SUPABASE_URL`
2. Ensure both services use the same Supabase project
3. If they must use different projects, use API key authentication instead

### Issue 2: Missing Supabase Configuration

**Symptom:** Error "Supabase credentials are not configured"

**Cause:** `SUPABASE_URL` or `SUPABASE_ANON_KEY` not set in Alphapy

**Solution:**
```bash
# In Alphapy's environment variables:
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>
```

### Issue 3: Invalid JWT Token

**Symptom:** Error "Invalid Supabase token" or 401 Unauthorized

**Cause:** 
- Token is expired
- Token is for a different Supabase project
- Token format is incorrect

**Solution:**
- Ensure the token is fresh (Supabase tokens expire)
- Verify the token is for the correct Supabase project
- Check that the token includes `Bearer ` prefix (or let Alphapy handle it)

## Implementation Example (Mind Dashboard)

### Option 1: Using Supabase JWT (Recommended)

```typescript
// In Mind's API route or component
const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);

// Get the session token
const { data: { session } } = await supabase.auth.getSession();

if (!session?.access_token) {
  throw new Error("Not authenticated");
}

// Call Alphapy API
const alphapyBaseUrl = process.env.ALPHAPY_BASE_URL || "https://alphapy.innersync.tech";

const response = await fetch(`${alphapyBaseUrl}/api/dashboard/metrics`, {
  headers: {
    "Authorization": `Bearer ${session.access_token}`,
    // Optional: Add guild_id as query parameter
    // "guild_id": "123456789"
  }
});

if (!response.ok) {
  const error = await response.json();
  console.error("Alphapy API error:", error);
  throw new Error(error.detail || "Failed to fetch metrics");
}

const metrics = await response.json();
```

### Option 2: Using API Key (Fallback)

```typescript
// In Mind's API route or component
const alphapyBaseUrl = process.env.ALPHAPY_BASE_URL || "https://alphapy.innersync.tech";

const response = await fetch(`${alphapyBaseUrl}/api/dashboard/metrics`, {
  headers: {
    "X-API-Key": process.env.ALPHAPY_API_KEY!, // Must match Alphapy's API_KEY
    "X-User-Id": userId, // Required for API key auth
  }
});
```

### Example: Calling Settings Endpoint

```typescript
// Get guild settings
const guildId = "123456789";
const alphapyBaseUrl = process.env.ALPHAPY_BASE_URL || "https://alphapy.innersync.tech";

const { data: { session } } = await supabase.auth.getSession();

const response = await fetch(`${alphapyBaseUrl}/api/dashboard/settings/${guildId}`, {
  headers: {
    "Authorization": `Bearer ${session.access_token}`,
  }
});

const settings = await response.json();
```

### Example: Updating Settings

```typescript
// Update guild settings
const guildId = "123456789";
const alphapyBaseUrl = process.env.ALPHAPY_BASE_URL || "https://alphapy.innersync.tech";

const { data: { session } } = await supabase.auth.getSession();

const response = await fetch(`${alphapyBaseUrl}/api/dashboard/settings/${guildId}`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${session.access_token}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    category: "reminders",
    settings: {
      default_channel_id: "111222333",
      allow_everyone_mentions: false,
    },
  }),
});
```

## Environment Variables Summary

### Alphapy Required Variables

```bash
# Supabase Configuration (for JWT validation)
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_ANON_KEY=<anon-key>

# Optional: API Key (for fallback authentication)
API_KEY=<your-secret-api-key>
```

### Mind Required Variables

```bash
# Supabase Configuration (must match Alphapy's SUPABASE_URL)
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon-key>

# Alphapy API Base URL
ALPHAPY_BASE_URL=https://alphapy.innersync.tech
# For local development:
# ALPHAPY_BASE_URL=http://localhost:8000

# Optional: API Key (if using API key auth)
ALPHAPY_API_KEY=<same-as-alphapy-API_KEY>
```

## Testing Locally

### 1. Start Alphapy Locally

```bash
# In Alphapy directory
export SUPABASE_URL=https://<project-ref>.supabase.co
export SUPABASE_ANON_KEY=<anon-key>
# Optional:
export API_KEY=<test-api-key>

python3 bot.py
# API runs on http://localhost:8000
```

### 2. Test from Mind

```bash
# Set in Mind's .env.local
ALPHAPY_BASE_URL=http://localhost:8000
```

### 3. Test Authentication

```bash
# Test with Supabase JWT
curl -H "Authorization: Bearer <supabase-jwt-token>" \
  http://localhost:8000/api/dashboard/metrics

# Test with API Key
curl -H "X-API-Key: <api-key>" \
     -H "X-User-Id: test-user-123" \
  http://localhost:8000/api/dashboard/metrics
```

## Endpoint Details

### All Dashboard Endpoints Use Same Authentication

All `/api/dashboard/*` endpoints use the same authentication method:
- **Required:** Supabase JWT token (via `Authorization: Bearer <token>` header)
- **Optional Fallback:** API Key + User ID (if configured)

### Main Endpoints for Mind

#### `/api/dashboard/metrics` and `/api/metrics`

**Authentication:** Required (Supabase JWT OR API Key + User ID)

**Query Parameters:**
- `guild_id` (optional): Filter metrics by guild ID

**Response:** `DashboardMetrics` object with bot, Grok/LLM, reminders, tickets, infrastructure metrics, and optional `agent_sessions` (aggregate `/agent` counts for Mind observability — no session content)

#### `/api/dashboard/settings/{guild_id}`

**Authentication:** Required (Supabase JWT)

**Path Parameters:**
- `guild_id` (required): Discord guild ID

**Methods:** `GET`, `POST`

#### `/api/dashboard/{guild_id}/onboarding/questions`

**Authentication:** Required (Supabase JWT)

**Methods:** `GET`, `POST`, `DELETE`

#### `/api/dashboard/{guild_id}/onboarding/rules`

**Authentication:** Required (Supabase JWT)

**Methods:** `GET`, `POST`, `DELETE`

#### `/api/dashboard/{guild_id}/settings/history`

**Authentication:** Required (Supabase JWT)

**Query Parameters:**
- `scope` (optional): Filter by setting scope
- `key` (optional): Filter by setting key
- `limit` (optional, default: 50): Maximum records to return

#### `/api/dashboard/{guild_id}/settings/rollback/{history_id}`

**Authentication:** Required (Supabase JWT)

**Path Parameters:**
- `guild_id` (required): Discord guild ID
- `history_id` (required): History entry ID to rollback

**Error Responses (all endpoints):**
- `401 Unauthorized`: Missing or invalid authentication
- `403 Forbidden`: User doesn't have access to the requested resource
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server configuration issue (e.g., missing Supabase config)

## Security Notes

1. **Same Supabase Project Required**: For JWT authentication to work, Mind and Alphapy must use the same Supabase project. The JWT token is validated against Alphapy's configured `SUPABASE_URL`.

2. **API Key Security**: If using API key authentication, ensure `API_KEY` is kept secret and only shared between trusted services.

3. **Token Expiration**: Supabase JWT tokens expire. Ensure Mind refreshes tokens before they expire.

4. **Guild Filtering**: The `guild_id` query parameter filters results for security. Use it when possible to limit data exposure.

## Troubleshooting Checklist

- [ ] Verify `SUPABASE_URL` in Alphapy matches `NEXT_PUBLIC_SUPABASE_URL` in Mind
- [ ] Verify `SUPABASE_ANON_KEY` is set in Alphapy
- [ ] Check that the JWT token is fresh (not expired)
- [ ] Verify the `Authorization` header format: `Bearer <token>`
- [ ] Check Alphapy logs for detailed error messages
- [ ] If using API key auth, verify `API_KEY` matches in both services
- [ ] Ensure `X-User-Id` header is provided when using API key auth
