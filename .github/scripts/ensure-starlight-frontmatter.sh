#!/usr/bin/env bash
# Starlight docsSchema requires YAML frontmatter with title on every markdown page.
# Canonical copy also lives in Innersync-tech/docs (scripts/ensure-starlight-frontmatter.sh).
set -euo pipefail

ROOT="${1:-src/content/docs}"
missing=0

while IFS= read -r -d '' f; do
  if ! head -1 "$f" | grep -q '^---$'; then
    echo "::error::Missing Starlight frontmatter (---): ${f#"$ROOT"/}"
    missing=$((missing + 1))
  fi
done < <(find "$ROOT" -type f -name '*.md' -print0)

if [ "$missing" -gt 0 ]; then
  echo "Add --- title/description --- frontmatter before syncing to docs.innersync.tech."
  exit 1
fi

echo "Starlight frontmatter check passed for $ROOT"
