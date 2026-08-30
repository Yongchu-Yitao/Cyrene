from __future__ import annotations

import re
from typing import Any

from cyrene.platform.settings_store import get as get_setting

_SENSITIVE_KEYS = {
    "token", "key", "secret", "password", "authorization", "cookie",
    "api_key", "access_token", "refresh_token",
}

_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")
_PASSWORD_ASSIGNMENT_RE = re.compile(
    r"(?P<label>\b(?:password|passwd|passphrase)\b|密码|口令)"
    r"(?P<separator>\s*(?:是|为|[:=])\s*)"
    r"(?P<secret>[^\s,，;；]+)",
    re.IGNORECASE,
)


def redact_secrets_enabled() -> bool:
    return bool(get_setting("redact_secrets", True))


def redact_text(text: Any) -> Any:
    if not isinstance(text, str) or not redact_secrets_enabled():
        return text
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", text)
    redacted = _SK_RE.sub("[REDACTED_API_KEY]", redacted)
    redacted = _PASSWORD_ASSIGNMENT_RE.sub(
        lambda match: (
            match.group("label") + match.group("separator") + "[REDACTED_PASSWORD]"
        ),
        redacted,
    )
    return redacted


def redact_value(value: Any) -> Any:
    if not redact_secrets_enabled():
        return value
    if isinstance(value, dict):
        sensitive_text = bool(value.get("sensitive"))
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lower = key_text.lower()
            if sensitive_text and lower in {"text", "command"}:
                result[key_text] = "[REDACTED_SENSITIVE_INPUT]"
            elif lower in _SENSITIVE_KEYS or any(token in lower for token in _SENSITIVE_KEYS):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return redact_text(value)
