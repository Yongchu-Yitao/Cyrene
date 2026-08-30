"""Encrypted media provider and execution settings."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any
from urllib.parse import urlparse

from cyrene.platform.settings_store import get as get_setting, update_atomic


PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "comfyui": {
        "enabled": False,
        "mcp_server": "comfyui",
        "mode": "local",
        "submit_tool": "run_workflow",
        "status_tool": "job",
        "output_tool": "fetch_outputs",
        "upload_tool": "upload_file",
        "confirm_spend": False,
        "image_workflow": "",
        "video_workflow": "",
        "music_workflow": "",
    },
    "openai": {
        "enabled": False,
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "image_model": "gpt-image-2",
    },
    "seedream": {
        "enabled": False,
        "api_key": "",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "image_model": "doubao-seedream-5-0-260128",
    },
    "seedance": {
        "enabled": False,
        "api_key": "",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "video_model": "doubao-seedance-2-0-260128",
    },
    "minimax": {
        "enabled": False,
        "api_key": "",
        "base_url": "https://api.minimax.io",
        "video_model": "MiniMax-H3",
        "music_model": "music-3.0",
    },
    "google": {
        "enabled": False,
        "api_key": "",
        "image_model": "gemini-3.1-flash-image",
        "video_model": "gemini-omni-flash-preview",
    },
}

# Provider configuration is persisted as an encrypted settings object, but it
# still crosses public and backup boundaries. Keep the accepted schema closed:
# arbitrary nested values are otherwise an easy place to accidentally persist
# credentials that the public redaction code does not know about.
PROVIDER_FIELDS: dict[str, frozenset[str]] = {
    "comfyui": frozenset(
        {
            "enabled",
            "mcp_server",
            "mode",
            "submit_tool",
            "status_tool",
            "output_tool",
            "upload_tool",
            "confirm_spend",
            "image_workflow",
            "video_workflow",
            "music_workflow",
            "job_id_argument",
            "workflow_argument",
            "request_timeout_seconds",
            "generation_timeout_seconds",
            "poll_interval_seconds",
            "output_timeout_seconds",
        }
    ),
    "openai": frozenset(
        {
            "enabled",
            "api_key",
            "base_url",
            "image_model",
            "timeout_seconds",
        }
    ),
    "seedream": frozenset(
        {
            "enabled",
            "api_key",
            "base_url",
            "image_model",
            "timeout_seconds",
        }
    ),
    "seedance": frozenset(
        {
            "enabled",
            "api_key",
            "base_url",
            "video_model",
            "request_timeout_seconds",
            "generation_timeout_seconds",
            "poll_interval_seconds",
        }
    ),
    "minimax": frozenset(
        {
            "enabled",
            "api_key",
            "base_url",
            "video_model",
            "music_model",
            "request_timeout_seconds",
            "generation_timeout_seconds",
            "poll_interval_seconds",
            "music_timeout_seconds",
        }
    ),
    "google": frozenset(
        {
            "enabled",
            "api_key",
            "image_model",
            "video_model",
            "request_timeout_seconds",
            "upload_timeout_seconds",
            "generation_timeout_seconds",
            "poll_interval_seconds",
        }
    ),
}

_TOP_LEVEL_FIELDS = frozenset(
    {
        "version",
        "max_parallel_jobs",
        "max_attempts",
        "poll_interval_seconds",
        "max_download_mb",
        "default_providers",
        "providers",
    }
)
_PROVIDER_WRITE_ONLY_FIELDS = frozenset({"clear_api_key", "api_key_configured"})
_PROVIDER_BOOLEAN_FIELDS = frozenset({"enabled", "confirm_spend"})
_PROVIDER_NUMBER_FIELDS = frozenset(
    {
        "timeout_seconds",
        "request_timeout_seconds",
        "upload_timeout_seconds",
        "generation_timeout_seconds",
        "poll_interval_seconds",
        "output_timeout_seconds",
        "music_timeout_seconds",
    }
)
_PROVIDER_STRING_FIELDS = frozenset(
    {
        "api_key",
        "base_url",
        "image_model",
        "video_model",
        "music_model",
        "mcp_server",
        "mode",
        "submit_tool",
        "status_tool",
        "output_tool",
        "upload_tool",
        "image_workflow",
        "video_workflow",
        "music_workflow",
        "job_id_argument",
        "workflow_argument",
    }
)
_SENSITIVE_NAME_PARTS = frozenset({"secret", "token", "key", "authorization"})
_NON_SECRET_STATE_SUFFIXES = ("_configured", "_requires_reentry")

MEDIA_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "max_parallel_jobs": 3,
    "max_attempts": 2,
    "poll_interval_seconds": 3.0,
    "max_download_mb": 256,
    "default_providers": {"image": "auto", "video": "auto", "music": "auto"},
    "providers": PROVIDER_DEFAULTS,
}


def _merge(base: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _provider_config(name: str, raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    allowed = PROVIDER_FIELDS[name]
    filtered: dict[str, Any] = {}
    for key, value in source.items():
        if key not in allowed:
            continue
        if key in _PROVIDER_BOOLEAN_FIELDS and not isinstance(value, bool):
            continue
        if key in _PROVIDER_NUMBER_FIELDS and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))):
            continue
        if key in _PROVIDER_STRING_FIELDS and not isinstance(value, str):
            continue
        filtered[key] = value
    return _merge(
        PROVIDER_DEFAULTS[name],
        filtered,
    )


def _normalized_view(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    result = _merge(
        MEDIA_DEFAULTS,
        {key: value for key, value in source.items() if key in _TOP_LEVEL_FIELDS and key != "providers"},
    )
    providers = source.get("providers") if isinstance(source.get("providers"), dict) else {}
    result["providers"] = {name: _provider_config(name, providers.get(name)) for name in PROVIDER_DEFAULTS}
    return result


def _is_sensitive_name(value: Any) -> bool:
    raw = str(value or "")
    lowered = raw.lower()
    if lowered.endswith(_NON_SECRET_STATE_SUFFIXES):
        return False
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw).lower()
    parts = {part for part in re.split(r"[^a-z0-9]+", snake_case) if part}
    return bool(parts.intersection(_SENSITIVE_NAME_PARTS)) or lowered in {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "authorizationheader",
    }


def redact_media_secrets(
    value: Any,
    *,
    replacement: Any = "[REDACTED]",
) -> Any:
    """Return a detached tree with nested credential-shaped fields redacted.

    This is deliberately independent from the global logging-redaction toggle:
    HTTP and portable-backup boundaries must never expose stored credentials.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = deepcopy(replacement) if _is_sensitive_name(key_text) else redact_media_secrets(item, replacement=replacement)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_media_secrets(item, replacement=replacement) for item in value]
    return deepcopy(value)


