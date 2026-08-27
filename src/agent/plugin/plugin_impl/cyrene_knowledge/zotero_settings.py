"""Persisted Zotero settings owned by the Knowledge Plugin."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from cyrene.runtime import config_store

DEFAULT_ZOTERO_URL = "http://127.0.0.1:23119/api"


def _clean_url(value: Any, *, default: str = "") -> str:
    raw = str(value or default).strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not contain a query or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_zotero(raw: Any = None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    base_url = _clean_url(source.get("base_url"), default=DEFAULT_ZOTERO_URL)
    if base_url not in {
        "http://127.0.0.1:23119/api",
        "http://localhost:23119/api",
    }:
        raise ValueError(
            "Zotero Local API URL must be http://localhost:23119/api "
            "or http://127.0.0.1:23119/api"
        )
    return {
        "base_url": base_url,
        "auto_sync": bool(source.get("auto_sync", False)),
        "copy_attachments": bool(source.get("copy_attachments", True)),
    }


def get_zotero_settings() -> dict[str, Any]:
    return normalize_zotero(config_store.get_setting("zotero", {}))


def public_settings() -> dict[str, Any]:
    return {"zotero": get_zotero_settings()}


def update_settings(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("settings payload must be an object")
    incoming = payload.get("zotero")
    if not isinstance(incoming, dict):
        raise ValueError("zotero settings must be an object")
    current = get_zotero_settings()
    config_store.set_setting("zotero", normalize_zotero({**current, **incoming}))
    return public_settings()


def merged_test_config(service: str, draft: Any = None) -> dict[str, Any]:
    if service != "zotero":
        raise ValueError("unknown integration service")
    source = draft if isinstance(draft, dict) else {}
    return normalize_zotero({**get_zotero_settings(), **source})


async def test_zotero(config: dict[str, Any]) -> dict[str, Any]:
    url = config["base_url"].rstrip("/") + "/users/0/items"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params={"limit": 1, "format": "json"},
            headers={"Zotero-API-Version": "3"},
            timeout=8.0,
        )
        response.raise_for_status()
        data = response.json()
    return {
        "ok": True,
        "service": "zotero",
        "reachable": True,
        "sample_items": len(data) if isinstance(data, list) else 0,
    }


__all__ = [
    "DEFAULT_ZOTERO_URL",
    "get_zotero_settings",
    "merged_test_config",
    "normalize_zotero",
    "public_settings",
    "test_zotero",
    "update_settings",
]
