"""Typed Grok / xAI failure classification for Alphapy."""

from __future__ import annotations

from enum import Enum
from typing import Any


class GrokFailureKind(str, Enum):
    """Shared taxonomy — mirrors App `/api/gpt` codes where applicable."""

    RATE_LIMITED = "rate_limited"
    OFFLINE = "offline"
    QUOTA = "quota"


class GrokUnavailableError(Exception):
    """Raised when Grok cannot produce a real model reply.

    Callers must show ``user_message`` and must not persist it as model output.
    ``operator_detail`` is for logs / ``/gptstatus`` only.
    """

    def __init__(
        self,
        kind: GrokFailureKind,
        *,
        operator_detail: str,
        user_message: str,
    ) -> None:
        self.kind = kind
        self.operator_detail = operator_detail
        self.user_message = user_message
        super().__init__(f"{kind.value}:{operator_detail}")


def _status_code_from_exc(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def classify_grok_error(exc: BaseException | None = None, *, message: str = "") -> tuple[GrokFailureKind, str]:
    """Map an exception or error text to (kind, short operator_detail)."""
    text = (message or (str(exc) if exc else "")).lower()
    status = _status_code_from_exc(exc) if exc else None

    if exc is not None and isinstance(exc, RuntimeError):
        if "api_key" in text or "missing" in text:
            return GrokFailureKind.OFFLINE, "missing_key"

    if status == 429 or "rate limit" in text or "ratelimit" in text or " 429" in f" {text}":
        return GrokFailureKind.RATE_LIMITED, "rate_limit"

    if status == 401 or "unauthorized" in text or "invalid api key" in text or "incorrect api key" in text:
        return GrokFailureKind.OFFLINE, "unauthorized"

    if status == 402 or "credit" in text:
        return GrokFailureKind.OFFLINE, "credits"

    if status == 403 and "credit" in text:
        return GrokFailureKind.OFFLINE, "credits"

    if status in {500, 502, 503, 504}:
        return GrokFailureKind.OFFLINE, f"upstream_{status}"

    if "timeout" in text or "connection" in text or "network" in text:
        return GrokFailureKind.OFFLINE, "network"

    if status is not None:
        return GrokFailureKind.OFFLINE, f"upstream_{status}"

    return GrokFailureKind.OFFLINE, "unknown"


def should_enqueue_retry(kind: GrokFailureKind) -> bool:
    """Silent background retry only for rate limits (plan v1)."""
    return kind is GrokFailureKind.RATE_LIMITED


def grok_user_message(exc: BaseException) -> str:
    """Safe user-facing copy for any Grok failure (never leaks ops details)."""
    from utils.user_messages import ERR_GROK_OFFLINE

    if isinstance(exc, GrokUnavailableError):
        return exc.user_message
    return ERR_GROK_OFFLINE


def classify_from_http(status: int, body: Any = None) -> tuple[GrokFailureKind, str]:
    """Classify from an HTTP status + optional JSON/text body (App-compatible helper)."""
    text = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = str(err.get("message") or err.get("code") or "")
        else:
            text = str(err or body.get("message") or "")
    elif body is not None:
        text = str(body)

    class _Fake:
        status_code = status

    return classify_grok_error(_Fake(), message=text)
