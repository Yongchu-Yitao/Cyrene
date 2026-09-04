"""Discovery of editable model Plugins from the user Plugin directory."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from cyrene.core.plugin.activation import PluginActivationState
from cyrene.core.plugin.customization import PluginCustomizationState
from cyrene.core.plugin.registry import (
    PluginLoadFailure,
    PluginRegistry,
    default_plugin_impl_directory,
)
from cyrene.core.plugin.runtime import PluginRuntime
from cyrene.core.plugin.scopes import application_plugin_scope, application_plugin_service


def seed_builtin_plugin_directory(_root: Path) -> None:
    """Ask an application adapter to materialize its built-in packages."""

    from .native_tools import seed_builtin_plugin_directory as seed

    seed(_root)

_SESSION_MODEL_PREFERENCE_SETTING = "llm_session_model_preferences"
_LAST_SUCCESS_SETTING = "llm_last_success_endpoints"
_SESSION_AFFINITY_PREFIX = "session:"
_MAX_SESSION_AFFINITIES = 2048

_LOCK = threading.RLock()
_CACHE_ROOT: Path | None = None
_CACHE_SIGNATURE: tuple[tuple[str, int, int], ...] = ()
_CACHE_REGISTRY: PluginRegistry | None = None
_CACHE_FAILURES: tuple[PluginLoadFailure, ...] = ()
_CACHE_SETTINGS_SIGNATURE: tuple[Any, ...] = ()


def _model_configuration_port() -> Any:
    """Resolve the optional model pack's configuration port.

    Model configuration is deliberately not part of the plugin host.  This
    accessor keeps the host's routing/candidate helpers generic while avoiding
    a dependency from core code to ``plugin_impl`` (and makes a disabled model
    pack fail closed).
    """

    service = application_plugin_service("model_configuration")
    if service is None:
        raise RuntimeError("model configuration Plugin is not available")
    return service


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
    """Return an offline registry that honors persisted Plugin state.

    Live application code must use :func:`application_model_registry` so it
    cannot accidentally bypass pack activation, provider activation, or
    customizations by loading a second registry.
    """

    global _CACHE_ROOT, _CACHE_SIGNATURE, _CACHE_REGISTRY, _CACHE_FAILURES
    global _CACHE_SETTINGS_SIGNATURE
    root = Path(directory or default_plugin_impl_directory()).expanduser().resolve()
    with _LOCK:
        seed_builtin_plugin_directory(root)
        signature = _model_pack_signature(root)
        from cyrene.platform import settings_store

        enabled_plugins = settings_store.get_enabled_plugins()
        enabled_packs = settings_store.get_enabled_plugin_packs()
        raw_customizations = settings_store.get("plugin_tool_customizations", {}) or {}
        customizations = (
            raw_customizations if isinstance(raw_customizations, Mapping) else {}
        )
        settings_signature = (
            tuple(sorted(enabled_plugins.items())),
            tuple(sorted(enabled_packs.items())),
            repr(sorted((str(key), repr(value)) for key, value in customizations.items())),
        )
        if (
            _CACHE_REGISTRY is not None
            and _CACHE_ROOT == root
            and _CACHE_SIGNATURE == signature
            and _CACHE_SETTINGS_SIGNATURE == settings_signature
        ):
            return _CACHE_REGISTRY, _CACHE_FAILURES
        registry = PluginRegistry(
            include_core=False,
            activation=PluginActivationState(
                plugins=enabled_plugins,
                packs=enabled_packs,
            ),
            customizations=PluginCustomizationState(customizations),
        )
        failures = registry.load_directory(root)
        _CACHE_ROOT = root
        _CACHE_SIGNATURE = signature
        _CACHE_REGISTRY = registry
        _CACHE_FAILURES = failures
        _CACHE_SETTINGS_SIGNATURE = settings_signature
        return registry, failures


def application_model_registry(
    directory: str | Path | None = None,
) -> tuple[PluginRegistry, tuple[PluginLoadFailure, ...]]:
    """Return the live application registry, or a stateful offline fallback."""

    if directory is None:
        host = application_plugin_scope()
        if host is not None:
            return host.registry, host.load_failures
    return editable_model_registry(directory)


def application_model_runtime(registry: PluginRegistry) -> PluginRuntime:
    """Reuse the active host runtime when ``registry`` belongs to that host."""

    host = application_plugin_scope()
    if host is not None and host.registry is registry:
        return host.runtime
    return PluginRuntime(registry)


def model_plugin_catalog(
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    registry, _failures = application_model_registry(directory)
    return registered_model_plugin_catalog(registry)


def registered_model_plugin_catalog(registry: PluginRegistry) -> list[dict[str, Any]]:
    """Describe the model Providers already loaded in one live Registry."""

    result: list[dict[str, Any]] = []
    for registered in registry.list_plugins():
        plugin = registered.plugin
        if plugin.kind != "model":
            continue
        try:
            if not registry.plugin_enabled(plugin.name):
                continue
        except Exception:
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
    if "provider_preset" in candidate:
        value = candidate.get("provider_preset")
    elif "provider_preset" in options:
        value = options.get("provider_preset")
    else:
        value = candidate.get("provider")
    return str(value or "").strip().lower()


def _fallback_provider_id(adapter_id: Any) -> str:
    """Return the generic runtime Provider used when no preset is selected."""

    normalized_adapter = str(adapter_id or "").strip().lower()
    return {
        "openai": "openai_compatible",
        "openai_responses": "openai",
    }.get(normalized_adapter, normalized_adapter)


def resolve_registered_model_plugin(
    registry: PluginRegistry,
    provider_id: str,
    adapter_id: str,
):
    """Resolve a Provider from the Registry used by the active AgentSession."""

    normalized_provider = str(provider_id or "").strip().lower()
    normalized_adapter = str(adapter_id or "").strip().lower()
    all_by_id: dict[str, Any] = {}
    for registered in registry.list_plugins():
        provider = registered.plugin.metadata.get("provider")
        if registered.plugin.kind != "model" or not isinstance(provider, Mapping):
            continue
        identity = str(provider.get("id") or "").strip().lower()
        if identity:
            all_by_id[identity] = registered
    registered = all_by_id.get(normalized_provider)
    if registered is not None:
        try:
            return registry.resolve(registered.plugin.name)
        except Exception:
            return None
    if normalized_provider:
        # A named Provider that was disabled, deleted, or is no longer
        # installed must not silently cross its activation boundary by
        # falling back to a different protocol implementation.
        return None

    catalog = registered_model_plugin_catalog(registry)
    by_id = {str(item.get("id") or ""): item for item in catalog}
    fallback_id = _fallback_provider_id(normalized_adapter)
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

    model_configuration = _model_configuration_port()
    candidates_for_route = model_configuration.candidates_for_route
    from cyrene.platform.settings_store import get as get_setting

    normalized_route = str(route or "primary").strip().lower()
    if normalized_route not in {"primary", "secondary", "vision", "embedding"}:
        raise ValueError(f"Unsupported model route: {route}")
    route_candidates = [dict(item) for item in candidates_for_route(normalized_route)]
    normalized_session = str(session_id or "").strip()
    raw_preferences = get_setting(_SESSION_MODEL_PREFERENCE_SETTING, {})
    preferences = raw_preferences if isinstance(raw_preferences, Mapping) else {}
    raw_preference = preferences.get(normalized_session) if normalized_session and normalized_route == "primary" else None
    preference = raw_preference if isinstance(raw_preference, Mapping) else {}
    if preference and not any(
        _candidate_matches_saved(candidate, preference)
        for candidate in route_candidates
    ):
        candidate_for_profile = model_configuration.candidate_for_profile
        selected = candidate_for_profile(
            str(preference.get("candidate_id") or "").strip()
        )
        selected_capabilities = set((selected or {}).get("capabilities") or ())
        if (
            selected is not None
            and selected_capabilities.intersection({"chat", "vision"})
            and _candidate_matches_saved(selected, preference)
        ):
            # A Composer selection is a conversation-level override, not a
            # request to rewrite the configured automatic route.  Put the
            # selected profile first, then retain the primary route as its
            # fallback chain.
            route_candidates.insert(0, dict(selected))
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

    # Catalog consumers such as the Memory overview are informational and
    # must remain available when the optional model-configuration pack is not
    # installed (or has been disabled).  Actual model selection still uses
    # ``configured_model_candidates`` directly and therefore fails closed.
    try:
        candidates = configured_model_candidates(session_id, route=route)
    except RuntimeError as exc:
        if "model configuration Plugin is not available" not in str(exc):
            raise
        return 0
    limits: list[int] = []
    for candidate in candidates:
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

    candidate_for_profile = _model_configuration_port().candidate_for_profile
    candidate = candidate_for_profile(str(profile_id or "").strip())
    if candidate is None or "chat" not in set(candidate.get("capabilities") or ()):
        return None
    return dict(candidate)


def resolve_exact_model_candidate(identity: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve exactly one configured profile from a secret-free identity."""

    service = _model_configuration_port()
    candidate_for_profile = service.candidate_for_profile
    get_model_configuration = service.get_model_configuration

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
            configured_provider = str(candidate.get("provider") or "").strip().lower()
            runtime_provider = (
                candidate_provider
                or _fallback_provider_id(candidate_adapter)
            )
            # ``provider`` in a persisted response identity names the Provider
            # Plugin that actually handled the request.  A candidate without
            # an explicit preset therefore round-trips through the generic
            # adapter fallback (for example openai -> openai_compatible).  Keep
            # accepting the configured provider for identities written before
            # runtime Provider identities were recorded.
            if provider.lower() not in {
                candidate_provider,
                configured_provider,
                runtime_provider,
            }:
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
    from cyrene.platform.settings_store import get as get_setting, set_ as set_setting

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
    from cyrene.platform.settings_store import get as get_setting, set_ as set_setting

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
    provider_id: str = "",
) -> dict[str, str]:
    """Build the secret-free identity of the Provider that actually replied."""

    return {
        "candidateId": str(candidate.get("id") or ""),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or ""),
        "provider": str(provider_id or candidate_provider_id(candidate)).strip().lower(),
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

    registry, failures = application_model_registry(directory)
    plugin = resolve_registered_model_plugin(registry, provider_id, adapter_id)
    if plugin is not None:
        return registry, plugin
    model_failures = [failure.error for failure in failures if failure.path.name == "cyrene_model"]
    if model_failures:
        raise RuntimeError("failed to load editable model Plugins: " + "; ".join(model_failures))
    return registry, None


__all__ = [
    "application_model_registry",
    "application_model_runtime",
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
