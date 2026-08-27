"""Safe model catalogs and best-effort provider model discovery.

The settings UI must remain useful when a provider is unconfigured, offline, or
does not expose a model-list API.  Every response therefore starts with a small
curated catalog.  Account discovery only annotates and extends that catalog;
its failures never make the settings endpoint fail or expose upstream details.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx


_DISCOVERABLE_PROVIDERS = frozenset({"openai", "minimax", "google"})
SUPPORTED_MODEL_PROVIDERS = frozenset(
    {*_DISCOVERABLE_PROVIDERS, "seedream", "seedance"}
)

_MAX_PAGES = 5
_MAX_DISCOVERED_MODELS = 250
_MODEL_ID_LIMIT = 240
_DISCOVERY_TIMEOUT_SECONDS = 6.0


def _item(
    model_id: str,
    label: str,
    *kinds: str,
    recommended: bool = False,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": label,
        # ``name`` keeps the response friendly to generic selector components
        # that use name/value terminology rather than label/id.
        "name": label,
        "kinds": list(kinds),
        "recommended": recommended,
        "configured": False,
        "source": "catalog",
        "verified": False,
    }


# These are deliberately conservative: only current, documented identifiers
# supported by Cyrene's corresponding adapter are offered.  A saved custom
# identifier is appended separately so upgrades never discard user choices.
MODEL_CATALOG: dict[str, tuple[dict[str, Any], ...]] = {
    "openai": (
        _item("gpt-image-2", "GPT Image 2", "image", recommended=True),
    ),
    "seedream": (
        _item(
            "doubao-seedream-5-0-260128",
            "Seedream 5.0",
            "image",
            recommended=True,
        ),
        _item(
            "doubao-seedream-5-0-lite-260128",
            "Seedream 5.0 Lite",
            "image",
        ),
    ),
    "seedance": (
        _item(
            "doubao-seedance-2-0-260128",
            "Seedance 2.0",
            "video",
            recommended=True,
        ),
        _item(
            "doubao-seedance-2-0-fast-260128",
            "Seedance 2.0 Fast",
            "video",
        ),
    ),
    "minimax": (
        _item("MiniMax-H3", "MiniMax H3", "video", recommended=True),
        _item("MiniMax-Hailuo-2.3", "Hailuo 2.3", "video"),
        _item("MiniMax-Hailuo-2.3-Fast", "Hailuo 2.3 Fast", "video"),
        _item("MiniMax-Hailuo-02", "Hailuo 02", "video"),
        _item("music-3.0", "Music 3.0", "music", recommended=True),
        _item("music-2.6", "Music 2.6", "music"),
    ),
    "google": (
        _item(
            "gemini-3.1-flash-image",
            "Gemini 3.1 Flash Image",
            "image",
            recommended=True,
        ),
        _item(
            "gemini-3.1-flash-lite-image",
            "Gemini 3.1 Flash Lite Image",
            "image",
        ),
        _item("gemini-3-pro-image", "Gemini 3 Pro Image", "image"),
        _item(
            "gemini-omni-flash",
            "Gemini Omni Flash",
            "video",
            recommended=True,
        ),
        _item(
            "gemini-omni-flash-preview",
            "Gemini Omni Flash Preview",
            "video",
        ),
        _item("veo-3.1-generate-preview", "Veo 3.1", "video"),
        _item("veo-3.1-fast-generate-preview", "Veo 3.1 Fast", "video"),
        _item("veo-3.0-generate-001", "Veo 3", "video"),
        _item("veo-3.0-fast-generate-001", "Veo 3 Fast", "video"),
        _item("veo-2.0-generate-001", "Veo 2", "video"),
    ),
}


def static_model_catalog(provider: str) -> list[dict[str, Any]]:
    """Return a detached curated catalog for a supported provider."""

    normalized = str(provider or "").strip().lower()
    return [deepcopy(item) for item in MODEL_CATALOG.get(normalized, ())]


def _safe_model_id(value: Any, *, api_key: str = "") -> str:
    """Normalize one upstream identifier without reflecting credential data."""

    candidate = str(value or "").strip()
    if candidate.startswith("models/"):
        candidate = candidate.removeprefix("models/")
    if not candidate or len(candidate) > _MODEL_ID_LIMIT:
        return ""
    if any(ord(char) < 32 or char.isspace() for char in candidate):
        return ""
    # Model names used by the supported APIs are intentionally narrow.  This
    # also keeps an untrusted compatible gateway from injecting display markup.
    if any(
        not (
            char.isascii()
            and (char.isalnum() or char in {"-", "_", ".", ":", "/"})
        )
        for char in candidate
    ):
        return ""
    secret = str(api_key or "").strip()
    if secret and secret in candidate:
        return ""
    return candidate


def _payload_entries(payload: dict[str, Any]) -> list[Any]:
    for key in ("data", "models", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested_key in ("data", "models", "items"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested
    return []


def _entry_id(entry: Any, *, api_key: str) -> str:
    if isinstance(entry, str):
        return _safe_model_id(entry, api_key=api_key)
    if not isinstance(entry, dict):
        return ""
    for key in ("id", "name", "model", "model_name"):
        model_id = _safe_model_id(entry.get(key), api_key=api_key)
        if model_id:
            return model_id
    return ""


def _raise_for_payload_error(payload: dict[str, Any]) -> None:
    if payload.get("error"):
        raise ValueError("provider returned an error payload")
    base_response = payload.get("base_resp")
    if isinstance(base_response, dict):
        status = base_response.get("status_code", 0)
        if status not in (None, "", 0, "0"):
            raise ValueError("provider returned an error status")


def _next_page_token(
    payload: dict[str, Any],
    entries: list[Any],
    *,
    api_key: str,
) -> str:
    for source in (payload, payload.get("data")):
        if not isinstance(source, dict):
            continue
        for key in (
            "nextPageToken",
            "next_page_token",
            "next_cursor",
            "nextCursor",
        ):
            token = source.get(key)
            if isinstance(token, str) and token.strip():
                normalized = token.strip()
                if api_key and api_key in normalized:
                    return ""
                return normalized if len(normalized) <= 1024 else ""
        if source.get("has_more") is True and entries:
            last_id = _entry_id(entries[-1], api_key=api_key)
            if last_id:
                return last_id
    return ""


def _openai_kinds(model_id: str) -> set[str]:
    return {"image"} if model_id.lower().startswith("gpt-image-") else set()


def _minimax_kinds(model_id: str) -> set[str]:
    normalized = model_id.casefold()
    if normalized == "minimax-h3" or normalized.startswith("minimax-hailuo-"):
        return {"video"}
    if normalized in {"music-3.0", "music-2.6"}:
        return {"music"}
    return set()


def _google_kinds(model_id: str) -> set[str]:
    normalized = model_id.casefold()
    if normalized.startswith("gemini-") and "omni" in normalized:
        return {"video"}
    if normalized.startswith("gemini-") and "image" in normalized:
        return {"image"}
    if normalized.startswith("veo-"):
        return {"video"}
    return set()


def _normalized_base(base_url: Any, fallback: str) -> str:
    return str(base_url or fallback).strip().rstrip("/")


def _models_url(provider: str, settings: dict[str, Any]) -> str:
    if provider == "openai":
        base = _normalized_base(settings.get("base_url"), "https://api.openai.com/v1")
        return f"{base}/models"
    if provider == "minimax":
        base = _normalized_base(settings.get("base_url"), "https://api.minimax.io")
        for suffix in ("/v1", "/v2"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base}/v1/models"
    if provider == "google":
        return "https://generativelanguage.googleapis.com/v1beta/models"
    raise ValueError("provider does not support account discovery")


async def _discover_live_models(
    provider: str,
    settings: dict[str, Any],
) -> dict[str, set[str]]:
    api_key = str(settings.get("api_key") or "").strip()
    url = _models_url(provider, settings)
    headers = (
        {"x-goog-api-key": api_key}
        if provider == "google"
        else {"Authorization": f"Bearer {api_key}"}
    )
    kind_resolver = {
        "openai": _openai_kinds,
        "minimax": _minimax_kinds,
        "google": _google_kinds,
    }[provider]
    found: dict[str, set[str]] = {}
    page_token = ""
    seen_tokens: set[str] = set()
    timeout = httpx.Timeout(
        _DISCOVERY_TIMEOUT_SECONDS,
        connect=min(3.0, _DISCOVERY_TIMEOUT_SECONDS),
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for _page in range(_MAX_PAGES):
            if provider == "google":
                params: dict[str, Any] = {"pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token
            else:
                params = {"limit": 100}
                if page_token:
                    params["after"] = page_token
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("provider model response must be an object")
            _raise_for_payload_error(payload)
            entries = _payload_entries(payload)
            for entry in entries:
                model_id = _entry_id(entry, api_key=api_key)
                kinds = kind_resolver(model_id) if model_id else set()
                if kinds:
                    found.setdefault(model_id, set()).update(kinds)
                if len(found) >= _MAX_DISCOVERED_MODELS:
                    return found
            next_token = _next_page_token(payload, entries, api_key=api_key)
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            page_token = next_token
    return found


def _configured_models(
    provider_settings: dict[str, Any],
    *,
    api_key: str,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for kind in ("image", "video", "music"):
        model_id = _safe_model_id(
            provider_settings.get(f"{kind}_model"),
            api_key=api_key,
        )
        if model_id:
            result.setdefault(model_id, set()).add(kind)
    return result


def _merge_catalog(
    provider: str,
    provider_settings: dict[str, Any],
    discovered: dict[str, set[str]],
    *,
    status: str,
) -> list[dict[str, Any]]:
    api_key = str(provider_settings.get("api_key") or "").strip()
    configured = _configured_models(provider_settings, api_key=api_key)
    items = static_model_catalog(provider)
    by_id = {str(item["id"]).casefold(): item for item in items}

    for model_id in sorted(discovered, key=str.casefold):
        kinds = sorted(discovered[model_id])
        lookup_id = model_id.casefold()
        existing = by_id.get(lookup_id)
        if existing is None:
            existing = _item(model_id, model_id, *kinds)
            existing["source"] = "live"
            items.append(existing)
            by_id[lookup_id] = existing
        else:
            existing["kinds"] = sorted(set(existing["kinds"]).union(kinds))
        existing["verified"] = True

    for model_id, kinds in configured.items():
        lookup_id = model_id.casefold()
        existing = by_id.get(lookup_id)
        if existing is None:
            existing = _item(model_id, model_id, *sorted(kinds))
            existing["source"] = "configured"
            items.append(existing)
            by_id[lookup_id] = existing
        else:
            existing["kinds"] = sorted(set(existing["kinds"]).union(kinds))
        existing["configured"] = True

    for item in items:
        item["available"] = (
            bool(item["verified"]) if status == "verified" else None
        )
    return items


async def provider_model_catalog(
    provider: str,
    media_settings: dict[str, Any],
) -> dict[str, Any]:
    """Build one provider response without exposing credentials or failures."""

    normalized = str(provider or "").strip().lower()
    if normalized not in SUPPORTED_MODEL_PROVIDERS:
        raise KeyError(normalized)
    providers = media_settings.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    raw_provider_settings = providers.get(normalized)
    provider_settings = (
        dict(raw_provider_settings) if isinstance(raw_provider_settings, dict) else {}
    )
    api_key = str(provider_settings.get("api_key") or "").strip()
    discovered: dict[str, set[str]] = {}
    if normalized not in _DISCOVERABLE_PROVIDERS:
        status = "catalog_only"
    elif not api_key:
        status = "missing_key"
    else:
        try:
            discovered = await _discover_live_models(normalized, provider_settings)
        except Exception:
            # The route is intentionally a resilient settings aid.  Do not
            # reflect exception text, URLs, response bodies, headers, or keys.
            status = "failed"
        else:
            status = "verified"
    return {
        "provider": normalized,
        "status": status,
        "models": _merge_catalog(
            normalized,
            provider_settings,
            discovered,
            status=status,
        ),
    }


__all__ = [
    "MODEL_CATALOG",
    "SUPPORTED_MODEL_PROVIDERS",
    "provider_model_catalog",
    "static_model_catalog",
]
