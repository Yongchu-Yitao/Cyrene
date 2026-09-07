"""Pure validation of the canonical connection/profile/route document.

Persistence, public secret redaction and runtime cache invalidation stay in the
configuration service. Validation preserves input order and the first error.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from cyrene.model.adapter_registry import require_adapter

CONFIG_VERSION = 12
ROUTE_NAMES = ("primary", "secondary", "vision", "embedding")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _identifier(value: Any, *, kind: str) -> str:
    result = str(value or "").strip()
    if not _ID_RE.fullmatch(result):
        raise ValueError(
            f"{kind} id must start with a letter or number and contain only "
            "letters, numbers, '.', '_', ':', or '-'"
        )
    return result


def _clean_url(value: Any, *, default: str = "") -> str:
    result = str(value or default or "").strip().rstrip("/")
    if not result:
        return ""
    parsed = urlsplit(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("connection base_url must be an absolute HTTP(S) URL")
    return result


def _context_limit(raw: dict[str, Any]) -> int:
    value: Any = raw.get("context_limit", 0)
    if isinstance(value, str):
        cleaned = value.strip().upper()
        multiplier = 1
        if cleaned.endswith("K"):
            cleaned, multiplier = cleaned[:-1], 1_000
        elif cleaned.endswith("M"):
            cleaned, multiplier = cleaned[:-1], 1_000_000
        try:
            value = int(float(cleaned) * multiplier) if cleaned else 0
        except ValueError as exc:
            raise ValueError("profile context_limit must be an integer") from exc
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("profile context_limit must be an integer") from exc
    if result < 0 or result > 16_000_000:
        raise ValueError("profile context_limit must be between 0 and 16000000")
    return result


def _capabilities(raw: dict[str, Any], adapter_id: str) -> list[str]:
    definition = require_adapter(adapter_id)
    source = raw.get("capabilities")
    if source is not None and not isinstance(source, list):
        raise ValueError("profile capabilities must be an array")
    result = {
        str(item or "").strip().lower()
        for item in (source or [])
        if str(item or "").strip()
    }
    if not result:
        # Unknown remote model capabilities should not be over-advertised. Chat
        # is the safe default; embedding-only adapters remain embedding-only.
        result.add("embedding" if definition.capabilities == ("embedding",) else "chat")
    unsupported = result - set(definition.capabilities)
    if unsupported:
        raise ValueError(
            f"adapter {adapter_id!r} does not support capabilities: "
            + ", ".join(sorted(unsupported))
        )
    return sorted(result)


def _migrate_bailian_connection(
    raw_connections: list[Any], version: Any
) -> list[Any]:
    if not isinstance(version, int) or version >= 11:
        return raw_connections
    has_bailian = any(
        isinstance(item, dict)
        and (
            str(item.get("id") or "").strip() == "aliyun_bailian"
            or str(
                (item.get("options") if isinstance(item.get("options"), dict) else {}).get("provider_preset") or ""
            ).strip().lower() == "aliyun_bailian"
        )
        for item in raw_connections
    )
    if has_bailian or len(raw_connections) >= 100:
        return raw_connections
    return [
        *raw_connections,
        {
            "id": "aliyun_bailian",
            "name": "Alibaba Cloud Model Studio",
            "adapter": "openai",
            "enabled": True,
            "use_proxy": False,
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "",
            "options": {"provider_preset": "aliyun_bailian"},
        },
    ]


def normalize_model_configuration(
    raw: Any, *, previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and detach a complete model configuration document."""

    if not isinstance(raw, dict):
        raise ValueError("model configuration must be an object")
    unknown = set(raw) - {"version", "connections", "profiles", "routes"}
    if unknown:
        raise ValueError(
            "unknown model configuration fields: " + ", ".join(sorted(unknown))
        )
    missing = {"connections", "profiles", "routes"} - set(raw)
    if missing:
        raise ValueError(
            "missing model configuration fields: " + ", ".join(sorted(missing))
        )
    version = raw.get("version")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 0
    ):
        raise ValueError("model configuration version must be a non-negative integer")
    raw_connections = raw["connections"]
    raw_profiles = raw["profiles"]
    raw_routes = raw["routes"]
    if not isinstance(raw_connections, list):
        raise ValueError("connections must be an array")
    if not isinstance(raw_profiles, list):
        raise ValueError("profiles must be an array")
    if not isinstance(raw_routes, dict):
        raise ValueError("routes must be an object")
    raw_connections = _migrate_bailian_connection(raw_connections, version)
    if len(raw_connections) > 100 or len(raw_profiles) > 1000:
        raise ValueError("model configuration is too large")

    connections = _normalize_connections(raw_connections, previous)
    profiles = _normalize_profiles(raw_profiles, connections)
    routes = _normalize_routes(raw_routes, {item["id"] for item in profiles})

    return {
        "version": CONFIG_VERSION,
        "connections": connections,
        "profiles": profiles,
        "routes": routes,
    }


