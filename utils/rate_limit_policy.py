"""IP write-rate-limit exemptions for inbound HMAC / service-key privacy paths."""

from __future__ import annotations


def is_write_rate_limit_exempt(path: str) -> bool:
    """Return True when a POST/PUT/DELETE must not share the 10 writes/min IP cap.

    Core's share/revoke webhooks and the service revoke API are HMAC- or
    API-key authenticated. Putting them on the generic write cap (10/min/IP)
    made Core's Railway egress drop revoke POSTs with HTTP 429, which left
    plaintext copies in ``app_reflections``.
    """
    normalized = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if normalized.startswith("/webhooks"):
        return True
    return normalized == "/api/reflections/revoke"


__all__ = ["is_write_rate_limit_exempt"]
