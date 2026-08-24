"""Persisted settings and safe connectivity probes for local integrations.

The public payload deliberately never contains the stored embedding API key.
Callers may submit an empty key to keep the existing secret, or explicitly set
``clear_api_key`` when they intend to remove it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from cyrene.runtime import config_store


DEFAULT_ZOTERO_URL = "http://127.0.0.1:23119/api"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
EMBEDDING_PROVIDERS = frozenset({"openai_compatible", "ollama", "local_onnx"})


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


def normalize_embedding(raw: Any = None, *, include_legacy: bool = True) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    provider = str(source.get("provider") or "openai_compatible").strip().lower().replace("-", "_")
    if provider not in EMBEDDING_PROVIDERS:
        raise ValueError("unsupported embedding provider")

    legacy_base_url = config_store.get_env("EMBEDDING_BASE_URL", "") if include_legacy else ""
    legacy_api_key = config_store.get_env("EMBEDDING_API_KEY", "") if include_legacy else ""
    legacy_model = config_store.get_env("EMBEDDING_MODEL", "") if include_legacy else ""
    default_base_url = DEFAULT_OLLAMA_URL if provider == "ollama" else ""

    dimension_value = source.get("dimensions", 0)
    try:
        dimensions = int(dimension_value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding dimensions must be an integer") from exc
    if dimensions < 0 or dimensions > 65_536:
        raise ValueError("embedding dimensions must be between 0 and 65536")

    return {
        "provider": provider,
        "base_url": "" if provider == "local_onnx" else _clean_url(
            source.get("base_url") or legacy_base_url,
            default=default_base_url,
        ),
        "api_key": str(source.get("api_key") or legacy_api_key or "").strip(),
        "model": str(source.get("model") or legacy_model or "").strip(),
        "dimensions": dimensions,
        "use_proxy": source.get("use_proxy") is True,
    }


def get_zotero_settings() -> dict[str, Any]:
    return normalize_zotero(config_store.get_setting("zotero", {}))


def get_embedding_settings() -> dict[str, Any]:
    return normalize_embedding(config_store.get_setting("embedding", {}))


def public_settings() -> dict[str, Any]:
    zotero = get_zotero_settings()
    embedding = get_embedding_settings()
    return {
        "zotero": zotero,
        "embedding": {
            "provider": embedding["provider"],
            "base_url": embedding["base_url"],
            "model": embedding["model"],
            "dimensions": embedding["dimensions"],
            "use_proxy": embedding["use_proxy"],
            "api_key_configured": bool(embedding["api_key"]),
        },
    }


def update_settings(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("settings payload must be an object")

    if "zotero" in payload:
        incoming = payload.get("zotero")
        if not isinstance(incoming, dict):
            raise ValueError("zotero settings must be an object")
        current = get_zotero_settings()
        merged = {**current, **incoming}
        config_store.set_setting("zotero", normalize_zotero(merged))

    if "embedding" in payload:
        incoming = payload.get("embedding")
        if not isinstance(incoming, dict):
            raise ValueError("embedding settings must be an object")
        current = get_embedding_settings()
        merged = {**current, **incoming}
        submitted_key = str(incoming.get("api_key") or "").strip()
        if submitted_key:
            merged["api_key"] = submitted_key
        elif incoming.get("clear_api_key") is True:
            merged["api_key"] = ""
        else:
            merged["api_key"] = current["api_key"]
        normalized = normalize_embedding(merged, include_legacy=False)
        config_store.set_setting("embedding", normalized)
        # Keep the legacy encrypted env slots in sync. They are still read by
        # older installations and may already have been injected into
        # ``os.environ`` during startup; updating them here makes UI changes
        # effective immediately without a restart.
        config_store.set_env_many({
            "EMBEDDING_BASE_URL": normalized["base_url"],
            "EMBEDDING_API_KEY": normalized["api_key"],
            "EMBEDDING_MODEL": normalized["model"],
        })

    return public_settings()


def merged_test_config(service: str, draft: Any = None) -> dict[str, Any]:
    source = draft if isinstance(draft, dict) else {}
    if service == "zotero":
        return normalize_zotero({**get_zotero_settings(), **source})
    if service == "embedding":
        current = get_embedding_settings()
        merged = {**current, **source}
        if not str(source.get("api_key") or "").strip():
            merged["api_key"] = current["api_key"]
        normalized = normalize_embedding(merged, include_legacy=False)
        if not normalized["model"] or (normalized["provider"] != "local_onnx" and not normalized["base_url"]):
            raise ValueError("embedding base URL and model are required")
        return normalized
    raise ValueError("unknown integration service")


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
    return {"ok": True, "service": "zotero", "reachable": True, "sample_items": len(data) if isinstance(data, list) else 0}


async def test_embedding(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("provider") == "local_onnx":
        from cyrene.knowledge import local_models

        if not local_models.is_ready("qwen3-embedding-0.6b"):
            return {
                "ok": True,
                "service": "embedding",
                "model": config["model"],
                "dimensions": 0,
                "fallback": "keyword",
            }

    from cyrene.knowledge.embedding_client import embed_texts_with_config

    vectors = await embed_texts_with_config(
        ["Cyrene connection test"],
        config,
    )
    dimensions = len(vectors[0]) if vectors else 0
    return {
        "ok": True,
        "service": "embedding",
        "model": config["model"],
        "dimensions": dimensions,
    }
