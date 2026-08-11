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
    "general", "channels", "remote", "agents", "appearance", "capabilities",
    "skills", "shortcuts", "data", "budget", "about",
)

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


SETTING_SPECS: tuple[SettingSpec, ...] = (
    _spec("spawn_policy", "string", "conservative", tab="agents", enum=("aggressive", "conservative", "off"), apply_mode="next_run"),
    _spec("heartbeat_interval", "integer", 1800, tab="agents", minimum=60, maximum=86400),
    _spec("agent_proactive", "boolean", True, tab="agents", apply_mode="next_run"),
    _spec("app_language", "string", "", agent=True, risk="R1", enum=("", "en", "zh")),
    _spec("timezone", "string", "Asia/Shanghai", agent=True, risk="R1", enum=tuple(sorted(SUPPORTED_TIMEZONES))),
    _spec("notify_telegram", "boolean", True, tab="channels", agent=True, risk="R1"),
    _spec("notify_wechat", "boolean", True, tab="channels", agent=True, risk="R1"),
    _spec("redact_secrets", "boolean", True, tab="data", readable=True),
    _spec("beta_updates", "boolean", False, tab="about"),
    _spec("auto_update", "boolean", True, tab="about"),
    _spec("budget_enabled", "boolean", False, tab="budget"),
    _spec("codex_budget_enabled", "boolean", True, tab="budget"),
    _spec("budget_monthly", "number", 50.0, tab="budget", minimum=0, maximum=1_000_000),
    _spec("budget_currency", "string", "CNY", tab="budget", enum=("CNY", "USD")),
    _spec("budget_action", "string", "warn", tab="budget", enum=("warn", "block")),
    _spec("budget_mode", "string", "normal", tab="budget", enum=("economy", "normal")),
    _spec("budget_start_day", "integer", 1, tab="budget", minimum=1, maximum=28),
    _spec("subagent_execution_max_tool_calls", "integer", 200, tab="agents", minimum=1, maximum=5000),
    _spec("subagent_execution_max_wall_seconds", "integer", 1800, tab="agents", minimum=30, maximum=86400),
    _spec("subagent_execution_no_progress_turns", "integer", 3, tab="agents", minimum=1, maximum=20),
    _spec("subagent_execution_checkpoint_calls", "integer", 20, tab="agents", minimum=1, maximum=500),
    _spec("subagent_execution_max_cost_usd", "number", 5.0, tab="agents", minimum=0, maximum=1000),
    _spec("subagent_execution_max_context_tokens", "integer", 0, tab="agents", minimum=0, maximum=4_000_000),
    _spec("subagent_discussion_max_rounds", "integer", 5, tab="agents", minimum=1, maximum=50),
    _spec("subagent_discussion_max_messages_per_agent", "integer", 4, tab="agents", minimum=1, maximum=50),
    _spec("subagent_discussion_max_total_messages", "integer", 20, tab="agents", minimum=1, maximum=500),
    _spec("subagent_discussion_max_message_chars", "integer", 2000, tab="agents", minimum=100, maximum=20000),
    _spec("subagent_discussion_max_wall_seconds", "integer", 600, tab="agents", minimum=30, maximum=86400),
    _spec("subagent_discussion_max_tool_calls", "integer", 50, tab="agents", minimum=1, maximum=1000),
    _spec("subagent_discussion_no_new_info_rounds", "integer", 2, tab="agents", minimum=1, maximum=20),
    _spec("enabled_tools", "boolean_map", {}, tab="capabilities"),
    _spec("enabled_tool_packs", "boolean_map", {}, tab="capabilities", apply_mode="next_run"),
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
    SettingControlSpec("general.map_provider", "general", "current_ui", "cyrene.ui.inspect", "R1"),
    SettingControlSpec("general.amap_api_key", "general", "user_ceremony", "cyrene.secret.input", "R3", secret=True),
    SettingControlSpec("general.zotero", "general", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("general.zotero_test", "general", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("general.zotero_import", "general", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("channels.telegram_token", "channels", "user_ceremony", "cyrene.secret.input", "R3", secret=True),
    SettingControlSpec("channels.wechat_login", "channels", "user_ceremony", "cyrene.ui.inspect", "R3"),
    SettingControlSpec("channels.wechat_runtime", "channels", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("remote.service", "remote", "existing_capability", "remote_tools", "R2"),
    SettingControlSpec("remote.pairing", "remote", "user_ceremony", "remote_tools", "R3", secret=True),
    SettingControlSpec("remote.peer_grants", "remote", "existing_capability", "remote_tools", "R3"),
    SettingControlSpec("agents.soul", "agents", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("capabilities.voice_settings", "capabilities", "current_ui", "cyrene.ui.inspect", "R2"),
    SettingControlSpec("capabilities.voice_profile", "capabilities", "user_ceremony", "cyrene.file_picker", "R3"),
    SettingControlSpec("capabilities.tool_packages", "capabilities", "direct", "cyrene.settings.update", "R2", "next_run"),
    SettingControlSpec("capabilities.mcp_servers", "capabilities", "current_ui", "cyrene.ui.inspect", "R2", "restart_required"),
    SettingControlSpec("skills.installed", "skills", "existing_capability", "skill_tools", "R2"),
    SettingControlSpec("skills.install_picker", "skills", "user_ceremony", "cyrene.file_picker", "R2"),
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
if any(item.tab not in NON_MODEL_SETTINGS_TABS for item in SETTING_SPECS + SETTING_CONTROL_SPECS):
    raise RuntimeError("every settings registry entry must belong to a non-model tab")
if {item.tab for item in SETTING_SPECS + SETTING_CONTROL_SPECS} != set(NON_MODEL_SETTINGS_TABS):
    raise RuntimeError("settings registry must cover every non-model tab")


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
        if spec.key in {"app_language", "budget_action", "budget_mode"}:
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
    specs = [item for item in SETTING_SPECS if namespace is None or item.namespace == namespace]
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
    controls = [asdict(item) for item in SETTING_CONTROL_SPECS] if namespace is None else []
    covered_tabs = NON_MODEL_SETTINGS_TABS if namespace is None else tuple(sorted({item.tab for item in specs}))
    return {
        "schema_version": 2,
        "revision": config_store.get_settings_revision(),
        "settings": rows,
        "controls": controls,
        "covered_tabs": list(covered_tabs),
        "excluded_tabs": ["models"],
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
    for spec in SETTING_SPECS:
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
    for key, value in changes.items():
        spec = SPEC_BY_KEY.get(str(key))
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
        pack_changes = normalized.get("enabled_tool_packs")
        if isinstance(pack_changes, dict) and "cyrene_tools" in pack_changes:
            raise SettingsForbiddenError("cyrene_tools cannot change its own availability")
        tool_changes = normalized.get("enabled_tools")
        if isinstance(tool_changes, dict) and any(str(key).startswith("Cyrene") for key in tool_changes):
            raise SettingsForbiddenError("Cyrene tools cannot change their own availability")
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
    "NON_MODEL_SETTINGS_TABS", "SHORTCUT_DEFAULTS", "SettingSpec", "SettingControlSpec", "SettingsForbiddenError",
    "SettingsServiceError", "SettingsValidationError", "describe", "read_public", "update",
    "validate_changes",
]
