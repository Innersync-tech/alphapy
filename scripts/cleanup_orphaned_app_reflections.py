#!/usr/bin/env python3
"""Dry-run-default cleanup of orphaned Railway app_reflections rows.

NOT applied automatically. Never SELECTs plaintext_content.

Usage (from alphapy repo root):
    PYTHONPATH=. python scripts/cleanup_orphaned_app_reflections.py --ids-file orphans.txt
    PYTHONPATH=. python scripts/cleanup_orphaned_app_reflections.py --ids-file orphans.txt --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_reflection_ids(text: str) -> list[str]:
    """Accept one id per line. Ignore blanks and # comments."""
    ids: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            ids.append(line)
    return ids


async def count_matching_rows(pool, reflection_ids: list[str]) -> int:
    if not reflection_ids:
        return 0
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT count(*)::bigint
            FROM app_reflections
            WHERE reflection_id = ANY($1::text[])
            """,
            reflection_ids,
        )
    return int(value or 0)


async def delete_matching_rows(pool, reflection_ids: list[str]) -> int:
    if not reflection_ids:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM app_reflections
            WHERE reflection_id = ANY($1::text[])
            """,
            reflection_ids,
        )
    if result and result.split()[-1].isdigit():
        return int(result.split()[-1])
    return 0


async def run(*, ids_file: Path, apply: bool) -> int:
    import asyncpg

    import config

    reflection_ids = parse_reflection_ids(ids_file.read_text(encoding="utf-8"))
    logger.info("Loaded %s reflection_id values from %s", len(reflection_ids), ids_file)

    if not config.DATABASE_URL:
        raise SystemExit("DATABASE_URL is required (Railway Alphapy Postgres)")

    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=2)
    try:
        matched = await count_matching_rows(pool, reflection_ids)
        logger.info("Railway app_reflections matching ids: %s", matched)
        if not apply:
            logger.info("Dry-run only. Pass --apply to DELETE those rows.")
            return matched
        deleted = await delete_matching_rows(pool, reflection_ids)
        logger.info("Deleted Railway app_reflections rows: %s", deleted)
        return deleted
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ids-file",
        type=Path,
        required=True,
        help="Text file of reflection_id values (one per line). No journal content.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually DELETE matching Railway rows. Default is dry-run.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.ids_file.is_file():
        raise SystemExit(f"ids file not found: {args.ids_file}")
    asyncio.run(run(ids_file=args.ids_file, apply=args.apply))


if __name__ == "__main__":
    main()
