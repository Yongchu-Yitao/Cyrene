"""Normalized model connections, profiles, and independent role routes.

This module owns the single adapter-oriented model configuration used by both
the settings UI and the runtime. It accepts and persists only the canonical
connection/profile/route graph, contains no HTTP concerns, and never returns
stored secrets from its public read API.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from cyrene.model.adapter_registry import list_adapters, require_adapter
from cyrene.model.cache_invalidation import invalidate_model_runtime_caches
from cyrene.model.transcript_policy import (
    ProviderFamily,
    ProviderFamilyError,
    provider_family_for_candidate,
)
from cyrene.platform import config_store


CONFIG_VERSION = 10
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


def normalize_model_configuration(
    raw: Any,
    *,
    previous: dict[str, Any] | None = None,
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
    if len(raw_connections) > 100 or len(raw_profiles) > 1000:
        raise ValueError("model configuration is too large")

    previous_connections = {
        str(item.get("id") or ""): item
        for item in ((previous or {}).get("connections") or [])
        if isinstance(item, dict)
    }
    connections: list[dict[str, Any]] = []
    connection_ids: set[str] = set()
    for source in raw_connections:
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
        connection_id = _identifier(source.get("id"), kind="connection")
        if connection_id in connection_ids:
            raise ValueError(f"duplicate connection id: {connection_id}")
        connection_ids.add(connection_id)
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
        connections.append({
            "id": connection_id,
            "name": str(source.get("name") or definition.label).strip() or definition.label,
            "adapter": adapter_id,
            "enabled": source.get("enabled") is not False,
            "use_proxy": source.get("use_proxy") is True,
            "base_url": base_url,
            "api_key": api_key,
            "options": deepcopy(options),
        })

    connection_by_id = {item["id"]: item for item in connections}
    profiles: list[dict[str, Any]] = []
    profile_ids: set[str] = set()
    for source in raw_profiles:
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
        profile_id = _identifier(source.get("id"), kind="profile")
        if profile_id in profile_ids:
            raise ValueError(f"duplicate profile id: {profile_id}")
        profile_ids.add(profile_id)
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
        profiles.append({
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
        })

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

    return {
        "version": CONFIG_VERSION,
        "connections": connections,
        "profiles": profiles,
        "routes": routes,
    }


def _plugin_seed_configuration() -> dict[str, Any]:
    """Build the one-time initial graph from editable Model Plugin metadata."""

    from cyrene.plugins.model_catalog import model_plugin_catalog

    catalog = model_plugin_catalog()
    if not catalog:
        raise RuntimeError("no Model Provider Plugins are available to seed settings")
    connections: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    routes = {name: [] for name in ROUTE_NAMES}
    for provider in catalog:
        provider_id = _identifier(provider.get("id"), kind="provider")
        adapter = str(provider.get("adapter") or "").strip().lower()
        if not adapter:
            raise ValueError(f"model Provider Plugin {provider_id!r} has no adapter")
        connections.append({
            "id": provider_id,
            "name": str(provider.get("name") or provider_id).strip() or provider_id,
            "adapter": adapter,
            "enabled": True,
            "use_proxy": False,
            "base_url": str(provider.get("default_base_url") or "").strip(),
            "api_key": "",
            "options": {"provider_preset": provider_id},
        })
        if adapter == "local_onnx":
            profile_id = f"{provider_id}:qwen3-embedding-0.6b"
            profiles.append({
                "id": profile_id,
                "connection_id": provider_id,
                "model": "qwen3-embedding-0.6b",
                "name": "Qwen3 Embedding 0.6B",
                "enabled": True,
                "capabilities": ["embedding"],
                "dimensions": 1024,
            })
            routes["embedding"].append(profile_id)
    return normalize_model_configuration({
        "version": CONFIG_VERSION,
        "connections": connections,
        "profiles": profiles,
        "routes": routes,
    })


def get_model_configuration(*, persist_seed: bool = True) -> dict[str, Any]:
    """Read the canonical graph, seeding Plugin connections only when absent."""

    revision = config_store.get_settings_revision()
    raw = config_store.get_setting("model_configuration", None)
    if isinstance(raw, dict):
        return normalize_model_configuration(raw, previous=raw)

    seeded = _plugin_seed_configuration()
    if not persist_seed:
        return seeded
    try:
        config_store.update_settings_atomic(
            {"model_configuration": seeded},
            expected_revision=revision,
        )
    except config_store.SettingsRevisionConflict:
        latest = config_store.get_setting("model_configuration", None)
        if isinstance(latest, dict):
            return normalize_model_configuration(latest, previous=latest)
        raise
    return seeded


def candidate_for_profile(
    profile_id: str,
    configuration: dict[str, Any] | None = None,
    *,
    require_enabled: bool = True,
) -> dict[str, Any] | None:
    config = configuration or get_model_configuration()
    profile = next(
        (item for item in config["profiles"] if item["id"] == str(profile_id or "")),
        None,
    )
    if profile is None or (require_enabled and not profile.get("enabled", True)):
        return None
    connection = next(
        (item for item in config["connections"] if item["id"] == profile["connection_id"]),
        None,
    )
    if connection is None or (require_enabled and not connection.get("enabled", True)):
        return None
    adapter = str(connection.get("adapter") or "openai_compatible")
    limit = int(profile.get("context_limit") or 0)
    runtime_provider = (
        "codex_oauth"
        if adapter == "codex_oauth"
        else adapter
        if adapter in {"anthropic", "openai", "openai_responses", "gemini"}
        else "openai_compatible"
    )
    return {
        "id": profile["id"],
        "profile_id": profile["id"],
        "connection_id": connection["id"],
        "model": profile["model"],
        "name": profile["name"],
        "provider": runtime_provider,
        "adapter": adapter,
        "reasoning_effort": profile.get("reasoning_effort", ""),
        "vision_capable": "vision" in profile.get("capabilities", []),
        "capabilities": list(profile.get("capabilities") or []),
        "ctx": str(profile.get("ctx") or ""),
        "ctx_limit": limit,
        "context_limit": limit,
        "dimensions": int(profile.get("dimensions") or 0),
        "max_concurrency": int(profile.get("max_concurrency") or 0),
        "use_proxy": connection.get("use_proxy") is True,
        "options": {
            **(
                deepcopy(connection.get("options"))
                if isinstance(connection.get("options"), dict)
                else {}
            ),
            **(
                deepcopy(profile.get("options"))
                if isinstance(profile.get("options"), dict)
                else {}
            ),
        },
        "desc": profile.get("description", ""),
        "price": profile.get("price", ""),
        "base_url": str(connection.get("base_url") or "").rstrip("/"),
        "api_key": str(connection.get("api_key") or ""),
    }


def candidates_for_route(
    route_name: str,
    configuration: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if route_name not in ROUTE_NAMES:
        raise ValueError(f"unknown model route: {route_name}")
    config = configuration or get_model_configuration()
    result: list[dict[str, Any]] = []
    for profile_id in config["routes"][route_name]:
        candidate = candidate_for_profile(profile_id, config)
        if candidate is not None:
            result.append(candidate)
    return result


def selectable_model_candidates(
    configuration: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return every enabled profile that can be selected in a chat composer.

    Routes describe automatic/default use and must not limit manual selection.
    Keep primary-route entries first for a stable default, then append every
    other enabled chat or vision profile in configuration order. Embedding-only
    profiles are intentionally excluded because they cannot produce a reply.
    """
    config = configuration or get_model_configuration()
    ordered_ids = list(config["routes"]["primary"])
    ordered_ids.extend(
        profile["id"]
        for profile in config["profiles"]
        if profile["id"] not in ordered_ids
    )
    result: list[dict[str, Any]] = []
    for profile_id in ordered_ids:
        candidate = candidate_for_profile(profile_id, config)
        if candidate is None:
            continue
        capabilities = set(candidate.get("capabilities") or [])
        if not capabilities.intersection({"chat", "vision"}):
            continue
        result.append(candidate)
    return result


