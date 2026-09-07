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

from cyrene.model.adapter_registry import list_adapters
from cyrene.model.cache_invalidation import invalidate_model_runtime_caches
from cyrene.model.transcript_policy import (
    ProviderFamily,
    ProviderFamilyError,
    provider_family_for_candidate,
)
from cyrene.platform import config_store

from .configuration_validation import (
    CONFIG_VERSION, ROUTE_NAMES, _identifier, normalize_model_configuration,
)

_BUILTIN_MODEL_PLUGIN_PACK = "cyrene_model"
_CONNECTION_PATCH_FIELDS = frozenset({
    "name", "adapter", "enabled", "use_proxy", "base_url", "api_key",
    "clear_api_key", "options",
})
_PROFILE_PATCH_FIELDS = frozenset({
    "connection_id", "model", "name", "enabled", "context_limit",
    "dimensions", "reasoning_effort", "description", "price",
    "max_concurrency", "capabilities", "options",
})


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
        "provider_preset": provider_preset_for_connection(connection),
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