def _validate_connection_fields(source):
    if not isinstance(source, dict):
        raise ValueError("each connection must be an object")
    unknown = set(source) - {
        "id",
        "name",
        "adapter",
        "enabled",
        "use_proxy",
        "base_url",
        "api_key",
        "clear_api_key",
        "options",
    }
    if unknown:
        raise ValueError(
            "unknown connection fields: " + ", ".join(sorted(unknown))
        )
    for field in ("id", "name", "adapter", "base_url", "api_key"):
        if field in source and not isinstance(source[field], str):
            raise ValueError(f"connection {field} must be a string")
    for field in ("enabled", "use_proxy", "clear_api_key"):
        if field in source and not isinstance(source[field], bool):
            raise ValueError(f"connection {field} must be a boolean")
    if "options" in source and not isinstance(source["options"], dict):
        raise ValueError("connection options must be an object")


def _validate_profile_fields(source):
    if not isinstance(source, dict):
        raise ValueError("each profile must be an object")
    unknown = set(source) - {
        "id",
        "connection_id",
        "model",
        "name",
        "enabled",
        "capabilities",
        "context_limit",
        "ctx",
        "dimensions",
        "reasoning_effort",
        "description",
        "price",
        "max_concurrency",
        "options",
    }
    if unknown:
        raise ValueError(
            "unknown profile fields: " + ", ".join(sorted(unknown))
        )
    for field in (
        "id",
        "connection_id",
        "model",
        "name",
        "ctx",
        "reasoning_effort",
        "description",
        "price",
    ):
        if field in source and not isinstance(source[field], str):
            raise ValueError(f"profile {field} must be a string")
    if "enabled" in source and not isinstance(source["enabled"], bool):
        raise ValueError("profile enabled must be a boolean")
    if "options" in source and not isinstance(source["options"], dict):
        raise ValueError("profile options must be an object")
    if "capabilities" in source and (
        not isinstance(source["capabilities"], list)
        or not all(isinstance(item, str) for item in source["capabilities"])
    ):
        raise ValueError("profile capabilities must be an array of strings")
    if isinstance(source.get("context_limit"), bool):
        raise ValueError("profile context_limit must be an integer")
    for field in ("dimensions", "max_concurrency"):
        if field in source and (
            not isinstance(source[field], int) or isinstance(source[field], bool)
        ):
            raise ValueError(f"profile {field} must be an integer")


def _normalize_connection(source, connection_id, previous_connections):
    adapter_id = str(source.get("adapter") or "").strip().lower()
    if not adapter_id:
        raise ValueError(f"connection {connection_id!r} requires an adapter")
    definition = require_adapter(adapter_id)
    if adapter_id in {"codex_oauth", "local_onnx"}:
        base_url = definition.default_base_url if adapter_id == "codex_oauth" else ""
    else:
        base_url = _clean_url(
            source.get("base_url"), default=definition.default_base_url
        )
    previous_secret = str(
        (previous_connections.get(connection_id) or {}).get("api_key") or ""
    ).strip()
    submitted_secret = str(source.get("api_key") or "").strip()
    if source.get("clear_api_key") is True:
        api_key = ""
    elif submitted_secret:
        api_key = submitted_secret
    else:
        api_key = previous_secret
    if definition.auth_type != "api_key":
        api_key = ""
    options = source.get("options") if isinstance(source.get("options"), dict) else {}
    return {
        "id": connection_id,
        "name": str(source.get("name") or definition.label).strip() or definition.label,
        "adapter": adapter_id,
        "enabled": source.get("enabled") is not False,
        "use_proxy": source.get("use_proxy") is True,
        "base_url": base_url,
        "api_key": api_key,
        "options": deepcopy(options),
    }