def validate_active_route_provider_families(
    configuration: dict[str, Any],
) -> None:
    """Reject automatic chat routes that could cross provider families."""
    enabled_connections = {
        str(connection.get("id") or ""): connection
        for connection in configuration.get("connections") or []
        if isinstance(connection, dict) and connection.get("enabled", True)
    }
    enabled_profiles = {
        str(profile.get("id") or ""): profile
        for profile in configuration.get("profiles") or []
        if isinstance(profile, dict) and profile.get("enabled", True)
    }
    route_families: dict[str, ProviderFamily] = {}
    routes = configuration.get("routes") or {}
    for route_name in ("primary", "secondary", "vision"):
        families: list[ProviderFamily] = []
        for profile_id in routes.get(route_name) or []:
            profile = enabled_profiles.get(str(profile_id or ""))
            if profile is None:
                continue
            connection = enabled_connections.get(
                str(profile.get("connection_id") or "")
            )
            if connection is None:
                continue
            family = provider_family_for_candidate({
                "adapter": connection.get("adapter"),
                "provider": connection.get("adapter"),
            })
            if family not in families:
                families.append(family)
        if len(families) > 1:
            raise ProviderFamilyError(
                f"route {route_name!r} mixes Codex and OpenAI-compatible "
                "models; automatic fallback across provider families is not allowed"
            )
        if families:
            route_families[route_name] = families[0]

    primary_family = route_families.get("primary")
    if primary_family is None:
        return
    for route_name in ("secondary", "vision"):
        family = route_families.get(route_name)
        if family is not None and family is not primary_family:
            raise ProviderFamilyError(
                f"route {route_name!r} uses {family.value} while the primary "
                f"route uses {primary_family.value}; automatic fallback across "
                "Codex and OpenAI-compatible provider families is not allowed"
            )


def save_model_configuration(
    raw: Any,
    *,
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], int]:
    previous = get_model_configuration()
    normalized = normalize_model_configuration(raw, previous=previous)
    validate_active_route_provider_families(normalized)
    revision, _settings = config_store.update_settings_atomic(
        {"model_configuration": normalized},
        expected_revision=expected_revision,
    )
    invalidate_model_runtime_caches()
    return normalized, revision


def public_model_configuration(configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    config = deepcopy(configuration or get_model_configuration())
    for connection in config["connections"]:
        configured = bool(connection.get("api_key"))
        connection["api_key"] = ""
        connection["api_key_configured"] = configured
        connection["secret_configured"] = configured
    config["adapters"] = [item.public_dict() for item in list_adapters()]
    config["revision"] = config_store.get_settings_revision()
    return config


def connection_with_secret(connection_id: str) -> dict[str, Any] | None:
    config = get_model_configuration()
    return next(
        (deepcopy(item) for item in config["connections"] if item["id"] == connection_id),
        None,
    )


__all__ = [
    "CONFIG_VERSION",
    "ROUTE_NAMES",
    "candidate_for_profile",
    "candidates_for_route",
    "connection_with_secret",
    "get_model_configuration",
    "normalize_model_configuration",
    "public_model_configuration",
    "save_model_configuration",
    "selectable_model_candidates",
    "validate_active_route_provider_families",
]
