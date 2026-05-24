# Hermit via Core Rollout Runbook

This runbook describes staged rollout for Core-mediated Hermit context integration.

## Invariant

- No direct Alphapy <-> Hermes traffic.
- All strategic-context flow goes through Core.

## Deployment Order

1. Deploy Core endpoints (`POST` ingest + `GET` read) for `/integrations/hermit/strategic-context`.
2. Configure Core Railway env vars:
   - `HERMIT_PUSH_WEBHOOK_SECRET`
   - `HERMES_CONTEXT_MAX_CHARS`
3. Deploy Alphapy with feature flag disabled.
4. Configure Alphapy Railway env vars:
   - `HERMIT_CONTEXT_ENABLED=false`
   - `HERMIT_CONTEXT_TTL_SECONDS=1800`
   - `HERMIT_CONTEXT_TIMEOUT_SECONDS=2.0`
   - `CORE_HERMIT_CONTEXT_PATH=/integrations/hermit/strategic-context`

## Pilot Activation

1. Enable `HERMIT_CONTEXT_ENABLED=true` for pilot deployment window.
2. Monitor:
   - Core endpoint success/error and latency.
   - Alphapy `hermit_context` observability counters (`GET /api/observability`).
3. Keep pilot window 24-48h before wider rollout.

## Success Criteria

- Core fetch success rate >= 99%.
- No user-facing regressions in GPT commands.
- No prompt safety incident caused by upstream context text.

## Rollback

1. Set `HERMIT_CONTEXT_ENABLED=false` in Alphapy.
2. Redeploy Alphapy.
3. Keep Core endpoint online for diagnostics.

## Post-Flight Summary Template

- Core shipped:
- Alphapy shipped:
- Feature flag status:
- Pilot scope:
- Observed metrics (success, latency, fallback/stale usage):
- Go/no-go decision:
