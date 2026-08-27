"""Discovery of editable model Plugins from the user Plugin directory."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .native_tools import seed_builtin_plugin_directory
from .registry import PluginLoadFailure, PluginRegistry, default_plugin_impl_directory

_SESSION_MODEL_PREFERENCE_SETTING = "llm_session_model_preferences"
_LAST_SUCCESS_SETTING = "llm_last_success_endpoints"
_SESSION_AFFINITY_PREFIX = "session:"
_MAX_SESSION_AFFINITIES = 2048

_LOCK = threading.RLock()
_CACHE_ROOT: Path | None = None
_CACHE_SIGNATURE: tuple[tuple[str, int, int], ...] = ()
_CACHE_REGISTRY: PluginRegistry | None = None
_CACHE_FAILURES: tuple[PluginLoadFailure, ...] = ()


def _model_pack_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    pack = root / "cyrene_model"
    if not pack.is_dir():
        return ()
    result: list[tuple[str, int, int]] = []
    for path in sorted(pack.rglob("*.py")):
        try:
            stat = path.stat()
        except OSError:
            continue
        result.append((str(path.relative_to(pack)), stat.st_mtime_ns, stat.st_size))
    return tuple(result)


def editable_model_registry(
    directory: str | Path | None = None,
) -> tuple[PluginRegistry, tuple[PluginLoadFailure, ...]]:
    """Return a registry refreshed whenever an editable model file changes."""

    global _CACHE_ROOT, _CACHE_SIGNATURE, _CACHE_REGISTRY, _CACHE_FAILURES
    root = Path(directory or default_plugin_impl_directory()).expanduser().resolve()
    with _LOCK:
        seed_builtin_plugin_directory(root)
        signature = _model_pack_signature(root)
        if _CACHE_REGISTRY is not None and _CACHE_ROOT == root and _CACHE_SIGNATURE == signature:
            return _CACHE_REGISTRY, _CACHE_FAILURES
        registry = PluginRegistry(include_core=False)
        failures = registry.load_directory(root)
        _CACHE_ROOT = root
        _CACHE_SIGNATURE = signature
        _CACHE_REGISTRY = registry
        _CACHE_FAILURES = failures
        return registry, failures


def model_plugin_catalog(
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    registry, _failures = editable_model_registry(directory)
    result: list[dict[str, Any]] = []
    for registered in registry.list_plugins():
        plugin = registered.plugin
        if plugin.kind != "model":
            continue
        provider = plugin.metadata.get("provider")
        if not isinstance(provider, Mapping):
            continue
        provider_id = str(provider.get("id") or "").strip().lower()
        if not provider_id:
            continue
        result.append(
            {
                **dict(provider),
                "id": provider_id,
                "plugin_name": plugin.name,
                "description": plugin.description,
                "pack_id": registered.pack_id or "",
            }
        )
    return sorted(result, key=lambda item: (str(item.get("name") or ""), item["id"]))


def registered_model_plugin_catalog(registry: PluginRegistry) -> list[dict[str, Any]]:
    """Describe the model Providers already loaded in one live Registry."""

    result: list[dict[str, Any]] = []
    for registered in registry.list_plugins():
        plugin = registered.plugin
        if plugin.kind != "model":
            continue
        provider = plugin.metadata.get("provider")
        if not isinstance(provider, Mapping):
            continue
        provider_id = str(provider.get("id") or "").strip().lower()
        if not provider_id:
            continue
        result.append(
            {
                **dict(provider),
                "id": provider_id,
                "plugin_name": plugin.name,
                "description": plugin.description,
                "pack_id": registered.pack_id or "",
            }
        )
    return sorted(result, key=lambda item: (str(item.get("name") or ""), item["id"]))


def candidate_provider_id(candidate: Mapping[str, Any]) -> str:
    """Return the Provider preset selected by one configured candidate."""

    options = candidate.get("options")
    options = options if isinstance(options, Mapping) else {}
    return str(candidate.get("provider_preset") or options.get("provider_preset") or candidate.get("provider") or "").strip().lower()


def resolve_registered_model_plugin(
    registry: PluginRegistry,
    provider_id: str,
    adapter_id: str,
):
    """Resolve a Provider from the Registry used by the active AgentSession."""

    catalog = registered_model_plugin_catalog(registry)
    by_id = {str(item.get("id") or ""): item for item in catalog}
    normalized_provider = str(provider_id or "").strip().lower()
    normalized_adapter = str(adapter_id or "").strip().lower()
    item = by_id.get(normalized_provider)
    if item is None:
        fallback_id = {
            "openai": "openai_compatible",
            "openai_responses": "openai",
        }.get(normalized_adapter, normalized_adapter)
        item = by_id.get(fallback_id)
    return registry.resolve(str(item["plugin_name"])) if item is not None else None


def _base_root(url: Any) -> str:
    normalized = str(url or "").strip().rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3].rstrip("/")
    return normalized.lower()


def public_base_url(url: Any) -> str:
    """Return a credential- and path-free endpoint identity."""

    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")
    except (TypeError, ValueError):
        return ""


def _candidate_matches_saved(
    candidate: Mapping[str, Any],
    saved: Mapping[str, Any],
) -> bool:
    return (
        str(candidate.get("id") or "") == str(saved.get("candidate_id") or "")
        and (not str(saved.get("adapter") or "") or str(candidate.get("adapter") or candidate.get("provider") or "") == str(saved.get("adapter") or ""))
        and str(candidate.get("model") or "") == str(saved.get("model") or "")
        and _base_root(candidate.get("base_url")) == _base_root(saved.get("base_url"))
    )


def configured_model_candidates(
    session_id: str = "",
    *,
    route: str = "primary",
) -> list[dict[str, Any]]:
    """Resolve one configured model route without the retired Agent client."""

    from cyrene.runtime.model_configuration import candidates_for_route
    from cyrene.runtime.settings_store import get as get_setting

    normalized_route = str(route or "primary").strip().lower()
    if normalized_route not in {"primary", "secondary", "vision", "embedding"}:
        raise ValueError(f"Unsupported model route: {route}")
    route_candidates = [dict(item) for item in candidates_for_route(normalized_route)]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for route_role, items in (
        (normalized_route, route_candidates),
        (
            "primary",
            [dict(item) for item in candidates_for_route("primary")] if normalized_route == "secondary" else [],
        ),
    ):
        for item in items:
            key = (
                str(item.get("id") or ""),
                str(item.get("adapter") or item.get("provider") or ""),
                str(item.get("model") or ""),
                _base_root(item.get("base_url")),
            )
            if key in seen:
                continue
            seen.add(key)
            item["_model_route"] = route_role
            candidates.append(item)
    normalized_session = str(session_id or "").strip()
    raw_preferences = get_setting(_SESSION_MODEL_PREFERENCE_SETTING, {})
    preferences = raw_preferences if isinstance(raw_preferences, Mapping) else {}
    raw_preference = preferences.get(normalized_session) if normalized_session and normalized_route == "primary" else None
    preference = raw_preference if isinstance(raw_preference, Mapping) else {}

    raw_affinities = get_setting(_LAST_SUCCESS_SETTING, {})
    affinities = raw_affinities if isinstance(raw_affinities, Mapping) else {}
    prepared: list[dict[str, Any]] = []
    for rank, original in enumerate(candidates):
        candidate = dict(original)
        candidate["_configured_rank"] = rank
        candidate_route = str(candidate.get("_model_route") or normalized_route)
        if candidate_route == "primary" and _candidate_matches_saved(candidate, preference):
            effort = str(preference.get("reasoning_effort") or "").strip().lower()
            if effort:
                candidate["reasoning_effort"] = effort
            candidate["_session_selected"] = True
        raw_affinity = affinities.get(f"{_SESSION_AFFINITY_PREFIX}{normalized_session}:{candidate_route}") if normalized_session else None
        affinity = raw_affinity if isinstance(raw_affinity, Mapping) else {}
        if _candidate_matches_saved(candidate, affinity):
            endpoint = str(affinity.get("endpoint") or "").strip()
            if endpoint:
                candidate["preferred_endpoint"] = endpoint
        prepared.append(candidate)
    prepared.sort(
        key=lambda item: (
            0 if str(item.get("_model_route") or "") == normalized_route else 1,
            0 if item.get("_session_selected") else 1,
            int(item.get("_configured_rank") or 0),
        )
    )
    return prepared


def configured_context_limit(
    session_id: str = "",
    *,
    route: str = "primary",
) -> int:
    """Return the smallest declared window across one automatic route."""

    limits: list[int] = []
    for candidate in configured_model_candidates(session_id, route=route):
        try:
            limit = int(
                candidate.get("context_limit") or candidate.get("ctx_limit") or 0
            )
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            limits.append(limit)
    return min(limits) if limits else 0


def resolve_session_model_candidate(session_id: str) -> dict[str, Any] | None:
    """Resolve the Provider candidate currently selected for a conversation."""

    candidates = configured_model_candidates(str(session_id or ""), route="primary")
    return dict(candidates[0]) if candidates else None


def resolve_model_profile_candidate(profile_id: str) -> dict[str, Any] | None:
    """Resolve one enabled chat-capable model profile by its durable id."""

    from cyrene.runtime.model_configuration import candidate_for_profile

    candidate = candidate_for_profile(str(profile_id or "").strip())
    if candidate is None or "chat" not in set(candidate.get("capabilities") or ()):
        return None
    return dict(candidate)


def resolve_exact_model_candidate(identity: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve exactly one configured profile from a secret-free identity."""

    from cyrene.runtime.model_configuration import candidate_for_profile, get_model_configuration

    candidate_id = str(identity.get("candidateId") or "").strip()
    profile_id = str(identity.get("profileId") or identity.get("profile_id") or "").strip()
    adapter = str(identity.get("adapter") or "").strip()
    provider = str(identity.get("provider") or "").strip()
    model = str(identity.get("model") or "").strip()
    base_url = str(identity.get("baseUrl") or "").strip()
    configuration = get_model_configuration()
    configured: list[dict[str, Any]] = []
    for profile in configuration.get("profiles") or ():
        if not isinstance(profile, Mapping):
            continue
        candidate = candidate_for_profile(
            str(profile.get("id") or ""),
            configuration,
        )
        if candidate is not None and "chat" in set(candidate.get("capabilities") or ()):
            configured.append(dict(candidate))

    matches: list[dict[str, Any]] = []
    for candidate in configured:
        if profile_id and str(candidate.get("profile_id") or candidate.get("id") or "") != profile_id:
            continue
        if candidate_id and str(candidate.get("id") or "") != candidate_id:
            continue
        candidate_adapter = str(candidate.get("adapter") or candidate.get("provider") or "")
        if adapter and candidate_adapter != adapter:
            continue
        if provider:
            candidate_provider = candidate_provider_id(candidate)
            runtime_provider = str(candidate.get("provider") or "")
            if provider not in {candidate_provider, runtime_provider}:
                continue
        if model and str(candidate.get("model") or "") != model:
            continue
        if base_url and _base_root(public_base_url(candidate.get("base_url"))) != _base_root(base_url):
            continue
        matches.append(candidate)
    if len(matches) != 1:
        return None
    effort = str(identity.get("reasoningEffort") or "").strip().lower()
    if effort:
        matches[0]["reasoning_effort"] = effort
    return matches[0]


