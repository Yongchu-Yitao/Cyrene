"""Normalized model connections, profiles, and independent role routes.

This module owns the single adapter-oriented model configuration used by both
the settings UI and the runtime. It accepts and persists only the canonical
connection/profile/route graph, contains no HTTP concerns, and never returns
stored secrets from its public read API.
"""

from __future__ import annotations

import hashlib
import json
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


CONFIG_VERSION = 12
ROUTE_NAMES = ("primary", "secondary", "vision", "embedding")
_BUILTIN_MODEL_PLUGIN_PACK = "cyrene_model"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONNECTION_PATCH_FIELDS = frozenset({
    "name", "adapter", "enabled", "use_proxy", "base_url", "api_key",
    "clear_api_key", "options",
})
_PROFILE_PATCH_FIELDS = frozenset({
    "connection_id", "model", "name", "enabled", "context_limit",
    "dimensions", "reasoning_effort", "description", "price",
    "max_concurrency", "capabilities", "options",
})


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


def _plugin_seed_configuration(*, builtin_only: bool = False) -> dict[str, Any]:
    """Build the one-time initial graph from editable Model Plugin metadata."""

    from cyrene.plugins.model_catalog import model_plugin_catalog

    catalog = model_plugin_catalog()
    if builtin_only:
        catalog = [
            provider
            for provider in catalog
            if str(provider.get("pack_id") or "").strip()
            == _BUILTIN_MODEL_PLUGIN_PACK
        ]
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


def _configuration_version(raw: dict[str, Any]) -> int:
    version = raw.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 0:
        return version
    return 0


def _migrate_plugin_seed_connections(raw: dict[str, Any]) -> dict[str, Any]:
    """Restore built-in providers omitted by pre-v12 configuration graphs.

    Version 10 made the persisted connection graph authoritative, but did not
    reconcile an existing empty or partial graph with the newly plugin-owned
    provider catalog.  Onboarding then appended only the selected provider;
    the version 11 migration independently added Bailian.  Affected installs
    consequently exposed exactly those two services even though every built-in
    provider Plugin was loaded.

    Repair the graph once during the v12 upgrade.  Existing connections remain
    authoritative for their represented provider, so onboarding credentials,
    custom ids, profiles, and routes are preserved.  Once the migrated graph is
    saved at v12, later user deletions remain intentional and are not revived.
    """

    migrated = deepcopy(raw)
    seeded = _plugin_seed_configuration(builtin_only=True)
    raw_connections = migrated.get("connections")
    raw_profiles = migrated.get("profiles")
    raw_routes = migrated.get("routes")
    if not isinstance(raw_connections, list):
        return migrated
    if not isinstance(raw_profiles, list):
        return migrated
    if not isinstance(raw_routes, dict):
        return migrated

    seeded_connections = {
        str(connection.get("id") or "").strip().lower(): connection
        for connection in seeded["connections"]
        if isinstance(connection, dict)
    }
    represented: set[str] = set()
    used_connection_ids: set[str] = set()
    for connection in raw_connections:
        if not isinstance(connection, dict):
            continue
        connection_id = str(connection.get("id") or "").strip()
        if connection_id:
            used_connection_ids.add(connection_id)
            if connection_id.lower() in seeded_connections:
                represented.add(connection_id.lower())
        options = connection.get("options")
        preset = str(
            options.get("provider_preset") if isinstance(options, dict) else ""
        ).strip().lower()
        if preset in seeded_connections:
            represented.add(preset)

    added_connection_ids: set[str] = set()
    for provider_id, connection in seeded_connections.items():
        if provider_id in represented:
            continue
        connection_id = str(connection.get("id") or "")
        if not connection_id or connection_id in used_connection_ids:
            continue
        raw_connections.append(deepcopy(connection))
        represented.add(provider_id)
        used_connection_ids.add(connection_id)
        added_connection_ids.add(connection_id)

    used_profile_ids = {
        str(profile.get("id") or "")
        for profile in raw_profiles
        if isinstance(profile, dict)
    }
    added_profile_ids: set[str] = set()
    for profile in seeded["profiles"]:
        profile_id = str(profile.get("id") or "")
        if (
            str(profile.get("connection_id") or "") not in added_connection_ids
            or not profile_id
            or profile_id in used_profile_ids
        ):
            continue
        raw_profiles.append(deepcopy(profile))
        used_profile_ids.add(profile_id)
        added_profile_ids.add(profile_id)

    for route_name in ROUTE_NAMES:
        route = raw_routes.get(route_name)
        if not isinstance(route, list):
            continue
        for profile_id in seeded["routes"][route_name]:
            if profile_id in added_profile_ids and profile_id not in route:
                route.append(profile_id)

    migrated["version"] = CONFIG_VERSION
    return migrated


