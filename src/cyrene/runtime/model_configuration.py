"""Normalized model connections, profiles, and independent role routes.

This module owns the single adapter-oriented model configuration used by both
the settings UI and the runtime.  Legacy candidate lists are accepted only as
upgrade/write inputs; they are never maintained as a second source of truth.
It contains no HTTP concerns and never returns stored secrets from its public
read API.
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from cyrene.model_runtime.adapter_registry import list_adapters, require_adapter
from cyrene.model_runtime.cache_invalidation import invalidate_model_runtime_caches
from cyrene.model_runtime.transcript_policy import (
    ProviderFamily,
    ProviderFamilyError,
    provider_family_for_candidate,
)
from cyrene.runtime import config_store


CONFIG_VERSION = 9
ROUTE_NAMES = ("primary", "secondary", "vision", "embedding")
RETIRED_MODEL_SETTING_KEYS = frozenset({
    "models",
    "custom_models",
    "codex_model",
    "model_source",
    "vision_models",
    "secondary_model",
})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MINIMAX_DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
_MINIMAX_REPLACED_DEFAULT_BASE_URLS = {
    "https://api.minimax.io/v1",
    "https://api.minimax.com/v1",
}
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_DEEPSEEK_REPLACED_DEFAULT_BASE_URLS = {
    "https://api.deepseek.com",
}
_KIMI_DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
_GLM_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_OPENCODE_GO_DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_AMD_GPU_CLOUD_DEFAULT_BASE_URL = "https://developer.amd.com.cn/radeon/api/v1"

# Built-in providers are connection presets, not protocol adapters. This keeps
# the adapter picker focused on wire formats while still giving new and
# upgraded installs useful provider entries out of the box.
_DEFAULT_PROVIDER_CONNECTIONS: tuple[dict[str, Any], ...] = (
    {
        "introduced_version": 3,
        "id": "minimax",
        "name": "MiniMax",
        "adapter": "openai",
        "enabled": True,
        "base_url": _MINIMAX_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "minimax"},
    },
    {
        "introduced_version": 3,
        "id": "deepseek",
        "name": "DeepSeek",
        "adapter": "openai",
        "enabled": True,
        "base_url": _DEEPSEEK_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "deepseek"},
    },
    {
        "introduced_version": 6,
        "id": "codex_oauth",
        "name": "OpenAI Codex OAuth",
        "adapter": "codex_oauth",
        "enabled": True,
        "base_url": "codex://oauth",
        "api_key": "",
        "options": {"provider_preset": "codex_oauth"},
    },
    {
        "introduced_version": 7,
        "id": "local_onnx",
        "name": "Local ONNX",
        "adapter": "local_onnx",
        "enabled": True,
        "base_url": "",
        "api_key": "",
        "options": {"provider_preset": "local_onnx"},
    },
    {
        "introduced_version": 8,
        "id": "kimi",
        "name": "Kimi",
        "adapter": "openai",
        "enabled": True,
        "base_url": _KIMI_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "kimi"},
    },
    {
        "introduced_version": 8,
        "id": "glm",
        "name": "GLM",
        "adapter": "openai",
        "enabled": True,
        "base_url": _GLM_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "glm"},
    },
    {
        "introduced_version": 8,
        "id": "opencode_go",
        "name": "OpenCode Go",
        "adapter": "openai",
        "enabled": True,
        "base_url": _OPENCODE_GO_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "opencode_go"},
    },
    {
        "introduced_version": 8,
        "id": "gemini",
        "name": "Gemini",
        "adapter": "gemini",
        "enabled": True,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "options": {"provider_preset": "gemini"},
    },
    {
        "introduced_version": 8,
        "id": "openrouter",
        "name": "OpenRouter",
        "adapter": "openai",
        "enabled": True,
        "base_url": _OPENROUTER_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "openrouter"},
    },
    {
        "introduced_version": 8,
        "id": "amd_gpu_cloud",
        "name": "AMD GPU Cloud",
        "adapter": "openai",
        "enabled": True,
        "base_url": _AMD_GPU_CLOUD_DEFAULT_BASE_URL,
        "api_key": "",
        "options": {"provider_preset": "amd_gpu_cloud"},
    },
)

_DEFAULT_PROVIDER_HOSTS = {
    "minimax": {"api.minimax.io", "api.minimaxi.com"},
    "deepseek": {"api.deepseek.com"},
    "kimi": {"api.moonshot.cn"},
    "glm": {"open.bigmodel.cn"},
    "opencode_go": {"opencode.ai"},
    "gemini": {"generativelanguage.googleapis.com"},
    "openrouter": {"openrouter.ai"},
    "amd_gpu_cloud": {"developer.amd.com.cn"},
}


def _identifier(value: Any, *, kind: str, fallback_prefix: str = "") -> str:
    result = str(value or "").strip()
    if not result and fallback_prefix:
        result = f"{fallback_prefix}-{uuid.uuid4().hex[:12]}"
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
    value: Any = raw.get("context_limit", raw.get("ctx_limit", raw.get("ctx", 0)))
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
    if isinstance(source, str):
        source = [source]
    result = {
        str(item or "").strip().lower()
        for item in (source if isinstance(source, list) else [])
        if str(item or "").strip()
    }
    if raw.get("vision_capable") is True:
        result.add("vision")
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
    raw_connections = raw.get("connections", [])
    raw_profiles = raw.get("profiles", [])
    raw_routes = raw.get("routes", {})
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
        connection_id = _identifier(
            source.get("id"), kind="connection", fallback_prefix="connection"
        )
        if connection_id in connection_ids:
            raise ValueError(f"duplicate connection id: {connection_id}")
        connection_ids.add(connection_id)
        adapter_id = str(
            source.get("adapter")
            or source.get("adapter_id")
            or source.get("provider")
            or "openai_compatible"
        ).strip().lower().replace("-", "_")
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
        submitted_secret = str(source.get("api_key") or source.get("secret") or "").strip()
        if source.get("clear_api_key") is True or source.get("clear_secret") is True:
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
        profile_id = _identifier(
            source.get("id"), kind="profile", fallback_prefix="profile"
        )
        if profile_id in profile_ids:
            raise ValueError(f"duplicate profile id: {profile_id}")
        profile_ids.add(profile_id)
        connection_id = _identifier(source.get("connection_id"), kind="connection")
        connection = connection_by_id.get(connection_id)
        if connection is None:
            raise ValueError(
                f"profile {profile_id!r} references unknown connection {connection_id!r}"
            )
        model = str(source.get("model") or source.get("model_id") or "").strip()
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
            "description": str(source.get("description") or source.get("desc") or "").strip(),
            "price": str(source.get("price") or "").strip(),
            "max_concurrency": max_concurrency,
            "options": deepcopy(source.get("options")) if isinstance(source.get("options"), dict) else {},
        })

    routes: dict[str, list[str]] = {}
    for route_name in ROUTE_NAMES:
        value = raw_routes.get(route_name, [])
        if isinstance(value, str):
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            raise ValueError(f"route {route_name!r} must be an array")
        route: list[str] = []
        for raw_id in value:
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


def _unique_id(preferred: str, used: set[str], prefix: str) -> str:
    candidate = str(preferred or "").strip()
    if not _ID_RE.fullmatch(candidate):
        candidate = ""
    if candidate and candidate not in used:
        used.add(candidate)
        return candidate
    index = 1
    while f"{prefix}-{index}" in used:
        index += 1
    candidate = f"{prefix}-{index}"
    used.add(candidate)
    return candidate


def _default_connection_state(
    connections: list[Any],
    pending_presets: list[dict[str, Any]],
) -> tuple[set[str], set[str], dict[str, str]]:
    default_provider_ids = {
        str(preset.get("id") or "").strip().lower()
        for preset in pending_presets
    }
    recognized: set[str] = set()
    used_ids: set[str] = set()
    hosts_by_connection: dict[str, str] = {}
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        connection_id = str(connection.get("id") or "").strip().lower()
        if connection_id:
            used_ids.add(connection_id)
        options = connection.get("options")
        preset = str(
            options.get("provider_preset") if isinstance(options, dict) else ""
        ).strip().lower()
        adapter = str(
            connection.get("adapter")
            or connection.get("adapter_id")
            or connection.get("provider")
            or ""
        ).strip().lower().replace("-", "_")
        connection_name = re.sub(
            r"[^a-z0-9]+", "", str(connection.get("name") or "").lower()
        )
        base_url = str(connection.get("base_url") or "").strip().rstrip("/")
        if (
            (preset == "minimax" or (connection_id == "minimax" and connection_name == "minimax"))
            and base_url in _MINIMAX_REPLACED_DEFAULT_BASE_URLS
        ):
            connection["base_url"] = _MINIMAX_DEFAULT_BASE_URL
        if (
            (preset == "deepseek" or (connection_id == "deepseek" and connection_name == "deepseek"))
            and base_url in _DEEPSEEK_REPLACED_DEFAULT_BASE_URLS
        ):
            connection["base_url"] = _DEEPSEEK_DEFAULT_BASE_URL
        if preset in default_provider_ids:
            recognized.add(preset)
        if connection_id in _DEFAULT_PROVIDER_HOSTS:
            recognized.add(connection_id)
        if connection_name in _DEFAULT_PROVIDER_HOSTS:
            recognized.add(connection_name)
        if adapter in default_provider_ids:
            recognized.add(adapter)
        try:
            host = (urlsplit(str(connection.get("base_url") or "")).hostname or "").lower()
        except ValueError:
            host = ""
        if connection_id:
            hosts_by_connection[connection_id] = host
    return recognized, used_ids, hosts_by_connection


def _with_default_provider_connections(raw: dict[str, Any]) -> dict[str, Any]:
    """Add presets introduced after the stored version without reviving deletions."""

    upgraded = deepcopy(raw)
    connections = upgraded.get("connections")
    if not isinstance(connections, list):
        return upgraded

    source_version = _configuration_version(raw)
    pending_presets = [
        preset
        for preset in _DEFAULT_PROVIDER_CONNECTIONS
        if source_version < int(preset["introduced_version"])
    ]
    recognized, used_ids, hosts_by_connection = _default_connection_state(
        connections,
        pending_presets,
    )

    raw_profiles = upgraded.get("profiles")
    profiles = raw_profiles if isinstance(raw_profiles, list) else []
    openai_capabilities = set(require_adapter("openai").capabilities)

    for preset in pending_presets:
        provider_id = str(preset["id"])
        if provider_id in recognized:
            continue
        provider_hosts = _DEFAULT_PROVIDER_HOSTS.get(provider_id, set())
        host_matches = [
            connection
            for connection in connections
            if isinstance(connection, dict)
            and hosts_by_connection.get(str(connection.get("id") or "").lower())
            in provider_hosts
        ]
        if host_matches:
            # Prefer the legacy connection that already owns a provider model.
            # Keeping its id preserves profiles, routes, and stored credentials.
            model_connection_ids = {
                str(profile.get("connection_id") or "")
                for profile in profiles
                if isinstance(profile, dict)
                and str(profile.get("model") or "").strip().lower().startswith(provider_id)
            }
            connection = next(
                (
                    item
                    for item in host_matches
                    if str(item.get("id") or "") in model_connection_ids
                ),
                host_matches[0],
            )
            connection_id = str(connection.get("id") or "")
            generic_name = re.sub(
                r"[^a-z0-9]+", "", str(connection.get("name") or "").lower()
            )
            if generic_name in {"", "openai", "openaicompatible", "openaicompatiblelegacy"}:
                connection["name"] = preset["name"]
            options = connection.get("options")
            connection["options"] = {
                **(options if isinstance(options, dict) else {}),
                "provider_preset": provider_id,
            }
            if (
                provider_id == "deepseek"
                and str(connection.get("base_url") or "").strip().rstrip("/")
                in _DEEPSEEK_REPLACED_DEFAULT_BASE_URLS
            ):
                connection["base_url"] = _DEEPSEEK_DEFAULT_BASE_URL
            profile_capabilities = {
                str(capability or "").strip().lower()
                for profile in profiles
                if isinstance(profile, dict)
                and str(profile.get("connection_id") or "") == connection_id
                for capability in (
                    profile.get("capabilities")
                    if isinstance(profile.get("capabilities"), list)
                    else []
                )
            }
            if profile_capabilities <= openai_capabilities:
                connection["adapter"] = "openai"
            continue
        connection = {
            key: deepcopy(value)
            for key, value in preset.items()
            if key != "introduced_version"
        }
        if provider_id in used_ids:
            connection["id"] = _unique_id(
                f"{provider_id}-default", used_ids, provider_id
            )
        else:
            used_ids.add(provider_id)
        connections.append(connection)
    upgraded["version"] = CONFIG_VERSION
    return upgraded


def _configuration_version(raw: dict[str, Any]) -> int:
    try:
        return max(0, int(raw.get("version") or 0))
    except (TypeError, ValueError):
        return 0


def migrate_legacy_model_configuration(
    primary_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the normalized graph from the pre-plugin settings schema."""

    connections: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    routes = {name: [] for name in ROUTE_NAMES}
    connection_keys: dict[tuple[str, str, str, bool], str] = {}
    profile_keys: dict[tuple[str, str], str] = {}
    used_profile_ids: set[str] = set()

    def add(raw: Any, role: str, *, forced_adapter: str = "") -> str:
        if not isinstance(raw, dict):
            return ""
        model = str(raw.get("model") or raw.get("name") or raw.get("id") or "").strip()
        if not model:
            return ""
        adapter = str(
            forced_adapter or raw.get("adapter") or raw.get("provider") or "openai_compatible"
        ).strip().lower().replace("-", "_")
        if adapter not in {item.id for item in list_adapters()}:
            adapter = "openai_compatible"
        definition = require_adapter(adapter)
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        if adapter == "codex_oauth":
            base_url = definition.default_base_url
        elif adapter == "local_onnx":
            base_url = ""
        elif not base_url:
            base_url = definition.default_base_url
        api_key = "" if definition.auth_type != "api_key" else str(raw.get("api_key") or "").strip()
        use_proxy = raw.get("use_proxy") is True
        key = (adapter, base_url, api_key, use_proxy)
        connection_id = connection_keys.get(key)
        if not connection_id:
            connection_id = f"connection-{len(connections) + 1}"
            connection_keys[key] = connection_id
            connections.append({
                "id": connection_id,
                "name": str(raw.get("provider_name") or definition.label).strip() or definition.label,
                "adapter": adapter,
                "enabled": True,
                "use_proxy": use_proxy,
                "base_url": base_url,
                "api_key": api_key,
                "options": {},
            })
        profile_key = (connection_id, model)
        profile_id = profile_keys.get(profile_key)
        if not profile_id:
            profile_id = _unique_id(str(raw.get("id") or ""), used_profile_ids, "profile")
            profile_keys[profile_key] = profile_id
            capabilities: set[str] = {"embedding" if role == "embedding" else "chat"}
            if role == "vision" or raw.get("vision_capable") is True or adapter == "codex_oauth":
                capabilities.add("vision")
            limit = _context_limit(raw)
            profiles.append({
                "id": profile_id,
                "connection_id": connection_id,
                "model": model,
                "name": str(raw.get("name") or model).strip() or model,
                "enabled": True,
                "capabilities": sorted(capabilities),
                "context_limit": limit,
                "ctx": str(raw.get("ctx") or (limit if limit else "")).strip(),
                "dimensions": int(raw.get("dimensions") or 0),
                "reasoning_effort": str(raw.get("reasoning_effort") or "").strip().lower(),
                "description": str(raw.get("description") or raw.get("desc") or "").strip(),
                "price": str(raw.get("price") or "").strip(),
                "max_concurrency": int(raw.get("max_concurrency") or 0),
                "options": {},
            })
        else:
            profile = next(item for item in profiles if item["id"] == profile_id)
            if role == "vision" and "vision" not in profile["capabilities"]:
                profile["capabilities"].append("vision")
            if role == "embedding" and "embedding" not in profile["capabilities"]:
                profile["capabilities"].append("embedding")
        if profile_id not in routes[role]:
            routes[role].append(profile_id)
        return profile_id

    # Read the retired raw key directly.  ``get_models()`` is now a derived
    # primary-route view and calling it here would make legacy migration depend
    # on the graph that this function is constructing.
    legacy_models = [
        item
        for item in config_store.get_setting("models", []) or []
        if isinstance(item, dict)
    ]
    legacy_custom = [
        item
        for item in config_store.get_setting("custom_models", []) or []
        if isinstance(item, dict)
    ]
    raw_codex = config_store.get_setting("codex_model", {})
    legacy_codex = raw_codex if isinstance(raw_codex, dict) else {}
    source = str(config_store.get_setting("model_source", "") or "").strip().lower()
    primary_items = (
        [item for item in primary_candidates if isinstance(item, dict)]
        if primary_candidates is not None
        else [legacy_codex]
        if source == "codex" and isinstance(legacy_codex, dict) and legacy_codex
        else (legacy_custom or legacy_models)
    )
    for item in primary_items:
        add(item, "primary")
    # Preserve inactive profiles as editable provider/model records.
    for item in [*legacy_models, *legacy_custom]:
        profile_id = add(item, "primary")
        if item not in primary_items and profile_id in routes["primary"]:
            routes["primary"].remove(profile_id)
    if isinstance(legacy_codex, dict) and legacy_codex:
        profile_id = add(legacy_codex, "primary", forced_adapter="codex_oauth")
        if source != "codex" and profile_id in routes["primary"]:
            routes["primary"].remove(profile_id)
    for item in config_store.get_setting("vision_models", []) or []:
        add(item, "vision")
    secondary = config_store.get_setting("secondary_model", {})
    if isinstance(secondary, dict) and str(secondary.get("model") or "").strip():
        add(secondary, "secondary")

    embedding = config_store.get_setting("embedding", {})
    if not isinstance(embedding, dict):
        embedding = {}
    embedding = {
        **embedding,
        "base_url": embedding.get("base_url") or config_store.get_env("EMBEDDING_BASE_URL", ""),
        "api_key": embedding.get("api_key") or config_store.get_env("EMBEDDING_API_KEY", ""),
        "model": embedding.get("model") or config_store.get_env("EMBEDDING_MODEL", ""),
    }
    if str(embedding.get("model") or "").strip():
        add(embedding, "embedding", forced_adapter=str(embedding.get("provider") or "openai_compatible"))
    return normalize_model_configuration(_with_default_provider_connections({
        "connections": connections,
        "profiles": profiles,
        "routes": routes,
    }))


