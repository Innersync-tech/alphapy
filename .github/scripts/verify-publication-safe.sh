#!/usr/bin/env bash
# Fail CI if synced docs contain internal or sensitive patterns.
# Canonical copy also lives in Innersync-tech/docs (scripts/verify-publication-safe.sh).
set -euo pipefail

ROOT="${1:-src/content/docs}"

if [ ! -d "$ROOT" ]; then
  echo "Missing docs root: $ROOT"
  exit 1
fi

failures=0

report() {
  echo "::error::$1"
  failures=$((failures + 1))
}

while IFS= read -r -d '' f; do
  report "Internal doc file must not be published: ${f#"$ROOT"/}"
done < <(find "$ROOT" -type f \( \
  -name 'premium-local-insert.md' -o \
  -name 'hermit-core-rollout.md' -o \
  -name 'testers-plan.md' -o \
  -name 'CONTRIBUTOR.md' -o \
  -name 'premium-layer-plan.md' -o \
  -name '.docs-publish-ignore' \
  \) -print0)

if command -v rg >/dev/null 2>&1; then
  if rg -n --glob '*.md' '\b[0-9]{17,19}\b' "$ROOT" 2>/dev/null; then
    report "Possible Discord/user snowflake IDs in published markdown"
  fi
  if rg -n --glob '*.md' 'postgresql://[^[:space:]]+:[^@[:space:]]+@' "$ROOT" 2>/dev/null; then
    report "PostgreSQL URL with embedded credentials pattern"
  fi
  if rg -n --glob '*.md' '(sk_live_|sk_test_|SUPABASE_SERVICE_ROLE_KEY=|BOT_TOKEN=)[^[:space:]]+' "$ROOT" 2>/dev/null; then
    report "Possible live secret value in markdown"
  fi
fi

if [ "$failures" -gt 0 ]; then
  echo "Publication safety check failed ($failures issue(s))."
  exit 1
fi

echo "Publication safety check passed for $ROOT"
