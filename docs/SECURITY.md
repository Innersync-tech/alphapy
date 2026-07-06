---
title: Security
description: Security practices for Alphapy configuration, webhooks, and operations.
---

# Security Reference

---

## Application-level security

### API authentication (`api.py`)

The FastAPI layer uses two authentication mechanisms, applied in order:

1. **Supabase JWT** — `Authorization: Bearer <token>` header. Token is validated by calling `SUPABASE_URL/auth/v1/user` using an async HTTP client (non-blocking).
2. **Static API key** — `X-Api-Key` header. Only checked if JWT validation fails and `API_KEY` is set.

If neither `API_KEY` nor `SUPABASE_URL` are configured, the API runs in **unauthenticated mode** and logs a startup warning. This mode is intentional for local development only — always set at least one auth mechanism in production.

For production hardening, enable:
- `APP_ENV=production`
- `STRICT_SECURITY_MODE=1`

With strict mode enabled, API startup fails fast if critical auth/webhook secrets are missing, instead of only logging warnings.

**Never trust `X-User-ID` or similar forwarded-identity headers.** User identity is always derived from verified JWT claims only.

### Webhook HMAC validation (`webhooks/common.py`)

All inbound webhooks (Supabase auth, premium invalidation, GDPR erasure, reflections, founder, legal-update, Discord link) are verified with `HMAC-SHA256`. The shared utility `validate_webhook_signature()` uses `hmac.compare_digest` to prevent timing attacks.

**Fail-closed behavior (2026-06-21):** When `APP_ENV=production` or `STRICT_SECURITY_MODE=1` is set, any webhook whose `*_WEBHOOK_SECRET` env var is absent returns `HTTP 503 Service Unavailable` immediately — the endpoint does not execute. Previously, missing secrets caused validation to be silently skipped. In non-production environments, missing secrets still emit a debug log and skip validation (development convenience only).

All of the following secrets are **required in production**:

| Secret | Protects |
|---|---|
| `SUPABASE_WEBHOOK_SECRET` | GDPR erasure + Supabase auth events |
| `PREMIUM_INVALIDATE_WEBHOOK_SECRET` | Premium subscription invalidation |
| `APP_REFLECTIONS_WEBHOOK_SECRET` | Reflection sync from Core-API |
| `DISCORD_LINK_WEBHOOK_SECRET` | Discord ↔ Innersync identity link/unlink |
| `FOUNDER_WEBHOOK_SECRET` | Founder welcome DM trigger |
| `LEGAL_UPDATE_WEBHOOK_SECRET` | ToS / Privacy Policy change notifications |

Leaving any of these unset in production means the corresponding endpoint is either unaccessible (503) or publicly triggerable — configure all of them before deploying.

### Rate limiting (`api.py` — `RateLimitMiddleware`)

In-memory, IP-based sliding-window rate limiter applied to all endpoints:

| Endpoint type | Limit |
|---|---|
| Health/metrics probes | 60 req/min |
| Read requests (GET) | 30 req/min |
| Write requests (POST/PUT/DELETE) | 10 req/min |

The in-memory store is cleaned every 10 minutes. Note: this is a single-instance limiter — it does not share state across multiple API replicas.

### Agent session quotas (Discord `/agent`)

Separate from API IP rate limits. Enforced in `start_agent_session()` via `check_and_increment_agent_session_quota()` (`utils/premium_guard.py`).

| Tier | Daily `/agent start` cap |
|------|--------------------------|
| free | 10 |
| monthly | 25 |
| yearly / lifetime | unlimited |

- Storage: Railway table `agent_session_usage` (Alembic migration `024`)
- `/agent continue` and `/agent end` do not consume quota
- Fails open on DB error (same policy as GPT quota)
- Counters purged on GDPR erasure (`webhooks/supabase.py`, `/delete_my_data`)

Tier constants: `AGENT_DAILY_SESSION_LIMIT` in `utils/premium_tiers.py`. See `docs/alphapy-agents-architecture.md` §6.

### Request tracing and API observability (`api.py`)

- `RequestObservabilityMiddleware` attaches/propagates `X-Request-ID` on every response.
- `GET /api/observability` exposes rolling API/webhook metrics:
  - request counts
  - success rates
  - latency percentiles (`p50`, `p95`, `p99`)
- `GET /api/observability` now requires `X-Api-Key` (service key) and returns `503` when no service key is configured.

This endpoint is intended for operational monitoring and troubleshooting.

### Input sanitization (`utils/sanitizer.py`)

- `safe_embed_text(text, limit)` — strips mentions, filters dangerous URL protocols, escapes Discord markdown, and truncates to `limit` characters. **Must be used for all user-supplied content placed in embeds.** As of 2026-06-21 this is enforced across the codebase with the following limits:

  | Context | Limit |
  |---|---|
  | Automod violation — message content | 200 chars |
  | Engagement challenge titles | 1 024 chars |
  | Discord display names (embed fields / author) | 256 chars |
  | Ticket descriptions | 3 800 chars |
  | General embed fields (default) | 1 024 chars |

- `safe_prompt()` — detects jailbreak patterns and neutralizes them before passing to LLM APIs.
- `safe_log_message()` — removes control characters and truncates before logging.

### Owner/admin IDs (`config.py`)

Bot owner and admin role IDs are loaded from environment variables `OWNER_IDS` and `ADMIN_ROLE_ID` (comma-separated integers). Hardcoding these values in source code is not allowed. The fallback defaults are left in place for backward compatibility, but must be overridden in production via env vars.

### Dependency security

Known-vulnerable transitive dependencies are pinned explicitly in `requirements.txt`:

```
cryptography>=46.0.6
pyopenssl>=26.0.0
requests>=2.33.0
```

**CI scanning (2026-06-21):** `pip-audit` runs as a parallel CI job on every push to any branch. The job fails the build on any known CVE — vulnerabilities block merging without an explicit override. Run the same check locally before pushing:

```bash
pip-audit -r requirements.txt
```

### Privileged Discord commands

- **`/migrate downgrade`** — restricted to `OWNER_IDS` only (not guild admins). Triggers `alembic downgrade -1` which is a destructive database operation.
- All admin commands use `validate_admin()` from `utils/validators.py`.
- Owner-only commands use `requires_owner()` decorator or an explicit `OWNER_IDS` check.

### Error disclosure

Internal exceptions must not be forwarded to Discord users or API clients. Log the full exception server-side with `logger.error(...)` and send only a generic message to the client (e.g. `"Database error. Please try again later."`).

---

## Google Cloud (Drive API)

Alphapy uses a **Google service account** for read-only Drive access (`/learn_topic`, PDF loading).

### Credential storage

- **Never** commit keys to git (`.env` and `credentials/` are gitignored).
- **Runtime**: `GOOGLE_CREDENTIALS_JSON` — full service account JSON as one env var (local `.env` or Railway).
- **Code**: `utils/drive_sync.py` parses `GOOGLE_CREDENTIALS_JSON` at startup; no GCP Secret Manager integration.

See [Google credentials setup](../google_credentials_setup/) for creating the service account and setting Railway variables.

### Disable dormant keys

**Manual action required in GCP Console**:

1. Go to **"APIs & Services" > "Credentials"**
2. Review all API keys and service account keys
3. Identify keys with no activity (30+ days)
4. **Decommission inactive keys**:
   - Click on the key
   - Select "Delete" or "Disable"
   - Confirm deactivation

**Audit procedure** (monthly):
- Check "APIs & Services" > "Credentials" for inactive keys
- Review Cloud Audit Logs for key usage patterns
- Document all deactivated keys in project changelog

### Enforce API restrictions

**Manual configuration in GCP Console**:

For **API keys**:
1. Go to **"APIs & Services" > "Credentials"**
2. Select an API key
3. Click "Restrict key"
4. **API restrictions**:
   - Select "Restrict key"
   - Choose only the required APIs (e.g. "Drive API")
   - Save
5. **Application restrictions** (if applicable):
   - **IP addresses**: Add allowed IP ranges
   - **HTTP referrers**: Add allowed referrer URLs
   - **Android apps**: Add package names
   - **iOS apps**: Add bundle IDs

For **service account keys**:
- Service accounts automatically have limited scopes (see code: `drive.readonly`)
- No extra API restrictions needed (scopes are sufficient)

**Current implementation**:
- ✅ Service account uses only `https://www.googleapis.com/auth/drive.readonly` scope
- ⚠️ **TODO**: Configure API key restrictions in GCP Console if API keys are used

### Apply least privilege

**Service account permissions**:

**Current scopes** (implemented in code):
- ✅ `https://www.googleapis.com/auth/drive.readonly` — Read-only, no write

**IAM permissions review**:

1. **Use IAM Recommender**:
   ```bash
   # Via gcloud CLI
   gcloud recommender recommendations list \
     --recommender=google.iam.policy.Recommender \
     --project=YOUR_PROJECT_ID \
     --location=global
   ```

2. **Review unused permissions**:
   - Go to **"IAM & Admin" > "IAM"**
   - Select service account
   - Review assigned roles
   - Remove unused roles