def _stored_configuration() -> dict[str, Any] | None:
    raw = config_store.get_setting("model_configuration", None)
    if not isinstance(raw, dict):
        return None
    # The default empty graph is indistinguishable from an unmigrated install.
    # If legacy model state exists, migrate it once instead of hiding it.
    has_graph = bool(raw.get("connections") or raw.get("profiles"))
    has_legacy = bool(
        config_store.get_setting("models", [])
        or config_store.get_setting("custom_models", [])
        or config_store.get_setting("codex_model", {})
        or config_store.get_setting("vision_models", [])
        or str((config_store.get_setting("secondary_model", {}) or {}).get("model") or "").strip()
        or str((config_store.get_setting("embedding", {}) or {}).get("model") or "").strip()
    )
    return raw if has_graph or not has_legacy else None


def get_model_configuration(*, persist_migration: bool = True) -> dict[str, Any]:
    raw = _stored_configuration()
    if raw is not None:
        needs_upgrade = _configuration_version(raw) < CONFIG_VERSION
        needs_retired_models_cleanup = any(
            config_store.get_setting(key, None) is not None
            for key in RETIRED_MODEL_SETTING_KEYS
        )
        source = _with_default_provider_connections(raw) if needs_upgrade else raw
        normalized = normalize_model_configuration(source, previous=raw)
        if (needs_upgrade or needs_retired_models_cleanup) and persist_migration:
            try:
                config_store.update_settings_atomic(
                    {"model_configuration": normalized},
                    remove_setting_keys=RETIRED_MODEL_SETTING_KEYS,
                )
            except config_store.SettingsRevisionConflict:
                latest = _stored_configuration()
                if latest is not None:
                    latest_source = (
                        _with_default_provider_connections(latest)
                        if _configuration_version(latest) < CONFIG_VERSION
                        else latest
                    )
                    return normalize_model_configuration(latest_source, previous=latest)
        return normalized
    migrated = migrate_legacy_model_configuration()
    if persist_migration:
        try:
            config_store.update_settings_atomic(
                {"model_configuration": migrated},
                remove_setting_keys=RETIRED_MODEL_SETTING_KEYS,
            )
        except config_store.SettingsRevisionConflict:
            # Another request completed the same migration first.
            latest = _stored_configuration()
            if latest is not None:
                return normalize_model_configuration(latest, previous=latest)
    return migrated