def _normalize_stored_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    source = (
        _migrate_plugin_seed_connections(raw)
        if _configuration_version(raw) < CONFIG_VERSION
        else raw
    )
    return normalize_model_configuration(source, previous=raw)


def get_model_configuration(*, persist_seed: bool = True) -> dict[str, Any]:
    """Read the canonical graph, seeding or upgrading Plugin connections."""

    revision = config_store.get_settings_revision()
    raw = config_store.get_setting("model_configuration", None)
    if isinstance(raw, dict):
        configured = _normalize_stored_configuration(raw)
        if _configuration_version(raw) >= CONFIG_VERSION or not persist_seed:
            return configured
        try:
            config_store.update_settings_atomic(
                {"model_configuration": configured},
                expected_revision=revision,
            )
            invalidate_model_runtime_caches()
        except config_store.SettingsRevisionConflict:
            latest = config_store.get_setting("model_configuration", None)
            if isinstance(latest, dict):
                return _normalize_stored_configuration(latest)
            raise
        return configured

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


def provider_preset_for_connection(connection: Any) -> str:
    """Return the canonical optional Provider Plugin identity for a connection."""

    if not isinstance(connection, dict):
        return ""
    options = connection.get("options")
    if not isinstance(options, dict):
        return ""
    return str(options.get("provider_preset") or "").strip().lower()


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
    updates: dict[str, object] = {"model_configuration": normalized}
    if any(
        connection.get("use_proxy") is True
        for connection in normalized["connections"]
    ):
        # A per-connection proxy opt-in must be effective immediately. Persist
        # the compatibility master switch in the same CAS write so enabling a
        # model proxy cannot create a second settings revision (and conflict
        # with the model configuration save that triggered it).
        updates["external_agent_proxy_enabled"] = True
    revision, _settings = config_store.update_settings_atomic(
        updates,
    )
    invalidate_model_runtime_caches()
    return normalized, revision


def model_configuration_hash(configuration: dict[str, Any] | None = None) -> str:
    """Return a stable opaque digest for one canonical model graph.

    The digest is diagnostic metadata for patch rebasing, not a write lease:
    stale hashes never block a field-level patch.  Secrets remain inside the
    one-way digest and are never copied into a public response.
    """

    config = configuration or get_model_configuration()
    canonical = normalize_model_configuration(config, previous=config)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_configuration_patch(raw: Any) -> tuple[str, list[Any]]:
    if not isinstance(raw, dict):
        raise ValueError("model configuration patch must be an object")
    unknown = set(raw) - {"base_hash", "operations"}
    if unknown:
        raise ValueError(
            "unknown model configuration patch fields: "
            + ", ".join(sorted(unknown))
        )
    base_hash = str(raw.get("base_hash") or "").strip().lower()
    if base_hash and not re.fullmatch(r"[0-9a-f]{64}", base_hash):
        raise ValueError("base_hash must be a SHA-256 hex digest")
    operations = raw.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("model configuration patch operations must be a non-empty array")
    if len(operations) > 2000:
        raise ValueError("model configuration patch has too many operations")
    return base_hash, operations


def _entity_index(working: dict[str, Any], collection: str, entity_id: str) -> int:
    return next(
        (
            index
            for index, item in enumerate(working[collection])
            if str(item.get("id") or "") == entity_id
        ),
        -1,
    )


def _apply_connection_patch(
    working: dict[str, Any], operation: dict[str, Any], kind: str, entity_id: str
) -> None:
    entity_id = _identifier(entity_id, kind="connection")
    index = _entity_index(working, "connections", entity_id)
    if kind == "remove_connection":
        if index >= 0:
            working["connections"].pop(index)
        removed_profiles = {
            str(profile.get("id") or "")
            for profile in working["profiles"]
            if str(profile.get("connection_id") or "") == entity_id
        }
        working["profiles"] = [
            profile
            for profile in working["profiles"]
            if str(profile.get("connection_id") or "") != entity_id
        ]
        for route_name in ROUTE_NAMES:
            working["routes"][route_name] = [
                profile_id
                for profile_id in working["routes"][route_name]
                if profile_id not in removed_profiles
            ]
        return
    if kind == "upsert_connection":
        value = operation.get("value")
        if not isinstance(value, dict):
            raise ValueError("upsert_connection value must be an object")
        replacement = {**deepcopy(value), "id": entity_id}
        if index >= 0:
            working["connections"][index] = replacement
        else:
            working["connections"].append(replacement)
        return
    changes = operation.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("patch_connection changes must be a non-empty object")
    unsupported = set(changes) - _CONNECTION_PATCH_FIELDS
    if unsupported:
        raise ValueError(
            "unsupported connection patch fields: " + ", ".join(sorted(unsupported))
        )
    if index < 0:
        raise ValueError(f"model connection not found: {entity_id}")
    working["connections"][index] = {
        **working["connections"][index],
        **deepcopy(changes),
        "id": entity_id,
    }


