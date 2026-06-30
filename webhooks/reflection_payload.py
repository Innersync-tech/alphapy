"""Normalize inbound reflection share/revoke webhook payloads."""

from __future__ import annotations

from typing import Any

import asyncpg

from utils.innersync_identity import get_discord_id_for_innersync


class ReflectionWebhookPayloadError(ValueError):
    """Invalid or unlinkable reflection webhook payload."""


async def resolve_discord_user_id(
    pool: asyncpg.Pool | None,
    raw_user_id: Any,
) -> int:
    """Resolve webhook user_id to a Discord snowflake (int)."""
    if raw_user_id is None:
        raise ReflectionWebhookPayloadError("Missing user_id.")

    try:
        return int(raw_user_id)
    except (TypeError, ValueError):
        pass

    innersync_user_id = str(raw_user_id).strip()
    if not innersync_user_id:
        raise ReflectionWebhookPayloadError("Missing user_id.")

    discord_id = await get_discord_id_for_innersync(
        pool,
        innersync_user_id,
        allow_profile_fallback=False,
    )
    if discord_id is None:
        raise ReflectionWebhookPayloadError(
            "Discord account not linked for this user_id."
        )
    return int(discord_id)


def extract_plaintext_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Build plaintext_content from canonical or legacy flat share fields."""
    existing = payload.get("plaintext_content")
    if isinstance(existing, dict) and existing:
        reflection_text = (
            existing.get("reflection_text")
            or existing.get("reflection")
            or ""
        )
        if not str(reflection_text).strip():
            raise ReflectionWebhookPayloadError("plaintext_content has no reflection text.")
        return dict(existing)

    reflection = payload.get("reflection") or payload.get("reflection_text")
    if not reflection or not str(reflection).strip():
        raise ReflectionWebhookPayloadError(
            "Missing plaintext_content or reflection field."
        )

    date_val = payload.get("date", "")
    if hasattr(date_val, "isoformat"):
        date_val = date_val.isoformat()

    text = str(reflection).strip()
    return {
        "reflection_text": text,
        "reflection": text,
        "mantra": str(payload.get("mantra") or ""),
        "thoughts": str(payload.get("thoughts") or ""),
        "future_message": str(payload.get("future_message") or ""),
        "date": str(date_val) if date_val else "",
    }


__all__ = [
    "ReflectionWebhookPayloadError",
    "extract_plaintext_content",
    "resolve_discord_user_id",
]