def _normalize_connections(raw_connections, previous):
    previous_connections = {
        str(item.get("id") or ""): item
        for item in ((previous or {}).get("connections") or [])
        if isinstance(item, dict)
    }
    connections: list[dict[str, Any]] = []
    connection_ids: set[str] = set()
    for source in raw_connections:
        _validate_connection_fields(source)
        connection_id = _identifier(source.get("id"), kind="connection")
        if connection_id in connection_ids:
            raise ValueError(f"duplicate connection id: {connection_id}")
        connection_ids.add(connection_id)
        connections.append(_normalize_connection(source, connection_id, previous_connections))
    return connections


def _normalize_profile(source, profile_id, connection_by_id):
    connection_id = _identifier(source.get("connection_id"), kind="connection")
    connection = connection_by_id.get(connection_id)
    if connection is None:
        raise ValueError(
            f"profile {profile_id!r} references unknown connection {connection_id!r}"
        )
    model = str(source.get("model") or "").strip()
    if not model:
        raise ValueError(f"profile {profile_id!r} requires a model")
    limit = _context_limit(source)
    try:
        dimensions = int(source.get("dimensions") or 0)
        max_concurrency = int(source.get("max_concurrency") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("profile numeric fields must be integers") from exc
    if dimensions < 0 or dimensions > 65_536:
        raise ValueError("profile dimensions must be between 0 and 65536")
    if max_concurrency < 0 or max_concurrency > 10_000:
        raise ValueError("profile max_concurrency must be between 0 and 10000")
    return {
        "id": profile_id,
        "connection_id": connection_id,
        "model": model,
        "name": str(source.get("name") or model).strip() or model,
        "enabled": source.get("enabled") is not False,
        "capabilities": _capabilities(source, connection["adapter"]),
        "context_limit": limit,
        "ctx": str(source.get("ctx") or (limit if limit else "")).strip(),
        "dimensions": dimensions,
        "reasoning_effort": str(source.get("reasoning_effort") or "").strip().lower(),
        "description": str(source.get("description") or "").strip(),
        "price": str(source.get("price") or "").strip(),
        "max_concurrency": max_concurrency,
        "options": deepcopy(source.get("options")) if isinstance(source.get("options"), dict) else {},
    }



def _normalize_profiles(raw_profiles, connections):
    connection_by_id = {item["id"]: item for item in connections}
    profiles: list[dict[str, Any]] = []
    profile_ids: set[str] = set()
    for source in raw_profiles:
        _validate_profile_fields(source)
        profile_id = _identifier(source.get("id"), kind="profile")
        if profile_id in profile_ids:
            raise ValueError(f"duplicate profile id: {profile_id}")
        profile_ids.add(profile_id)
        profiles.append(_normalize_profile(source, profile_id, connection_by_id))
    return profiles


def _normalize_routes(raw_routes, profile_ids):
    routes: dict[str, list[str]] = {}
    unknown_routes = set(raw_routes) - set(ROUTE_NAMES)
    if unknown_routes:
        raise ValueError("unknown model routes: " + ", ".join(sorted(unknown_routes)))
    for route_name in ROUTE_NAMES:
        if route_name not in raw_routes:
            raise ValueError(f"missing model route: {route_name}")
        value = raw_routes[route_name]
        if not isinstance(value, list):
            raise ValueError(f"route {route_name!r} must be an array")
        route: list[str] = []
        for raw_id in value:
            if not isinstance(raw_id, str):
                raise ValueError(f"route {route_name!r} entries must be strings")
            profile_id = str(raw_id or "").strip()
            if profile_id not in profile_ids:
                raise ValueError(
                    f"route {route_name!r} references unknown profile {profile_id!r}"
                )
            if profile_id not in route:
                route.append(profile_id)
        routes[route_name] = route
    return routes
