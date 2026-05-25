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

All inbound webhooks (Supabase auth, premium invalidation, GDPR erasure, reflections, founder, legal-update) are verified with `HMAC-SHA256`. The shared utility `validate_webhook_signature()` uses `hmac.compare_digest` to prevent timing attacks.

If the secret for a webhook is not configured, validation is skipped and a debug log is emitted. For production:
- `SUPABASE_WEBHOOK_SECRET` — required for GDPR erasure and Supabase auth events
- `PREMIUM_INVALIDATE_WEBHOOK_SECRET` — required for premium invalidation
- `APP_REFLECTIONS_WEBHOOK_SECRET` — required for reflection sync

Leaving these unset in production means those endpoints are publicly triggerable.

### Rate limiting (`api.py` — `RateLimitMiddleware`)

In-memory, IP-based sliding-window rate limiter applied to all endpoints:

| Endpoint type | Limit |
|---|---|
| Health/metrics probes | 60 req/min |
| Read requests (GET) | 30 req/min |
| Write requests (POST/PUT/DELETE) | 10 req/min |

The in-memory store is cleaned every 10 minutes. Note: this is a single-instance limiter — it does not share state across multiple API replicas.

### Request tracing and API observability (`api.py`)

- `RequestObservabilityMiddleware` attaches/propagates `X-Request-ID` on every response.
- `GET /api/observability` exposes rolling API/webhook metrics:
  - request counts
  - success rates
  - latency percentiles (`p50`, `p95`, `p99`)
- `GET /api/observability` now requires `X-Api-Key` (service key) and returns `503` when no service key is configured.

This endpoint is intended for operational monitoring and troubleshooting.

### Input sanitization (`utils/sanitizer.py`)

- `safe_embed_text()` — strips mentions, filters dangerous URL protocols, escapes Discord markdown. Must be used for all user-supplied content placed in embeds.
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

Run `pip-audit -r requirements.txt` regularly to catch new CVEs. Dependabot or Renovate can automate this.

### Privileged Discord commands

- **`/migrate downgrade`** — restricted to `OWNER_IDS` only (not guild admins). Triggers `alembic downgrade -1` which is a destructive database operation.
- All admin commands use `validate_admin()` from `utils/validators.py`.
- Owner-only commands use `requires_owner()` decorator or an explicit `OWNER_IDS` check.

### Error disclosure

Internal exceptions must not be forwarded to Discord users or API clients. Log the full exception server-side with `logger.error(...)` and send only a generic message to the client (e.g. `"Database error. Please try again later."`).

---

# Google Cloud Security Best Practices

This document describes security best practices for Google Cloud credentials and API keys within the Alphapy project, in line with Google Cloud Security recommendations.

## Overview

Alphapy uses Google Cloud services for:
- **Google Drive API**: Reading PDF documents via service account credentials

All credentials are managed via **Google Cloud Secret Manager** in production, with fallback to environment variables for local development.

## Credential lifecycle management

### 1. Zero-code storage ✅

**Status**: Implemented

- ✅ Credentials are **never** committed to source code or version control
- ✅ `.gitignore` excludes `.env` and `credentials/`
- ✅ Production uses **Google Cloud Secret Manager** for credential storage
- ✅ Local development uses environment variables (fallback)

**Implementation**:
- `utils/gcp_secrets.py`: Helper for Secret Manager access with caching
- `utils/drive_sync.py`: Loads credentials from Secret Manager or environment variable
- Configuration via `GOOGLE_PROJECT_ID` and `GOOGLE_SECRET_NAME` environment variables

### 2. Secret Manager setup

**For production deployments**:

1. **Create secret in Secret Manager**:
   ```bash
   # Via gcloud CLI
   echo -n '{"type":"service_account",...}' | \
     gcloud secrets create alphapy-google-credentials \
     --data-file=- \
     --project=YOUR_PROJECT_ID
   ```

2. **Configure environment variables**:
   ```bash
   GOOGLE_PROJECT_ID=your-gcp-project-id
   GOOGLE_SECRET_NAME=alphapy-google-credentials  # Optional, default uses this name
   ```

3. **Grant access to service account**:
   ```bash
   gcloud secrets add-iam-policy-binding alphapy-google-credentials \
     --member="serviceAccount:YOUR_SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor" \
     --project=YOUR_PROJECT_ID
   ```

**For local development**:
- Use `GOOGLE_CREDENTIALS_JSON` environment variable
- Secret Manager is skipped when `GOOGLE_PROJECT_ID` is not set

### 3. Disable dormant keys

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

### 4. Enforce API restrictions

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

### 5. Apply least privilege

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

3. **Minimum roles for Secret Manager**:
   - `roles/secretmanager.secretAccessor` — Read secrets only
   - No `roles/secretmanager.admin` or `roles/secretmanager.secretAccessor` on project-level

**Current implementation**:
- ✅ Service account uses minimum scope (`drive.readonly`)
- ⚠️ **TODO**: Review IAM roles via IAM Recommender and remove unused permissions

### 6. Mandatory rotation

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

**Rotation procedure** (when key expires):
1. Generate new service account key in GCP Console
2. Update secret in Secret Manager:
   ```bash
   echo -n 'NEW_CREDENTIALS_JSON' | \
     gcloud secrets versions add alphapy-google-credentials \
     --data-file=-
   ```
3. Cache is automatically invalidated after TTL (1 hour)
4. Old key version can be removed after verification

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
- [x] Secret Manager integration with caching
- [x] Fallback to environment variables for local dev
- [x] Error handling for Secret Manager failures
- [x] Logging for security events (which method is used)
- [x] Minimum scopes (`drive.readonly`)

### Infrastructure-level (manual configuration) ⚠️

- [ ] API key restrictions configured (if applicable)
- [ ] Service account IAM permissions reviewed via IAM Recommender
- [ ] Unused permissions removed
- [ ] Key rotation policy configured (`iam.serviceAccountKeyExpiryHours`)
- [ ] Essential Contacts configured
- [ ] Budget alerts configured
- [ ] Anomaly detection enabled
- [ ] Dormant keys audit performed (30+ days inactive)

## Monitoring and alerting

### Secret Manager access logs

Monitor Secret Manager access via Cloud Audit Logs:

```bash
# View Secret Manager access logs
gcloud logging read "resource.type=secretmanager.googleapis.com/Secret" \
  --project=YOUR_PROJECT_ID \
  --limit=50
```

### Anomaly detection

- Monitor for unexpected Secret Manager access patterns
- Alert on failed authentication attempts
- Review logs monthly for security events

## Incident response

If credentials are compromised:

1. **Immediate actions**:
   - Disable the compromised key in GCP Console
   - Rotate secret in Secret Manager
   - Clear cache in application (restart or `clear_cache()` call)

2. **Investigation**:
   - Review Cloud Audit Logs for unauthorized access
   - Check for unexpected API calls
   - Document incident in security log

3. **Prevention**:
   - Review security configurations
   - Update IAM permissions if needed
   - Verify all best practices are followed

## References

- [Google Cloud Secret Manager Documentation](https://cloud.google.com/secret-manager/docs)
- [Google Cloud Security Best Practices](https://cloud.google.com/security/best-practices)
- [IAM Recommender](https://cloud.google.com/iam/docs/recommender-overview)
- [Service Account Key Management](https://cloud.google.com/iam/docs/service-accounts)