def set_session_model_preference(
    session_id: str,
    candidate: Mapping[str, Any],
    reasoning_effort: str = "",
) -> None:
    """Persist the primary Provider selection for one conversation."""

    normalized_session = str(session_id or "").strip()
    if not normalized_session:
        return
    from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

    raw = get_setting(_SESSION_MODEL_PREFERENCE_SETTING, {})
    saved = dict(raw) if isinstance(raw, Mapping) else {}
    preference = {
        "candidate_id": str(candidate.get("id") or "").strip(),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or "").strip(),
        "model": str(candidate.get("model") or candidate.get("name") or "").strip(),
        "base_url": str(candidate.get("base_url") or "").strip(),
        "reasoning_effort": str(
            reasoning_effort or candidate.get("reasoning_effort") or ""
        ).strip().lower(),
    }
    if saved.get(normalized_session) == preference:
        return
    saved[normalized_session] = preference
    while len(saved) > _MAX_SESSION_AFFINITIES:
        saved.pop(next(iter(saved)))
    set_setting(_SESSION_MODEL_PREFERENCE_SETTING, saved)


def remember_model_success(
    session_id: str,
    candidate: Mapping[str, Any],
    endpoint: str,
    *,
    route: str = "primary",
) -> None:
    """Persist endpoint affinity for one candidate in one conversation."""

    normalized_session = str(session_id or "").strip()
    normalized_endpoint = str(endpoint or "").strip()
    if not normalized_session or not normalized_endpoint:
        return
    from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

    raw = get_setting(_LAST_SUCCESS_SETTING, {})
    saved = dict(raw) if isinstance(raw, Mapping) else {}
    normalized_route = str(route or "primary").strip().lower()
    if normalized_route not in {"primary", "secondary", "vision", "embedding"}:
        raise ValueError(f"Unsupported model route: {route}")
    key = f"{_SESSION_AFFINITY_PREFIX}{normalized_session}:{normalized_route}"
    affinity = {
        "candidate_id": str(candidate.get("id") or ""),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or ""),
        "model": str(candidate.get("model") or ""),
        "base_url": str(candidate.get("base_url") or ""),
        "endpoint": normalized_endpoint,
    }
    if saved.get(key) == affinity:
        return
    saved[key] = affinity
    scoped = [item for item in saved if str(item).startswith(_SESSION_AFFINITY_PREFIX)]
    while len(scoped) > _MAX_SESSION_AFFINITIES:
        saved.pop(scoped.pop(0), None)
    set_setting(_LAST_SUCCESS_SETTING, saved)


