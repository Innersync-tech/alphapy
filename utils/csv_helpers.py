"""
CSV export utilities for consistent file generation and Discord file handling.
"""
import csv
import io
from typing import Any

import discord


def create_csv_buffer(rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> io.StringIO:
    """Create a CSV buffer from database rows."""
    if not fieldnames:
        fieldnames = list(rows[0].keys()) if rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return buf


def create_discord_file_from_buffer(buf: io.StringIO, filename: str) -> discord.File:
    """Create a Discord file from a CSV buffer."""
    buf.seek(0)
    content = buf.getvalue()
    buf.close()

    # Create bytes buffer for Discord
    bytes_buf = io.BytesIO(content.encode('utf-8'))
    return discord.File(bytes_buf, filename=filename)