**Current implementation**:
- ✅ Service account uses minimum scope (`drive.readonly`)
- ⚠️ **TODO**: Review IAM roles via IAM Recommender and remove unused permissions

### Key rotation

**Organization policies** (must be configured by GCP admin):

1. **Key expiry policy**:
   ```bash
   # Set maximum key lifetime (e.g. 90 days)
   gcloud resource-manager org-policies set \
     iam.serviceAccountKeyExpiryHours \
     --organization=ORGANIZATION_ID \
     --policy-file=policy.json
   ```
   
   Policy file (`policy.json`):
   ```json
   {
     "spec": {
       "rules": [{
         "values": {
           "allowedValues": ["2160"]
         }
       }]
     }
   }
   ```
   Note: `2160` = 90 days in hours

2. **Disable key creation** (if keys are not needed):
   ```bash
   gcloud resource-manager org-policies set \
     iam.disableServiceAccountKeyCreation \
     --organization=ORGANIZATION_ID \
     --enforce
   ```

**For this project**:
- ⚠️ **TODO**: Configure `iam.serviceAccountKeyExpiryHours` policy (recommended: 90 days)
- Service account keys are used, so disable policy does not apply

**Rotation procedure**:
1. Create a new JSON key for the service account in GCP Console.
2. Update `GOOGLE_CREDENTIALS_JSON` in Railway (or local `.env`).
3. Redeploy / restart the bot.
4. Delete the old key in GCP after Drive access is verified.

## Operational safeguards

### 1. Essential contacts

**Configuration in GCP Console**:

1. Go to **"IAM & Admin" > "Essential Contacts"**
2. Add contacts for:
   - **Security**: Security team email
   - **Billing**: Finance team email
   - **Technical**: DevOps team email
3. Select notification categories:
   - Security notifications
   - Billing notifications
   - Technical notifications

**For this project**:
- ⚠️ **TODO**: Configure Essential Contacts with appropriate email addresses

### 2. Billing anomaly and budget alerts

**Configuration in GCP Console**:

1. **Budget alerts**:
   - Go to **"Billing" > "Budgets & alerts"**
   - Create new budget alert
   - Set threshold (e.g. 80% of monthly budget)
   - Add email notifications

2. **Anomaly detection**:
   - Go to **"Billing" > "Budgets & alerts"**
   - Enable "Anomaly detection"
   - Configure threshold (e.g. 150% of average daily spend)
   - Add email notifications

**For this project**:
- ⚠️ **TODO**: Configure budget alerts and anomaly detection
- ⚠️ **TODO**: Set threshold based on expected usage

## Security checklist

### Code-level (implemented) ✅

- [x] Credentials not committed in source code
- [x] Drive credentials loaded from `GOOGLE_CREDENTIALS_JSON` only
- [x] Clear logging when Drive is configured or missing
- [x] Minimum scopes (`drive.readonly`)
- [x] Webhook endpoints fail-closed (HTTP 503) when secrets are missing in production
- [x] `DISCORD_LINK_WEBHOOK_SECRET` enforced by startup strict mode check
- [x] `safe_embed_text()` applied to all user-supplied embed content with per-context limits
- [x] `pip-audit` runs in CI on every push; fails on any known CVE

### Infrastructure-level (manual configuration) ⚠️

- [ ] API key restrictions configured (if applicable)
- [ ] Service account IAM permissions reviewed via IAM Recommender
- [ ] Unused permissions removed
- [ ] Key rotation policy configured (`iam.serviceAccountKeyExpiryHours`)
- [ ] Essential Contacts configured
- [ ] Budget alerts configured
- [ ] Anomaly detection enabled
- [ ] Dormant keys audit performed (30+ days inactive)

## Monitoring

- Review GCP **Cloud Audit Logs** for service account and Drive API usage.
- Watch Alphapy logs for Drive auth failures after deploys or key rotation.

## Incident response

If credentials are compromised:

1. **Immediate actions**:
   - Disable or delete the compromised key in GCP Console
   - Update `GOOGLE_CREDENTIALS_JSON` with a new key and redeploy

2. **Investigation**:
   - Review Cloud Audit Logs for unauthorized access
   - Check for unexpected API calls
   - Document incident in security log

3. **Prevention**:
   - Review security configurations
   - Update IAM permissions if needed
   - Verify all best practices are followed

## References

- [Google Cloud Security Best Practices](https://cloud.google.com/security/best-practices)
- [IAM Recommender](https://cloud.google.com/iam/docs/recommender-overview)
- [Service Account Key Management](https://cloud.google.com/iam/docs/service-accounts)