def _apply_profile_patch(
    working: dict[str, Any], operation: dict[str, Any], kind: str, entity_id: str
) -> None:
    entity_id = _identifier(entity_id, kind="profile")
    index = _entity_index(working, "profiles", entity_id)
    if kind == "remove_profile":
        if index >= 0:
            working["profiles"].pop(index)
        for route_name in ROUTE_NAMES:
            working["routes"][route_name] = [
                profile_id
                for profile_id in working["routes"][route_name]
                if profile_id != entity_id
            ]
        return
    if kind == "upsert_profile":
        value = operation.get("value")
        if not isinstance(value, dict):
            raise ValueError("upsert_profile value must be an object")
        replacement = {**deepcopy(value), "id": entity_id}
        if index >= 0:
            working["profiles"][index] = replacement
        else:
            working["profiles"].append(replacement)
        return
    changes = operation.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("patch_profile changes must be a non-empty object")
    unsupported = set(changes) - _PROFILE_PATCH_FIELDS
    if unsupported:
        raise ValueError(
            "unsupported profile patch fields: " + ", ".join(sorted(unsupported))
        )
    if index < 0:
        raise ValueError(f"model profile not found: {entity_id}")
    working["profiles"][index] = {
        **working["profiles"][index],
        **deepcopy(changes),
        "id": entity_id,
    }


def _apply_configuration_patch_operation(
    working: dict[str, Any], operation: Any, position: int
) -> None:
    if not isinstance(operation, dict):
        raise ValueError(f"patch operation {position} must be an object")
    kind = str(operation.get("op") or "").strip().lower()
    entity_id = str(operation.get("id") or "").strip()
    if kind in {"upsert_connection", "patch_connection", "remove_connection"}:
        _apply_connection_patch(working, operation, kind, entity_id)
        return
    if kind in {"upsert_profile", "patch_profile", "remove_profile"}:
        _apply_profile_patch(working, operation, kind, entity_id)
        return
    if kind == "set_route":
        route_name = str(operation.get("route") or "").strip().lower()
        if route_name not in ROUTE_NAMES:
            raise ValueError(f"unknown model route: {route_name}")
        value = operation.get("value")
        if not isinstance(value, list):
            raise ValueError("set_route value must be an array")
        working["routes"][route_name] = deepcopy(value)
        return
    raise ValueError(f"unknown model configuration patch operation: {kind}")


def patch_model_configuration(
    raw: Any,
) -> tuple[dict[str, Any], int, str, bool]:
    """Atomically apply idempotent entity/field operations to the latest graph."""

    base_hash, operations = _validated_configuration_patch(raw)

    # Ensure the optional Plugin seed exists before entering the atomic
    # mutation.  Subsequent reads and the complete patch run under one lock.
    get_model_configuration()
    patch_state = {"observed_hash": "", "rebased": False}

    def mutate(previous_raw: Any) -> dict[str, Any]:
        if not isinstance(previous_raw, dict):
            raise ValueError("stored model configuration must be an object")
        current = normalize_model_configuration(previous_raw, previous=previous_raw)
        patch_state["observed_hash"] = model_configuration_hash(current)
        patch_state["rebased"] = bool(
            base_hash and base_hash != patch_state["observed_hash"]
        )
        working = deepcopy(current)
        for position, operation in enumerate(operations):
            _apply_configuration_patch_operation(working, operation, position)
        normalized = normalize_model_configuration(working, previous=current)
        validate_active_route_provider_families(normalized)
        return normalized

    revision, _before, saved_raw = config_store.mutate_setting_atomic(
        "model_configuration",
        mutate,
        companion_updates=lambda next_value: (
            {"external_agent_proxy_enabled": True}
            if isinstance(next_value, dict)
            and any(
                isinstance(connection, dict)
                and connection.get("use_proxy") is True
                for connection in next_value.get("connections") or []
            )
            else {}
        ),
    )
    if not isinstance(saved_raw, dict):
        raise RuntimeError("model configuration patch returned an invalid graph")
    saved = normalize_model_configuration(saved_raw, previous=saved_raw)
    invalidate_model_runtime_caches()
    return saved, revision, model_configuration_hash(saved), bool(patch_state["rebased"])


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
    "model_configuration_hash",
    "patch_model_configuration",
    "provider_preset_for_connection",
    "public_model_configuration",
    "save_model_configuration",
    "selectable_model_candidates",
    "validate_active_route_provider_families",
]