def _legacy_base_url(connection: dict[str, Any]) -> str:
    base_url = str(connection.get("base_url") or "").rstrip("/")
    if connection.get("adapter") == "ollama" and not base_url.endswith("/v1"):
        return f"{base_url}/v1"
    return base_url


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
        "base_url": _legacy_base_url(connection),
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


def _legacy_mirrors(configuration: dict[str, Any]) -> tuple[dict[str, object], dict[str, str]]:
    primary = candidates_for_route("primary", configuration)
    embedding = candidates_for_route("embedding", configuration)
    selected_embedding = embedding[0] if embedding else {}
    embedding_adapter = str(selected_embedding.get("adapter") or "openai_compatible")
    embedding_base = str(selected_embedding.get("base_url") or "")
    if embedding_adapter == "ollama" and embedding_base.endswith("/v1"):
        embedding_base = embedding_base[:-3].rstrip("/")
    integration_embedding = {
        "provider": embedding_adapter,
        "base_url": embedding_base,
        "api_key": str(selected_embedding.get("api_key") or ""),
        "model": str(selected_embedding.get("model") or ""),
        "dimensions": int(selected_embedding.get("dimensions") or 0),
        "use_proxy": selected_embedding.get("use_proxy") is True,
    }
    settings_updates: dict[str, object] = {
        "model_configuration": configuration,
        "embedding": integration_embedding,
    }
    selected_primary = primary[0] if primary else {}
    env_updates = {
        "OPENAI_MODEL": str(selected_primary.get("model") or ""),
        "OPENAI_BASE_URL": str(selected_primary.get("base_url") or ""),
        "OPENAI_API_KEY": str(selected_primary.get("api_key") or ""),
        "EMBEDDING_MODEL": integration_embedding["model"],
        "EMBEDDING_BASE_URL": integration_embedding["base_url"],
        "EMBEDDING_API_KEY": integration_embedding["api_key"],
    }
    return settings_updates, env_updates


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
    settings_updates, env_updates = _legacy_mirrors(normalized)
    revision, _settings = config_store.update_settings_and_env_atomic(
        settings_updates,
        env_updates,
        expected_revision=expected_revision,
        remove_setting_keys=RETIRED_MODEL_SETTING_KEYS,
    )
    invalidate_model_runtime_caches()
    return normalized, revision


def save_primary_model_candidates(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    """Compatibility write: replace the primary route without storing a list.

    Older callers still submit flat candidates.  Convert them immediately into
    connections/profiles/routes and persist only the normalized graph so a
    compatibility write cannot diverge from the settings page again.
    """
    migrated = migrate_legacy_model_configuration(list(candidates or []))
    return save_model_configuration(migrated)


def public_model_configuration(configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    config = deepcopy(configuration or get_model_configuration())
    for connection in config["connections"]:
        configured = bool(connection.get("api_key"))
        connection["api_key"] = ""
        connection["api_key_configured"] = configured
        connection["secret_configured"] = configured
        connection["adapter_id"] = connection["adapter"]
        connection["provider"] = connection["adapter"]
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
    "migrate_legacy_model_configuration",
    "normalize_model_configuration",
    "public_model_configuration",
    "save_model_configuration",
    "save_primary_model_candidates",
    "selectable_model_candidates",
    "validate_active_route_provider_families",
]