def candidate_identity(
    candidate: Mapping[str, Any],
    *,
    model: str = "",
    endpoint: str = "",
) -> dict[str, str]:
    """Build the secret-free identity of the Provider that actually replied."""

    return {
        "candidateId": str(candidate.get("id") or ""),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or ""),
        "provider": candidate_provider_id(candidate),
        "model": str(model or candidate.get("model") or ""),
        "baseUrl": public_base_url(candidate.get("base_url")),
        "endpoint": (public_base_url(endpoint) if endpoint.startswith(("http://", "https://")) else str(endpoint or "")),
        "reasoningEffort": str(candidate.get("reasoning_effort") or "").strip().lower(),
    }


def resolve_model_plugin(
    provider_id: str,
    adapter_id: str,
    directory: str | Path | None = None,
):
    """Resolve a provider preset first, then a generic protocol Plugin."""

    registry, failures = editable_model_registry(directory)
    catalog = model_plugin_catalog(directory)
    normalized_provider = str(provider_id or "").strip().lower()
    normalized_adapter = str(adapter_id or "").strip().lower()
    by_id = {str(item.get("id") or ""): item for item in catalog}
    item = by_id.get(normalized_provider)
    if item is None:
        fallback_id = {
            "openai": "openai_compatible",
            "openai_responses": "openai",
        }.get(normalized_adapter, normalized_adapter)
        item = by_id.get(fallback_id)
    if item is not None:
        return registry, registry.resolve(str(item["plugin_name"]))
    model_failures = [failure.error for failure in failures if failure.path.name == "cyrene_model"]
    if model_failures:
        raise RuntimeError("failed to load editable model Plugins: " + "; ".join(model_failures))
    return registry, None


__all__ = [
    "candidate_identity",
    "candidate_provider_id",
    "configured_context_limit",
    "configured_model_candidates",
    "editable_model_registry",
    "model_plugin_catalog",
    "public_base_url",
    "registered_model_plugin_catalog",
    "remember_model_success",
    "resolve_exact_model_candidate",
    "resolve_model_profile_candidate",
    "resolve_model_plugin",
    "resolve_registered_model_plugin",
    "resolve_session_model_candidate",
    "set_session_model_preference",
]
