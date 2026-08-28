"""Typed, revisioned settings service shared by UI routes and agent tools."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal

from cyrene.runtime import config_store

Namespace = Literal["runtime", "desktop", "appearance", "profile", "shortcuts"]
_NAMESPACES = frozenset({"runtime", "desktop", "appearance", "profile", "shortcuts"})

NON_MODEL_SETTINGS_TABS = (
    "general", "channels", "remote", "agents", "appearance", "voice",
    "media", "plugin-registry", "integrations",
    "shortcuts", "search", "data", "budget", "about",
)
AGENT_VISIBLE_SETTINGS_TABS = ("models",) + NON_MODEL_SETTINGS_TABS

SHORTCUT_DEFAULTS: dict[str, tuple[str, ...]] = {
    "search": ("mod", "K"),
    "new-chat": ("mod", "N"),
    "new-task": ("mod", "T"),
    "command-palette": ("mod", "shift", "P"),
    "switch-project": ("mod", "shift", "1"),
    "switch-session-1": ("mod", "1"),
    "switch-session-2": ("mod", "2"),
    "switch-session-3": ("mod", "3"),
    "next-session": ("ctrl", "Tab"),
    "previous-session": ("ctrl", "shift", "Tab"),
    "close-session-tab": ("mod", "W"),
    "toggle-sidebar": ("mod", "\\"),
    "settings": ("mod", ","),
    "composer-send": ("Enter",),
    "composer-newline": ("shift", "Enter"),
}

SUPPORTED_TIMEZONES = frozenset({
    "Pacific/Honolulu", "America/Los_Angeles", "America/Denver",
    "America/Chicago", "America/New_York", "America/Sao_Paulo",
    "UTC", "Europe/London", "Europe/Paris", "Africa/Cairo",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
    "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
})


class SettingsServiceError(ValueError):
    code = "invalid_settings"


class SettingsValidationError(SettingsServiceError):
    code = "validation_error"


class SettingsForbiddenError(SettingsServiceError):
    code = "forbidden_setting"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    namespace: Namespace
    tab: str
    value_type: str
    default: Any
    readable: bool
    writable_by_agent: bool
    secret: bool
    risk: str
    apply_mode: str
    enum: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class SettingControlSpec:
    """Coverage entry for a Settings UI control not represented by a scalar value.

    Complex resources and user ceremonies stay in their authoritative services;
    this registry makes their ownership and control path explicit instead of
    pretending they are ordinary config keys.
    """

    setting_id: str
    tab: str
    exposure: str
    capability_id: str
    risk: str
    apply_mode: str = "immediate"
    secret: bool = False


@dataclass(frozen=True, slots=True)
class PluginSettingsContribution:
    """Typed settings descriptors published by one application Plugin pack."""

    specs: tuple[SettingSpec, ...] = ()
    controls: tuple[SettingControlSpec, ...] = ()

    def setting_specs(self) -> tuple[SettingSpec, ...]:
        return self.specs

    def setting_control_specs(self) -> tuple[SettingControlSpec, ...]:
        return self.controls


def _spec(
    key: str,
    value_type: str,
    default: Any,
    *,
    namespace: Namespace = "runtime",
    tab: str = "general",
    agent: bool = False,
    risk: str = "R2",
    apply_mode: str = "immediate",
    enum: tuple[Any, ...] = (),
    minimum: float | None = None,
    maximum: float | None = None,
    readable: bool = True,
    secret: bool = False,
) -> SettingSpec:
    return SettingSpec(
        key, namespace, tab, value_type, default, readable, agent, secret, risk,
        apply_mode, enum, minimum, maximum,
    )


def plugin_setting_spec(
    key: str,
    value_type: str,
    default: Any,
    **options: Any,
) -> SettingSpec:
    """Build a setting descriptor without exposing core registry internals."""

    return _spec(key, value_type, default, **options)


SETTING_SPECS: tuple[SettingSpec, ...] = (
    _spec("app_language", "string", "", agent=True, risk="R1", enum=("", "en", "zh")),
    _spec("timezone", "string", "Asia/Shanghai", agent=True, risk="R1", enum=tuple(sorted(SUPPORTED_TIMEZONES))),
    _spec("performance_mode", "boolean", False, tab="appearance"),
    _spec("redact_secrets", "boolean", True, tab="data", readable=True),
    _spec("beta_updates", "boolean", False, tab="about"),
    _spec("auto_update", "boolean", True, tab="about"),
    _spec("budget_enabled", "boolean", False, tab="budget"),
    _spec("budget_monthly", "number", 50.0, tab="budget", minimum=0, maximum=1_000_000),
    _spec("budget_currency", "string", "CNY", tab="budget", enum=("CNY", "USD")),
    _spec("budget_action", "string", "warn", tab="budget", enum=("warn", "block")),
    _spec("budget_start_day", "integer", 1, tab="budget", minimum=1, maximum=28),
    _spec("enabled_plugins", "boolean_map", {}, tab="plugin-registry"),
    _spec("enabled_plugin_packs", "boolean_map", {}, tab="plugin-registry"),
    _spec("theme", "string", "system", namespace="appearance", tab="appearance", agent=True, risk="R1", enum=("system", "light", "dark")),
    _spec("accent", "string", "", namespace="appearance", tab="appearance", agent=True, risk="R1"),
    _spec("text_scale", "number", 1.0, namespace="appearance", tab="appearance", agent=True, risk="R1", minimum=0.8, maximum=1.4),
    _spec("background_light", "string", "", namespace="appearance", tab="appearance", agent=True, risk="R1"),
    _spec("background_dark", "string", "", namespace="appearance", tab="appearance", agent=True, risk="R1"),
    _spec("text_size", "string", "default", namespace="appearance", tab="appearance", agent=True, risk="R1", enum=("default", "large")),
    _spec("animate_pulse", "boolean", True, namespace="appearance", tab="appearance", agent=True, risk="R1"),
    _spec("appearance_migrated", "boolean", False, namespace="appearance", tab="appearance"),
    _spec("sidebar_visible", "boolean", True, namespace="appearance", tab="appearance", agent=True, risk="R1"),
    _spec("launchAtLogin", "boolean", False, namespace="desktop", agent=False, risk="R2"),
    _spec("runInBackground", "boolean", False, namespace="desktop", agent=False, risk="R2"),
    _spec("language", "string", "", namespace="desktop", agent=True, risk="R1", enum=("", "en", "zh")),
    _spec("quickChatEnabled", "boolean", False, namespace="desktop", tab="shortcuts", agent=False, risk="R2"),
    _spec("quickChatShortcut", "string", "CommandOrControl+Shift+Space", namespace="desktop", tab="shortcuts", agent=False, risk="R2"),
    _spec("profile_name", "string", "", namespace="profile", tab="agents", agent=True, risk="R1"),
    _spec("profile_bio", "string", "", namespace="profile", tab="agents", agent=True, risk="R1"),
    _spec("profile_avatar", "image_data", "", namespace="profile", tab="agents", risk="R2", readable=False),
    _spec("profile_avatar_emoji", "string", "", namespace="profile", tab="agents", agent=True, risk="R1"),
    _spec("profile_avatar_color", "color", "", namespace="profile", tab="agents", agent=True, risk="R1"),
    _spec("shortcut_bindings", "shortcut_map", {}, namespace="shortcuts", tab="shortcuts", agent=True, risk="R2"),
)

SPEC_BY_KEY = {item.key: item for item in SETTING_SPECS}


SETTING_CONTROL_SPECS: tuple[SettingControlSpec, ...] = (
    SettingControlSpec("general.desktop_notifications", "general", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("plugin-registry.plugin_packs", "plugin-registry", "direct", "cyrene.settings.update", "R2", "next_run"),
    SettingControlSpec("shortcuts.workbench_bindings", "shortcuts", "direct", "cyrene.settings.update", "R2"),
    SettingControlSpec("shortcuts.quick_chat", "shortcuts", "direct", "cyrene.settings.update", "R2"),
    SettingControlSpec("data.backup_export", "data", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("data.restore_reset", "data", "current_ui", "cyrene.ui.inspect", "R3"),
    SettingControlSpec("data.file_destination", "data", "user_ceremony", "cyrene.file_picker", "R2"),
    SettingControlSpec("budget.policy", "budget", "direct", "cyrene.settings.update", "R2"),
    SettingControlSpec("about.update_policy", "about", "direct", "cyrene.settings.update", "R2"),
    SettingControlSpec("about.update_actions", "about", "current_ui", "cyrene.ui.inspect", "R3"),
    SettingControlSpec("about.links", "about", "presentation_only", "", "R0"),
    SettingControlSpec("appearance.theme", "appearance", "direct", "cyrene.settings.update", "R1"),
    SettingControlSpec("appearance.colors", "appearance", "direct", "cyrene.settings.update", "R1"),
    SettingControlSpec("appearance.typography_motion", "appearance", "direct", "cyrene.settings.update", "R1"),
)

CONTROL_BY_ID = {item.setting_id: item for item in SETTING_CONTROL_SPECS}

if len(SPEC_BY_KEY) != len(SETTING_SPECS) or len(CONTROL_BY_ID) != len(SETTING_CONTROL_SPECS):
    raise RuntimeError("settings registry identifiers must be unique")
if any(item.tab not in AGENT_VISIBLE_SETTINGS_TABS for item in SETTING_SPECS + SETTING_CONTROL_SPECS):
    raise RuntimeError("every settings registry entry must belong to an agent-visible tab")


def _active_plugin_setting_contributions(
    provider_name: str,
    expected_type: type,
) -> tuple[Any, ...]:
    """Collect descriptors only from currently active application packs."""

    try:
        from agent.plugin import active_plugin_application_host

        host = active_plugin_application_host()
        services = host.active_services.values() if host is not None else ()
    except Exception:
        return ()
    contributions: list[Any] = []
    seen: set[int] = set()
    for service in services:
        if id(service) in seen:
            continue
        seen.add(id(service))
        try:
            provider = getattr(service, provider_name, None)
        except (AttributeError, RuntimeError):
            # Some application adapters are attached before their enabled
            # pack completes startup. They do not contribute settings until
            # their service has been bound.
            continue
        if not callable(provider):
            continue
        try:
            values = provider()
        except RuntimeError:
            continue
        if not isinstance(values, (tuple, list)) or any(
            not isinstance(item, expected_type) for item in values
        ):
            raise TypeError(
                f"Plugin service returned invalid {provider_name} contribution"
            )
        contributions.extend(values)
    return tuple(contributions)


def setting_specs() -> tuple[SettingSpec, ...]:
    specs = SETTING_SPECS + _active_plugin_setting_contributions(
        "setting_specs", SettingSpec
    )
    keys = [item.key for item in specs]
    if len(keys) != len(set(keys)):
        raise RuntimeError("settings registry keys must be unique")
    if any(item.tab not in AGENT_VISIBLE_SETTINGS_TABS for item in specs):
        raise RuntimeError("settings registry contains an unknown settings tab")
    return specs


def setting_control_specs() -> tuple[SettingControlSpec, ...]:
    controls = SETTING_CONTROL_SPECS + _active_plugin_setting_contributions(
        "setting_control_specs", SettingControlSpec
    )
    identities = [item.setting_id for item in controls]
    if len(identities) != len(set(identities)):
        raise RuntimeError("settings control identifiers must be unique")
    if any(item.tab not in AGENT_VISIBLE_SETTINGS_TABS for item in controls):
        raise RuntimeError("settings registry contains an unknown settings tab")
    return controls


def setting_spec_by_key() -> dict[str, SettingSpec]:
    return {item.key: item for item in setting_specs()}


def _normalize(spec: SettingSpec, value: Any) -> Any:
    if spec.value_type == "boolean":
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{spec.key} must be a boolean")
        normalized = value
    elif spec.value_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise SettingsValidationError(f"{spec.key} must be an integer")
        normalized = value
    elif spec.value_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SettingsValidationError(f"{spec.key} must be a number")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise SettingsValidationError(f"{spec.key} must be finite")
    elif spec.value_type == "string":
        if not isinstance(value, str):
            raise SettingsValidationError(f"{spec.key} must be a string")
        normalized = value.strip()
        if spec.key in {"app_language", "budget_action"}:
            normalized = normalized.lower()
        elif spec.key == "budget_currency":
            normalized = normalized.upper()
        if len(normalized) > 4000:
            raise SettingsValidationError(f"{spec.key} is too long")
    elif spec.value_type == "color":
        if not isinstance(value, str):
            raise SettingsValidationError(f"{spec.key} must be a string")
        normalized = value.strip()
        if normalized and (
            len(normalized) != 7
            or normalized[0] != "#"
            or any(char not in "0123456789abcdefABCDEF" for char in normalized[1:])
        ):
            raise SettingsValidationError(f"{spec.key} must be #rrggbb")
    elif spec.value_type == "image_data":
        if not isinstance(value, str):
            raise SettingsValidationError(f"{spec.key} must be a string")
        normalized = value.strip()
        if normalized and not normalized.startswith("data:image/"):
            raise SettingsValidationError(f"{spec.key} must be a data:image URL")
        if len(normalized) > 700_000:
            raise SettingsValidationError(f"{spec.key} is too large")
    elif spec.value_type == "boolean_map":
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and key and isinstance(flag, bool)
            for key, flag in value.items()
        ):
            raise SettingsValidationError(f"{spec.key} must be a map of booleans")
        normalized = dict(value)
    elif spec.value_type == "shortcut_map":
        if not isinstance(value, dict):
            raise SettingsValidationError(f"{spec.key} must be a shortcut map")
        normalized = {}
        modifiers = {"mod", "ctrl", "shift", "alt"}
        for action_id, raw_keys in value.items():
            action = str(action_id or "")
            if action not in SHORTCUT_DEFAULTS:
                raise SettingsValidationError(f"unknown shortcut action: {action}")
            if raw_keys is None:
                normalized[action] = None
                continue
            if not isinstance(raw_keys, list) or not raw_keys:
                raise SettingsValidationError(f"shortcut {action} must contain keys")
            keys: list[str] = []
            for raw_token in raw_keys:
                token = str(raw_token or "").strip()
                if not token or len(token) > 32:
                    raise SettingsValidationError(f"shortcut {action} contains an invalid key")
                if token.lower() in modifiers:
                    token = token.lower()
                elif len(token) == 1:
                    token = token.upper()
                keys.append(token)
            terminals = [token for token in keys if token not in modifiers]
            if len(terminals) != 1 or len(keys) != len(set(keys)):
                raise SettingsValidationError(f"shortcut {action} must contain one terminal key")
            normalized[action] = keys
    else:
        raise SettingsValidationError(f"unsupported setting type for {spec.key}")

    if spec.enum and normalized not in spec.enum:
        raise SettingsValidationError(f"invalid {spec.key}")
    if spec.minimum is not None and normalized < spec.minimum:
        raise SettingsValidationError(f"{spec.key} must be at least {spec.minimum:g}")
    if spec.maximum is not None and normalized > spec.maximum:
        raise SettingsValidationError(f"{spec.key} must be at most {spec.maximum:g}")
    if spec.key == "profile_name" and len(normalized) > 60:
        raise SettingsValidationError("profile_name is too long")
    if spec.key == "profile_bio" and len(normalized) > 120:
        raise SettingsValidationError("profile_bio is too long")
    if spec.key == "profile_avatar_emoji" and len(normalized) > 8:
        raise SettingsValidationError("profile_avatar_emoji is too long")
    if spec.key == "external_agent_proxy_url" and normalized:
        from cyrene.runtime.network_proxy import normalize_proxy_url

        canonical_proxy_url = normalize_proxy_url(normalized)
        if not canonical_proxy_url:
            raise SettingsValidationError(
                "external_agent_proxy_url must be an HTTP proxy address without credentials or a path"
            )
        normalized = canonical_proxy_url
    if spec.key == "quickChatShortcut" and (
        not normalized or len(normalized) > 80
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-" for char in normalized)
    ):
        raise SettingsValidationError("quickChatShortcut is not a valid accelerator")
    return normalized


def _validate_shortcut_bindings(bindings: dict[str, Any]) -> None:
    modifiers = {"mod", "ctrl", "shift", "alt"}
    effective = {
        action: list(bindings.get(action, default_keys))
        for action, default_keys in SHORTCUT_DEFAULTS.items()
    }
    seen: dict[tuple[str, ...], str] = {}
    for action, keys in effective.items():
        terminals = [token for token in keys if token not in modifiers]
        signature = tuple(sorted(token for token in keys if token in modifiers)) + (terminals[0],)
        conflict = seen.get(signature)
        if conflict:
            raise SettingsValidationError(f"shortcut {action} conflicts with {conflict}")
        seen[signature] = action


def _validate_namespace(namespace: Namespace | str | None) -> None:
    if namespace is not None and namespace not in _NAMESPACES:
        raise SettingsValidationError("invalid settings namespace")


def describe(namespace: Namespace | None = None) -> dict[str, Any]:
    _validate_namespace(namespace)
    all_specs = setting_specs()
    all_controls = setting_control_specs()
    specs = [item for item in all_specs if namespace is None or item.namespace == namespace]
    rows: list[dict[str, Any]] = []
    for item in specs:
        row = asdict(item)
        row["enum"] = list(item.enum)
        if item.value_type == "shortcut_map":
            row["patch_semantics"] = {
                "preserves_unspecified_actions": True,
                "delete_binding_with": None,
            }
        if item.secret:
            row["default"] = None
        rows.append(row)
    controls = [asdict(item) for item in all_controls] if namespace is None else []
    visible_tabs = {item.tab for item in all_specs + all_controls}
    covered_tabs = (
        tuple(tab for tab in AGENT_VISIBLE_SETTINGS_TABS if tab in visible_tabs)
        if namespace is None
        else tuple(sorted({item.tab for item in specs}))
    )
    excluded_tabs = (
        [tab for tab in AGENT_VISIBLE_SETTINGS_TABS if tab not in visible_tabs]
        if namespace is None
        else []
    )
    return {
        "schema_version": 2,
        "revision": config_store.get_settings_revision(),
        "settings": rows,
        "controls": controls,
        "covered_tabs": list(covered_tabs),
        "excluded_tabs": excluded_tabs,
        "shortcut_defaults": {
            action: list(keys) for action, keys in SHORTCUT_DEFAULTS.items()
        } if namespace in {None, "shortcuts"} else {},
    }


def read_public(namespace: Namespace | None = None) -> dict[str, Any]:
    _validate_namespace(namespace)
    if namespace == "desktop":
        raise SettingsForbiddenError("desktop settings must be read from the Electron host settings store")
    saved = config_store.get_all_settings()
    values: dict[str, Any] = {}
    for spec in setting_specs():
        if not spec.readable or (namespace is not None and spec.namespace != namespace):
            continue
        value = deepcopy(saved.get(spec.key, spec.default))
        values[spec.key] = _redact_value(spec, value)
    return {"revision": config_store.get_settings_revision(), "values": values}


def _redact_value(spec: SettingSpec, value: Any) -> Any:
    if not spec.secret:
        return value
    raw = str(value or "")
    return {"configured": bool(raw), "suffix": raw[-4:] if raw else ""}


def validate_changes(
    namespace: Namespace,
    changes: dict[str, Any],
    *,
    actor: Literal["ui", "agent"],
    approved_risks: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, SettingSpec]]:
    """Validate a whole namespace patch without writing any store."""
    _validate_namespace(namespace)
    if not isinstance(changes, dict) or not changes:
        raise SettingsValidationError("changes must be a non-empty object")

    normalized: dict[str, Any] = {}
    specs: dict[str, SettingSpec] = {}
    specs_by_key = setting_spec_by_key()
    for key, value in changes.items():
        spec = specs_by_key.get(str(key))
        if spec is None or spec.namespace != namespace:
            raise SettingsValidationError(f"unknown {namespace} setting: {key}")
        if actor == "agent" and not spec.writable_by_agent and spec.risk not in approved_risks:
            raise SettingsForbiddenError(f"agent may not update {spec.key} without exact approval")
        if actor == "agent" and spec.key == "redact_secrets":
            raise SettingsForbiddenError("agent may not change secrets redaction")
        normalized[spec.key] = _normalize(spec, value)
        specs[spec.key] = spec

    shortcut_patch = normalized.get("shortcut_bindings")
    if isinstance(shortcut_patch, dict):
        current = config_store.get_all_settings().get("shortcut_bindings", {})
        candidate = dict(current) if isinstance(current, dict) else {}
        for action, keys in shortcut_patch.items():
            if keys is None:
                candidate.pop(action, None)
            else:
                candidate[action] = keys
        _validate_shortcut_bindings(candidate)

    if actor == "agent":
        pack_changes = normalized.get("enabled_plugin_packs")
        plugin_changes = normalized.get("enabled_plugins")
        if isinstance(pack_changes, dict) or isinstance(plugin_changes, dict):
            from agent.plugin import active_plugin_application_host
            from agent.plugin.execution import current_plugin_execution

            host = active_plugin_application_host()
            if host is None:
                raise SettingsForbiddenError("Plugin activation is unavailable")
            registered_packs = {pack.id for pack in host.registry.list_packs()}
            registered_plugins = {
                item.plugin.name for item in host.registry.list_plugins()
            }
            execution = current_plugin_execution()
            executing_plugin = (
                str(execution.call.name)
                if execution is not None
                else ""
            )
            executing_pack = ""
            if executing_plugin:
                try:
                    registered = execution.runtime.registry.registered(
                        executing_plugin
                    )
                    executing_pack = str(registered.pack_id or "")
                except Exception:
                    executing_pack = ""
            if (plugin_changes or {}).get(executing_plugin) is False:
                raise SettingsForbiddenError(
                    "an executing Plugin cannot disable itself"
                )
            if executing_pack and (pack_changes or {}).get(executing_pack) is False:
                raise SettingsForbiddenError(
                    "an executing Plugin cannot disable its own pack"
                )
            for pack_id in (pack_changes or {}):
                if pack_id not in registered_packs:
                    raise SettingsValidationError(
                        f"unknown Plugin pack: {pack_id}"
                    )
                if host.registry.pack_locked(pack_id):
                    raise SettingsForbiddenError(
                        f"locked Plugin pack cannot change availability: {pack_id}"
                    )
            for plugin_id in (plugin_changes or {}):
                if plugin_id not in registered_plugins:
                    raise SettingsValidationError(
                        f"unknown Plugin: {plugin_id}"
                    )
                if host.registry.plugin_locked(plugin_id):
                    raise SettingsForbiddenError(
                        f"locked Plugin cannot change availability: {plugin_id}"
                    )
    return normalized, specs


def update(
    namespace: Namespace,
    changes: dict[str, Any],
    *,
    actor: Literal["ui", "agent"],
    expected_revision: int | None = None,
    approved_risks: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if namespace == "desktop":
        raise SettingsForbiddenError("desktop settings must use the Electron host settings store")
    if namespace == "shortcuts" and expected_revision is None:
        raise SettingsValidationError("shortcut settings require expected_revision")
    if expected_revision is not None:
        actual_revision = config_store.get_settings_revision()
        if expected_revision != actual_revision:
            raise config_store.SettingsRevisionConflict(expected_revision, actual_revision)
    normalized, specs = validate_changes(
        namespace, changes, actor=actor, approved_risks=approved_risks,
    )

    merge_mapping_keys = frozenset(
        key for key, spec in specs.items()
        if spec.value_type in {"boolean_map", "shortcut_map"}
    )
    revision, before, all_settings = config_store.patch_settings_atomic(
        normalized,
        expected_revision=expected_revision,
        merge_mapping_keys=merge_mapping_keys,
        merge_mapping_delete_none_keys=frozenset({"shortcut_bindings"}),
    )
    before = {
        key: deepcopy(specs[key].default) if before[key] is None else before[key]
        for key in normalized
    }
    after = {key: deepcopy(all_settings[key]) for key in normalized}
    diff = {
        key: {
            "before": _redact_value(specs[key], before[key]),
            "after": _redact_value(specs[key], after[key]),
        }
        for key in normalized
        if before[key] != after[key]
    }
    apply_modes = {specs[key].apply_mode for key in normalized}
    apply_mode = "next_run" if "next_run" in apply_modes else "immediate"
    return {
        "revision": revision,
        "changed": list(normalized),
        "diff": diff,
        "apply_mode": apply_mode,
    }


__all__ = [
    "SETTING_SPECS", "SPEC_BY_KEY", "SETTING_CONTROL_SPECS", "CONTROL_BY_ID",
    "AGENT_VISIBLE_SETTINGS_TABS", "NON_MODEL_SETTINGS_TABS", "SHORTCUT_DEFAULTS", "SettingSpec", "SettingControlSpec", "SettingsForbiddenError",
    "PluginSettingsContribution", "SettingsServiceError", "SettingsValidationError", "describe", "plugin_setting_spec", "read_public", "update",
    "setting_control_specs", "setting_spec_by_key", "setting_specs",
    "validate_changes",
]