def get_media_settings() -> dict[str, Any]:
    raw = get_setting("media", {})
    return _normalized_view(raw)


def save_media_settings(value: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
    normalized = _normalized_view(value)
    normalized["version"] = 1
    normalized["max_parallel_jobs"] = max(1, min(int(normalized.get("max_parallel_jobs") or 3), 8))
    normalized["max_attempts"] = max(1, min(int(normalized.get("max_attempts") or 2), 5))
    normalized["poll_interval_seconds"] = max(1.0, min(float(normalized.get("poll_interval_seconds") or 3), 30.0))
    normalized["max_download_mb"] = max(
        10,
        min(int(normalized.get("max_download_mb") or 256), 1024),
    )
    defaults = normalized.get("default_providers")
    if not isinstance(defaults, dict):
        raise ValueError("default_providers must be an object")
    normalized["default_providers"] = {kind: str(defaults.get(kind) or "auto").strip().lower() or "auto" for kind in ("image", "video", "music")}
    providers = normalized.get("providers") if isinstance(normalized.get("providers"), dict) else {}
    normalized["providers"] = {name: _merge(PROVIDER_DEFAULTS[name], providers.get(name) if isinstance(providers.get(name), dict) else {}) for name in PROVIDER_DEFAULTS}
    supported = {
        "image": {"auto", "comfyui", "openai", "seedream", "google"},
        "video": {"auto", "comfyui", "seedance", "minimax", "google"},
        "music": {"auto", "comfyui", "minimax"},
    }
    for kind, provider in normalized["default_providers"].items():
        if provider not in supported[kind]:
            raise ValueError(f"provider '{provider}' does not support {kind}")
    for name, provider in normalized["providers"].items():
        provider["enabled"] = provider.get("enabled") is True
        api_key = str(provider.get("api_key") or "")
        if len(api_key) > 16_384:
            raise ValueError(f"{name} api_key is too long")
        if "api_key" in PROVIDER_FIELDS[name]:
            provider["api_key"] = api_key
        base_url = str(provider.get("base_url") or "").strip()
        if base_url:
            if len(base_url) > 2048:
                raise ValueError(f"{name} base_url is too long")
            parsed = urlparse(base_url)
            loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not loopback:
                raise ValueError(f"{name} base_url must use HTTPS (loopback HTTP is allowed)")
            if not parsed.netloc or not parsed.hostname:
                raise ValueError(f"{name} base_url must contain a hostname")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(f"{name} base_url must not contain credentials")
            if parsed.query or parsed.fragment or "?" in base_url or "#" in base_url:
                raise ValueError(f"{name} base_url must not contain a query or fragment")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError(f"{name} base_url contains an invalid port") from exc
            provider["base_url"] = base_url
        for key in (
            "image_model",
            "video_model",
            "music_model",
            "mcp_server",
            "submit_tool",
            "status_tool",
            "output_tool",
            "upload_tool",
            "job_id_argument",
            "workflow_argument",
        ):
            if len(str(provider.get(key) or "")) > 240:
                raise ValueError(f"{name} {key} is too long")
        for key in ("image_workflow", "video_workflow", "music_workflow"):
            if len(str(provider.get(key) or "")) > 4096:
                raise ValueError(f"{name} {key} is too long")
        if name == "comfyui" and str(provider.get("mode") or "local") not in {
            "local",
            "cloud",
        }:
            raise ValueError("comfyui mode must be local or cloud")
        if name == "comfyui":
            provider["confirm_spend"] = provider.get("confirm_spend") is True
    update_atomic({"media": normalized}, expected_revision=expected_revision)
    return get_media_settings()


def public_media_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _normalized_view(get_media_settings() if value is None else value)
    for provider in (result.get("providers") or {}).values():
        if not isinstance(provider, dict):
            continue
        secret = str(provider.pop("api_key", "") or "")
        redacted = redact_media_secrets(provider)
        provider.clear()
        provider.update(redacted)
        provider["api_key_configured"] = bool(secret)
    result = redact_media_secrets(result)
    result["completion_behavior"] = "attach_then_wake_agent"
    return result


def merge_media_settings_update(incoming: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
    current = get_media_settings()
    payload = deepcopy(incoming if isinstance(incoming, dict) else {})
    raw_providers = payload.get("providers")
    if raw_providers is not None and not isinstance(raw_providers, dict):
        raise ValueError("providers must be an object")
    incoming_providers = raw_providers if isinstance(raw_providers, dict) else {}
    unknown_providers = sorted(set(incoming_providers) - set(PROVIDER_DEFAULTS))
    if unknown_providers:
        raise ValueError(f"unsupported media provider: {unknown_providers[0]}")
    merged_providers = deepcopy(current.get("providers") or {})
    for name in PROVIDER_DEFAULTS:
        update = incoming_providers.get(name)
        if not isinstance(update, dict):
            if update is not None:
                raise ValueError(f"{name} provider settings must be an object")
            continue
        unknown_fields = sorted(set(update) - PROVIDER_FIELDS[name] - _PROVIDER_WRITE_ONLY_FIELDS)
        if unknown_fields:
            raise ValueError(f"unsupported {name} provider setting: {unknown_fields[0]}")
        for key, item in update.items():
            if key in _PROVIDER_BOOLEAN_FIELDS and not isinstance(item, bool):
                raise ValueError(f"{name} {key} must be a boolean")
            if key in _PROVIDER_NUMBER_FIELDS and (isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item))):
                raise ValueError(f"{name} {key} must be a number")
            if key in _PROVIDER_STRING_FIELDS and not isinstance(item, str):
                raise ValueError(f"{name} {key} must be a string")
        if "clear_api_key" in update and not isinstance(update["clear_api_key"], bool):
            raise ValueError(f"{name} clear_api_key must be a boolean")
        previous = merged_providers.get(name) if isinstance(merged_providers.get(name), dict) else {}
        candidate = {
            **previous,
            **{key: item for key, item in update.items() if key in PROVIDER_FIELDS[name]},
        }
        draft_key = str(update.get("api_key") or "").strip()
        if draft_key:
            candidate["api_key"] = draft_key
        elif bool(update.get("clear_api_key")):
            candidate["api_key"] = ""
        else:
            candidate["api_key"] = str(previous.get("api_key") or "")
        candidate.pop("api_key_configured", None)
        candidate.pop("clear_api_key", None)
        merged_providers[name] = candidate
    payload["providers"] = merged_providers
    return save_media_settings(
        _merge(current, payload),
        expected_revision=expected_revision,
    )


__all__ = [
    "MEDIA_DEFAULTS",
    "PROVIDER_DEFAULTS",
    "PROVIDER_FIELDS",
    "get_media_settings",
    "merge_media_settings_update",
    "public_media_settings",
    "redact_media_secrets",
    "save_media_settings",
]
