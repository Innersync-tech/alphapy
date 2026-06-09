#!/usr/bin/env python3
"""
One-off backfill: copy legacy Supabase profiles.discord_id into Railway alphapy_discord_links
where a valid mapping exists and Railway has no row yet.

Requires Supabase REST env vars (utils.supabase_client). Live run also needs DATABASE_URL
(Railway) and project deps: pip install -r requirements.txt

Usage (from alphapy repo root):
    ./scripts/run_backfill_discord_links.sh --dry-run
    ./scripts/run_backfill_discord_links.sh

Or with an activated venv (pip install -r requirements.txt):
    PYTHONPATH=. python scripts/backfill_discord_links_from_profiles.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging

try:
    from utils.supabase_client import _supabase_get
except ImportError as exc:
    raise SystemExit(
        "Missing Python dependencies (httpx, etc.). Use the venv runner:\n"
        "  ./scripts/run_backfill_discord_links.sh --dry-run\n"
        "Or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    ) from exc

logger = logging.getLogger(__name__)


async def main(*, dry_run: bool) -> None:
    import config

    if not dry_run and not config.DATABASE_URL:
        raise SystemExit("DATABASE_URL is required (omit --dry-run for live backfill)")

    rows = await _supabase_get(
        "profiles",
        {
            "select": "user_id,discord_id",
            "discord_id": "not.is.null",
            "limit": 5000,
        },
    )
    if not rows:
        logger.info("No profiles with discord_id found")
        return

    inserted = 0
    skipped = 0
    candidates: list[tuple[str, int]] = []

    for row in rows:
        user_id = row.get("user_id")
        discord_raw = row.get("discord_id")
        if not user_id or discord_raw is None:
            skipped += 1
            continue
        try:
            discord_user_id = int(discord_raw)
        except (TypeError, ValueError):
            skipped += 1
            continue
        candidates.append((str(user_id), discord_user_id))

    if dry_run:
        for user_id, discord_user_id in candidates:
            logger.info("would backfill user=%s discord=%s", user_id, discord_user_id)
            inserted += 1
        logger.info(
            "backfill dry-run complete would_insert=%s skipped=%s",
            inserted,
            skipped,
        )
        return

    import asyncpg

    from utils.innersync_identity import upsert_discord_link

    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=2)
    assert pool is not None

    try:
        for user_id, discord_user_id in candidates:
            status, err = await upsert_discord_link(
                pool,
                innersync_user_id=user_id,
                discord_user_id=discord_user_id,
                link_source="legacy_profile_backfill",
            )
            if status in {"ok", "noop"}:
                inserted += 1
            else:
                logger.warning("skip user=%s discord=%s: %s", user_id, discord_user_id, err)
                skipped += 1
    finally:
        await pool.close()

    logger.info("backfill complete inserted_or_noop=%s skipped=%s dry_run=%s", inserted, skipped, dry_run)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
