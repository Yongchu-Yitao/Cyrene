"""Shared formatting helpers for provider and transport failures."""

from __future__ import annotations

import json
import re

_PERSISTED_ERROR_BODY_MAX_CHARS = 16_384
_PERSISTED_SENSITIVE_KEYS = frozenset({
    "token", "key", "secret", "password", "authorization", "cookie",
    "api_key", "access_token", "refresh_token",
})
_PERSISTED_BEARER_RE = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b",
    re.IGNORECASE,
)
_PERSISTED_SK_RE = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")


def _redact_persisted_error_value(value):
    """Telemetry redaction is mandatory, independent of the UI preference."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).lower() for marker in _PERSISTED_SENSITIVE_KEYS)
                else _redact_persisted_error_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_persisted_error_value(item) for item in value]
    if isinstance(value, str):
        redacted = _PERSISTED_BEARER_RE.sub("Bearer [REDACTED]", value)
        return _PERSISTED_SK_RE.sub("[REDACTED_API_KEY]", redacted)
    return value


def httpx_error_body_for_persistence(
    exc: Exception,
    *,
    max_chars: int = _PERSISTED_ERROR_BODY_MAX_CHARS,
) -> tuple[str, bool]:
    """Return a redacted, bounded 4xx body suitable for durable telemetry.

    Provider validation responses are usually small JSON objects, but a proxy
    may echo request material.  Persist the useful diagnostic without allowing
    credentials or an unbounded upstream response into the runtime database.
    """
    response = getattr(exc, "response", None)
    if response is None or not 400 <= int(getattr(response, "status_code", 0) or 0) < 500:
        return "", False
    try:
        raw = str(response.text or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return "", False
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        rendered = str(_redact_persisted_error_value(raw) or "")
        rendered = re.sub(r"\s+", " ", rendered).strip()
    else:
        rendered = json.dumps(
            _redact_persisted_error_value(decoded),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    limit = max(1, int(max_chars or _PERSISTED_ERROR_BODY_MAX_CHARS))
    return rendered[:limit], len(rendered) > limit


def format_httpx_error(exc: Exception) -> str:
    """Return a compact diagnostic with request, response, and cause context."""
    parts: list[str] = [type(exc).__name__]
    detail = str(exc or "").strip()
    if detail:
        parts.append(detail)

    request = getattr(exc, "request", None)
    if request is not None:
        method = str(getattr(request, "method", "") or "").strip()
        url = str(getattr(request, "url", "") or "").strip()
        request_part = "request="
        if method:
            request_part += method
        if url:
            request_part += f" {url}" if method else url
        parts.append(request_part)

    response = getattr(exc, "response", None)
    if response is not None:
        parts.append(f"status={response.status_code}")
        try:
            body = str(response.text or "").strip()
        except Exception:
            body = ""
        if body:
            parts.append(f"body={re.sub(r'\\s+', ' ', body)[:500]}")

    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_text = str(cause or "").strip()
        if cause_text:
            parts.append(f"cause={type(cause).__name__}: {cause_text}")
        else:
            parts.append(f"cause={type(cause).__name__}")

    return " | ".join(parts)


__all__ = ["format_httpx_error", "httpx_error_body_for_persistence"]
