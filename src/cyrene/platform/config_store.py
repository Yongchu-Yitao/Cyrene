"""Encrypted unified runtime configuration store.

All sensitive and user-editable configuration lives in a single
Fernet-encrypted JSON blob under DATA_DIR / "config.enc".
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from copy import deepcopy
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cyrene.platform import paths as app_paths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution (self-contained — do NOT import from cyrene.config)
# ---------------------------------------------------------------------------


_PATHS = app_paths.resolve_app_paths()
_BASE_DIR = _PATHS.runtime_base

DATA_DIR = _BASE_DIR / "data"
_ENCRYPTED_PATH = DATA_DIR / "config.enc"
_KEY_PATH = DATA_DIR / ".config_key"

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

_DEFAULT_ENV: dict[str, str] = {
    "ASSISTANT_NAME": "Cyrene",
    "MAX_TOOL_OUTPUT_CHARS": "0",
    "WEB_PORT": "4242",
}

_REMOVED_ENV_KEYS = frozenset({
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
})
_REMOVED_SETTING_KEYS = frozenset({
    "budget_mode",
    "models",
    "custom_models",
    "codex_model",
    "model_source",
    "vision_models",
    "secondary_model",
    "embedding",
})

# Plugin defaults belong to the contribution metadata. This mapping contains
# only persisted user overrides and intentionally knows no Plugin names.
_DEFAULT_ENABLED_PLUGINS: dict[str, bool] = {}

_DEFAULT_SETTINGS: dict = {
    "write_permission_mode": "workspace_only",
    # Optional Plugin-owned configuration is intentionally absent until first
    # access; its owning application service performs one-time seeding and a
    # subsequently saved empty graph remains empty.
    "enabled_plugins": _DEFAULT_ENABLED_PLUGINS,
    "enabled_plugin_packs": {},
    # Per-entry Workbench visibility overrides.  Entries are visible by
    # default; Plugin packs own the declarations and this map only records the
    # user's explicit choice.
    "workbench_entry_visibility": {},
    "plugin_tool_customizations": {},
    "app_language": "",
    "timezone": "Asia/Shanghai",
    # Renderer-wide low-overhead visual profile. The frontend mirrors this
    # value into localStorage so it can apply before the first React paint.
    "performance_mode": False,
    # Custom/API models use a currency budget. Keep the persisted defaults in
    # sync with the values shown by BudgetPanel so enabling the switch without
    # editing the amount still creates a real, visible budget.
    "budget_enabled": False,
    "budget_monthly": 50.0,
    "budget_currency": "CNY",
    "budget_action": "warn",
    "budget_start_day": 1,
    "redact_secrets": True,
    "shortcut_bindings": {},
}

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Key storage
#
# The Fernet key is deliberately stored next to the encrypted config in a
# permission-restricted file. Do not use an OS keyring here: development and
# packaged processes can have different keyring identities while sharing the
# same DATA_DIR, which previously caused valid config to be mistaken for
# corruption and replaced from a stale legacy backup.
# ---------------------------------------------------------------------------


def _store_key(key: bytes) -> bytes:
    """Create the local key file once and return the winning key.

    Exclusive creation prevents two processes on a first launch from choosing
    different keys for the same config directory.
    """
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _KEY_PATH.read_bytes()
        os.chmod(_KEY_PATH, 0o600)
        return existing
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _get_fernet() -> Fernet:
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes()
        os.chmod(_KEY_PATH, 0o600)
        try:
            return Fernet(key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid local config key: {_KEY_PATH}") from exc

    # Never generate a replacement key for an existing encrypted config.
    if _ENCRYPTED_PATH.exists():
        raise RuntimeError(
            f"Local config key is missing: {_KEY_PATH}; "
            f"encrypted config was preserved at {_ENCRYPTED_PATH}"
        )

    key = Fernet.generate_key()
    key = _store_key(key)
    return Fernet(key)


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet()
    return _fernet


def _next_recovery_path(reason: str) -> Path:
    """Return a non-destructive backup path for an unreadable config."""
    base = _ENCRYPTED_PATH.with_name(f"{_ENCRYPTED_PATH.name}.{reason}.bak")
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = base.with_name(f"{base.name}.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _recover_config_without_key() -> dict:
    """Preserve an undecryptable store and start with a fresh local key."""
    global _fernet, _initialized

    backup_path = _next_recovery_path("missing-key")
    _ENCRYPTED_PATH.replace(backup_path)
    logger.error(
        "Local config key is missing; preserved unreadable config at %s and "
        "started with default settings",
        backup_path,
    )

    key = _store_key(Fernet.generate_key())
    _fernet = Fernet(key)
    config = {
        "env": deepcopy(_DEFAULT_ENV),
        "settings": deepcopy(_DEFAULT_SETTINGS),
        "settings_revision": 0,
    }
    _persist(config)
    _initialized = True
    return config


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict | None = None
_initialized: bool = False


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def _initialize_if_needed() -> dict:
    global _initialized
    if _initialized:
        return _cache or {
            "env": dict(_DEFAULT_ENV),
            "settings": dict(_DEFAULT_SETTINGS),
            "settings_revision": 0,
        }

    config = {
        "env": deepcopy(_DEFAULT_ENV),
        "settings": deepcopy(_DEFAULT_SETTINGS),
        "settings_revision": 0,
    }
    _generate_key_if_missing()
    _persist(config)
    _initialized = True
    logger.info("Initialized encrypted config store at %s", _ENCRYPTED_PATH)
    return config


def _generate_key_if_missing() -> None:
    """Ensure a local encryption key exists for a new config store."""
    if _KEY_PATH.exists():
        return
    if _ENCRYPTED_PATH.exists():
        raise RuntimeError(
            f"Refusing to replace missing key for existing config: {_ENCRYPTED_PATH}"
        )
    key = Fernet.generate_key()
    key = _store_key(key)
    global _fernet
    _fernet = Fernet(key)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_PERSIST_LOCK = threading.RLock()


def _persist(config: dict) -> None:
    # Settings routes may persist from worker threads so the async API loop
    # remains responsive. Serialize the shared temp-file/replace sequence.
    with _PERSIST_LOCK:
        _ENCRYPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = _cipher().encrypt(plain)
        tmp = _ENCRYPTED_PATH.with_suffix(".enc.tmp")
        try:
            tmp.write_bytes(encrypted)
            tmp.replace(_ENCRYPTED_PATH)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def _read_config() -> dict:
    if not _ENCRYPTED_PATH.exists():
        return _initialize_if_needed()
    if not _KEY_PATH.exists():
        return _recover_config_without_key()
    try:
        encrypted = _ENCRYPTED_PATH.read_bytes()
        plain = _cipher().decrypt(encrypted)
        config = json.loads(plain.decode("utf-8"))
        return _sanitize_loaded_config(config)
    except InvalidToken as exc:
        raise RuntimeError(
            f"Cannot decrypt config with local key {_KEY_PATH}; "
            f"existing config was preserved at {_ENCRYPTED_PATH}"
        ) from exc


def _sanitize_loaded_config(config: dict) -> dict:
    """Validate the encrypted document and discard retired namespaces."""
    if not isinstance(config, dict):
        raise RuntimeError("Encrypted config must contain a JSON object")
    env = config.setdefault("env", {})
    settings = config.setdefault("settings", {})
    if not isinstance(env, dict) or not isinstance(settings, dict):
        raise RuntimeError("Encrypted config env and settings must be objects")
    changed = False

    revision = config.get("settings_revision", 0)
    if (
        "settings_revision" not in config
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
    ):
        config["settings_revision"] = 0
        changed = True

    for key in _REMOVED_ENV_KEYS:
        if key in env:
            env.pop(key)
            os.environ.pop(key, None)
            changed = True

    for key in _REMOVED_SETTING_KEYS:
        if key in settings:
            settings.pop(key)
            changed = True

    if changed:
        _persist(config)
        logger.info("Removed retired fields from encrypted config")

    return config


def _ensure_loaded() -> dict:
    global _cache
    with _PERSIST_LOCK:
        if _cache is None:
            _cache = _read_config()
        return _cache


_PORTABLE_SECRET_PARTS = frozenset({
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
})
_PORTABLE_SECRET_NAMES = frozenset({
    "apikey",
    "authorizationheader",
    "signingkey",
})
_PORTABLE_PUBLIC_STATE_SUFFIXES = ("_configured", "_requires_reentry")


def _portable_secret_name(value: object) -> bool:
    raw = str(value or "")
    lowered = raw.lower()
    if lowered.endswith(_PORTABLE_PUBLIC_STATE_SUFFIXES):
        return False
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw).lower()
    parts = {
        part
        for part in re.split(r"[^a-z0-9]+", snake_case)
        if part
    }
    return bool(parts.intersection(_PORTABLE_SECRET_PARTS)) or lowered in (
        _PORTABLE_SECRET_NAMES
    )


def _redact_portable_settings(value: object, *, replacement: object = "") -> object:
    """Detach and redact credential-shaped values from any Plugin setting."""

    if isinstance(value, dict):
        return {
            str(key): (
                deepcopy(replacement)
                if _portable_secret_name(key)
                else _redact_portable_settings(item, replacement=replacement)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _redact_portable_settings(item, replacement=replacement)
            for item in value
        ]
    return deepcopy(value)


def export_snapshot() -> dict:
    """Return a portable, detached snapshot of all configuration.

    The on-disk ``config.enc`` file cannot be copied between installations
    because its Fernet key is installation-local.
    Backup archives therefore carry this logical snapshot and re-encrypt it
    with the destination installation's key during restore.
    """
    snapshot = deepcopy(_ensure_loaded())
    # Runtime credentials remain encrypted locally but are intentionally not
    # portable. Keep the declarations and replace values with explicit
    # references so restore never transports plaintext secrets.
    settings = snapshot.get("settings")
    if isinstance(settings, dict):
        # Plugin settings are intentionally open-ended. Apply one generic
        # recursive policy so portable backup never depends on, or has to name,
        # an optional Plugin implementation.
        snapshot["settings"] = _redact_portable_settings(settings)
    return snapshot


def _normalize_restored_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise ValueError("configuration snapshot must be an object")
    env = snapshot.get("env")
    settings = snapshot.get("settings")
    if not isinstance(env, dict) or not isinstance(settings, dict):
        raise ValueError("configuration snapshot must contain env and settings objects")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise ValueError("configuration env values must be strings")
    normalized_env = {
        key: value
        for key, value in env.items()
        if key not in _REMOVED_ENV_KEYS
    }
    revision = snapshot.get("settings_revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("configuration settings_revision must be a non-negative integer")
    normalized_settings = {
        key: deepcopy(value)
        for key, value in settings.items()
        if key not in _REMOVED_SETTING_KEYS
    }
    return {
        "env": normalized_env,
        "settings": normalized_settings,
        "settings_revision": revision,
    }


def prepare_restored_snapshot(snapshot: dict) -> tuple[dict, bytes]:
    """Validate *snapshot* and encrypt it with this installation's key."""
    normalized = _normalize_restored_snapshot(snapshot)

    plain = json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8")
    return normalized, _cipher().encrypt(plain)


def activate_restored_snapshot(snapshot: dict) -> None:
    """Make an already-persisted restored snapshot active in this process."""
    normalized = _normalize_restored_snapshot(snapshot)
    global _cache, _initialized
    with _PERSIST_LOCK:
        previous_env = set((_cache or {}).get("env", {}))
        _cache = normalized
        _initialized = True
    restored_env = normalized["env"]
    for key in previous_env - set(restored_env):
        os.environ.pop(key, None)
    for key, value in restored_env.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Public API — Env
# ---------------------------------------------------------------------------


def get_env(key: str, default: str = "") -> str:
    if key in _REMOVED_ENV_KEYS:
        raise ValueError(f"Environment setting `{key}` has been removed.")
    config = _ensure_loaded()
    return config.get("env", {}).get(key, _DEFAULT_ENV.get(key, default))


def set_env(key: str, value: str) -> None:
    if key in _REMOVED_ENV_KEYS:
        raise ValueError(f"Environment setting `{key}` has been removed.")
    with _PERSIST_LOCK:
        config = deepcopy(_ensure_loaded())
        config.setdefault("env", {})[key] = str(value)
        _persist(config)
        global _cache
        _cache = config
    os.environ[key] = str(value)


def set_env_many(updates: dict[str, str]) -> None:
    removed = sorted(set(updates) & _REMOVED_ENV_KEYS)
    if removed:
        raise ValueError(
            "Environment setting(s) have been removed: " + ", ".join(removed)
        )
    with _PERSIST_LOCK:
        config = deepcopy(_ensure_loaded())
        for key, value in updates.items():
            config.setdefault("env", {})[key] = str(value)
        _persist(config)
        global _cache
        _cache = config
    for key, value in updates.items():
        os.environ[key] = str(value)


def get_all_env() -> dict[str, str]:
    config = _ensure_loaded()
    return dict(config.get("env", {}))


# ---------------------------------------------------------------------------
# Public API — Settings
# ---------------------------------------------------------------------------


def get_setting(key: str, default=None):
    if key in _REMOVED_SETTING_KEYS:
        raise ValueError(f"Setting `{key}` has been removed.")
    config = _ensure_loaded()
    return config.get("settings", {}).get(key, _DEFAULT_SETTINGS.get(key, default))


def set_setting(key: str, value) -> None:
    update_settings_atomic({key: value})


class SettingsRevisionConflict(ValueError):
    """Raised when a settings compare-and-swap revision is stale."""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"settings revision conflict: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


def get_settings_revision() -> int:
    """Return the monotonic revision of the persisted settings namespace."""
    with _PERSIST_LOCK:
        value = _ensure_loaded().get("settings_revision", 0)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def update_settings_atomic(
    updates: dict[str, object],
    *,
    expected_revision: int | None = None,
    remove_setting_keys: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object]]:
    """Persist one detached settings patch with compare-and-swap semantics.

    Callers must validate and normalize the complete patch before entering this
    function.  The cache is replaced only after the encrypted temp-file replace
    succeeds, so validation or persistence failures leave memory and disk intact.
    """
    revision, _before, settings = patch_settings_atomic(
        updates,
        expected_revision=expected_revision,
        remove_setting_keys=remove_setting_keys,
    )
    return revision, settings


def mutate_setting_atomic(
    key: str,
    mutator,
    *,
    companion_updates=None,
) -> tuple[int, object, object]:
    """Atomically transform one setting without a global revision precondition.

    The callback receives a detached copy of the setting's current value and
    must return its complete replacement.  The read, transformation, encrypted
    write, and cache swap share one lock, so callers can safely implement
    field-level patches without losing unrelated writes. ``companion_updates``
    may derive a small related settings patch from the replacement and persists
    it under that same lock. An idempotent transformation whose companion values
    are also unchanged does not advance the global settings revision.
    """

    if not isinstance(key, str) or not key:
        raise ValueError("setting key must be a non-empty string")
    if key in _REMOVED_SETTING_KEYS:
        raise ValueError(f"Setting has been removed: {key}")
    if not callable(mutator):
        raise TypeError("setting mutator must be callable")
    if companion_updates is not None and not callable(companion_updates):
        raise TypeError("companion_updates must be callable or None")
    with _PERSIST_LOCK:
        current = _ensure_loaded()
        actual_revision = int(current.get("settings_revision", 0) or 0)
        candidate = deepcopy(current)
        candidate_settings = candidate.setdefault("settings", {})
        before = deepcopy(candidate_settings.get(key, _DEFAULT_SETTINGS.get(key)))
        next_value = deepcopy(mutator(deepcopy(before)))
        companions = (
            companion_updates(deepcopy(next_value))
            if companion_updates is not None
            else {}
        )
        if not isinstance(companions, dict):
            raise TypeError("companion_updates must return an object")
        for companion_key in companions:
            if not isinstance(companion_key, str) or not companion_key:
                raise ValueError("companion setting keys must be non-empty strings")
            if companion_key == key:
                raise ValueError("companion updates cannot replace the mutated setting")
            if companion_key in _REMOVED_SETTING_KEYS:
                raise ValueError(f"Setting has been removed: {companion_key}")
        companions_changed = any(
            candidate_settings.get(companion_key, _DEFAULT_SETTINGS.get(companion_key))
            != companion_value
            for companion_key, companion_value in companions.items()
        )
        if next_value == before and not companions_changed:
            return actual_revision, before, deepcopy(before)
        candidate_settings[key] = next_value
        for companion_key, companion_value in companions.items():
            candidate_settings[companion_key] = deepcopy(companion_value)
        next_revision = actual_revision + 1
        candidate["settings_revision"] = next_revision
        _persist(candidate)
        global _cache
        _cache = candidate
        return next_revision, before, deepcopy(next_value)


def update_settings_and_env_atomic(
    settings_updates: dict[str, object],
    env_updates: dict[str, str],
    *,
    expected_revision: int | None = None,
    remove_setting_keys: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object]]:
    """Atomically replace settings and process-environment configuration."""

    if not isinstance(settings_updates, dict) or not settings_updates:
        raise ValueError("settings patch must be a non-empty object")
    if not isinstance(env_updates, dict):
        raise ValueError("environment patch must be an object")
    removed_settings = sorted(set(settings_updates) & _REMOVED_SETTING_KEYS)
    if removed_settings:
        raise ValueError(
            "Setting(s) have been removed: " + ", ".join(removed_settings)
        )
    removed = sorted(set(env_updates) & _REMOVED_ENV_KEYS)
    if removed:
        raise ValueError(
            "Environment setting(s) have been removed: " + ", ".join(removed)
        )
    normalized_env = {str(key): str(value) for key, value in env_updates.items()}
    with _PERSIST_LOCK:
        current = _ensure_loaded()
        actual_revision = int(current.get("settings_revision", 0) or 0)
        if expected_revision is not None and expected_revision != actual_revision:
            raise SettingsRevisionConflict(expected_revision, actual_revision)
        candidate = deepcopy(current)
        candidate_settings = candidate.setdefault("settings", {})
        for key, value in settings_updates.items():
            if not isinstance(key, str) or not key:
                raise ValueError("settings patch keys must be non-empty strings")
            candidate_settings[key] = deepcopy(value)
        for key in remove_setting_keys:
            candidate_settings.pop(key, None)
        candidate.setdefault("env", {}).update(normalized_env)
        next_revision = actual_revision + 1
        candidate["settings_revision"] = next_revision
        _persist(candidate)
        global _cache
        _cache = candidate
        snapshot = deepcopy(candidate_settings)
    for key, value in normalized_env.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    return next_revision, snapshot


def patch_settings_atomic(
    updates: dict[str, object],
    *,
    expected_revision: int | None = None,
    merge_mapping_keys: frozenset[str] = frozenset(),
    merge_mapping_delete_none_keys: frozenset[str] = frozenset(),
    remove_setting_keys: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object], dict[str, object]]:
    """Apply a patch and return the exact in-lock before/after values.

    ``merge_mapping_keys`` is used for product-level map patches such as tool
    switches. Keys in ``merge_mapping_delete_none_keys`` additionally treat a
    nested ``None`` as an explicit deletion. The merge happens while holding
    the same lock as CAS and disk replacement, preventing one client from
    erasing another client's update to a different map entry.
    """
    if not isinstance(updates, dict) or not updates:
        raise ValueError("settings patch must be a non-empty object")
    removed = sorted(set(updates) & _REMOVED_SETTING_KEYS)
    if removed:
        raise ValueError("Setting(s) have been removed: " + ", ".join(removed))
    with _PERSIST_LOCK:
        current = _ensure_loaded()
        actual_revision = int(current.get("settings_revision", 0) or 0)
        if expected_revision is not None and expected_revision != actual_revision:
            raise SettingsRevisionConflict(expected_revision, actual_revision)
        candidate = deepcopy(current)
        candidate_settings = candidate.setdefault("settings", {})
        before: dict[str, object] = {}
        for key, value in updates.items():
            if not isinstance(key, str) or not key:
                raise ValueError("settings patch keys must be non-empty strings")
            before[key] = deepcopy(candidate_settings.get(key, _DEFAULT_SETTINGS.get(key)))
            next_value = deepcopy(value)
            if key in merge_mapping_keys:
                current_map = candidate_settings.get(key, _DEFAULT_SETTINGS.get(key, {}))
                if not isinstance(current_map, dict) or not isinstance(next_value, dict):
                    raise ValueError(f"settings patch value for {key} must be an object")
                merged = deepcopy(current_map)
                for nested_key, nested_value in next_value.items():
                    if key in merge_mapping_delete_none_keys and nested_value is None:
                        merged.pop(nested_key, None)
                    else:
                        merged[nested_key] = nested_value
                next_value = merged
            candidate_settings[key] = next_value
        for key in remove_setting_keys:
            candidate_settings.pop(key, None)
        next_revision = actual_revision + 1
        candidate["settings_revision"] = next_revision
        _persist(candidate)
        global _cache
        _cache = candidate
        return next_revision, before, deepcopy(candidate_settings)


def get_all_settings() -> dict:
    config = _ensure_loaded()
    settings = dict(_DEFAULT_SETTINGS)
    saved = config.get("settings", {})
    for key, val in saved.items():
        if key in settings and isinstance(settings[key], dict) and isinstance(val, dict):
            settings[key] = {**settings[key], **val}
        else:
            settings[key] = val
    return settings


def reset_all() -> None:
    global _cache
    with _PERSIST_LOCK:
        current = _ensure_loaded()
        current_revision = int(current.get("settings_revision", 0) or 0)
        previous_env_keys = set(current.get("env", {}))
        candidate = {
            "env": deepcopy(_DEFAULT_ENV),
            "settings": deepcopy(_DEFAULT_SETTINGS),
            "settings_revision": current_revision + 1,
        }
        _persist(candidate)
        _cache = candidate
    # Persisted defaults and the live process must cross the reset boundary
    # together. Otherwise code reading os.environ keeps the pre-reset model or
    # credentials until the backend is restarted.
    for key in previous_env_keys - set(_DEFAULT_ENV):
        os.environ.pop(key, None)
    for key, value in _DEFAULT_ENV.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Specific settings accessors (used by callers that need typed returns)
# ---------------------------------------------------------------------------


def _parse_ctx_str(ctx_str: str) -> int:
    """Parse '128K' / '1M' / '200000' into an int token count. 0 if unknown."""
    s = str(ctx_str or "").strip().upper()
    if not s:
        return 0
    try:
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(float(s))
    except ValueError:
        return 0


def _profile_ctx_limit(profile_id: str) -> int:
    """Read one profile window through the optional model service port."""
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        return 0
    configuration = service.get_model_configuration()
    if not isinstance(configuration, dict):
        return 0
    profiles = configuration.get("profiles")
    connections = configuration.get("connections")
    if not isinstance(profiles, list) or not isinstance(connections, list):
        return 0
    profile = next(
        (
            item
            for item in profiles
            if isinstance(item, dict)
            and str(item.get("id") or "") == profile_id
        ),
        None,
    )
    if profile is None or profile.get("enabled") is False:
        return 0
    connection_id = str(profile.get("connection_id") or "")
    connection = next(
        (
            item
            for item in connections
            if isinstance(item, dict)
            and str(item.get("id") or "") == connection_id
        ),
        None,
    )
    if connection is None or connection.get("enabled") is False:
        return 0
    try:
        result = int(
            profile.get("context_limit") or profile.get("ctx_limit") or 0
        )
    except (TypeError, ValueError):
        result = 0
    return result or _parse_ctx_str(str(profile.get("ctx") or ""))


def _configured_model_profiles() -> list[dict]:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        return []
    configuration = service.get_model_configuration()
    result: list[dict] = []
    for profile in configuration.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        candidate = service.candidate_for_profile(
            str(profile.get("id") or ""),
            configuration,
        )
        if candidate is not None:
            result.append(candidate)
    return result


def ctx_limit_for_model(model_name: str) -> int:
    """Context-window size (in tokens) for a specific model name. 0 if unknown.

    Resolves a configured ``ctx`` first, then falls back to a family heuristic.
    Used per-conversation so each chat's context gauge reflects its OWN model,
    not just the globally-active one.
    """
    model_name = str(model_name or "").strip()
    if not model_name:
        return 0
    for model in _configured_model_profiles():
        if model_name in {
            str(model.get("model") or ""),
            str(model.get("name") or ""),
            str(model.get("id") or ""),
            str(model.get("profile_id") or ""),
        }:
            limit = int(model.get("context_limit") or model.get("ctx_limit") or 0)
            if not limit:
                limit = _parse_ctx_str(model.get("ctx", ""))
            if limit:
                return limit
    # Profiles outside automatic routes remain addressable by explicit profile
    # bindings and chat selectors.
    profile_limit = _profile_ctx_limit(model_name)
    if profile_limit > 0:
        return profile_limit
    return _known_ctx_limit_for_model(model_name)


def _known_ctx_limit_for_model(model_name: str) -> int:
    """Return only the built-in family window, without reading user config."""
    ml = model_name.lower()
    if "claude" in ml or any(x in ml for x in ("opus-4", "sonnet-4", "haiku-4")):
        return 200_000
    if "gpt-4" in ml:
        return 128_000
    if "gpt-3.5" in ml:
        return 16_000
    if "deepseek" in ml:
        # V4 family (deepseek-v4-flash / deepseek-v4-pro) ships a 1M-token
        # window; older deepseek-chat (V3) / deepseek-reasoner (R1) cap at 128K.
        # Over-reporting would push compaction past the model's real limit and
        # the API would hard-reject oversized requests, so only widen for V4.
        return 1_000_000 if "v4" in ml else 128_000
    if "qwen" in ml:
        return 128_000
    if "gemini" in ml:
        return 1_000_000
    if "mimo-v2.5" in ml:
        return 1_000_000
    return 0


def configured_ctx_limit_for_model(
    model_name: str,
    models: list[dict] | None = None,
) -> int:
    """Return only the explicitly configured window for one model."""
    target = str(model_name or "").strip()
    if not target:
        return 0
    configured = _configured_model_profiles() if models is None else models
    for item in configured or []:
        if not isinstance(item, dict):
            continue
        if target in {
            str(item.get("model") or "").strip(),
            str(item.get("name") or "").strip(),
            str(item.get("id") or "").strip(),
            str(item.get("profile_id") or "").strip(),
        }:
            limit = int(item.get("context_limit") or item.get("ctx_limit") or 0)
            return limit or _parse_ctx_str(item.get("ctx", ""))
    return 0


def effective_ctx_limit_for_model(
    model_name: str,
    models: list[dict] | None = None,
) -> int:
    """Resolve one model's context window with a known-model fallback.

    An explicit ``ctx`` always wins. Otherwise a built-in family window for the
    same model is used. Only an entirely unknown model falls back to the smallest
    known window among configured candidates, so known models are never reduced
    by an unrelated backup model.
    """
    configured = _configured_model_profiles() if models is None else models
    explicit = configured_ctx_limit_for_model(model_name, configured)
    if explicit > 0:
        return explicit
    if models is None:
        profile_limit = _profile_ctx_limit(model_name)
        if profile_limit > 0:
            return profile_limit
    # Do not call ctx_limit_for_model() here: it reads the process-global model
    # settings and can override the explicit ``models`` snapshot supplied by
    # the caller (for example while evaluating a pending settings change).
    known_for_model = _known_ctx_limit_for_model(model_name)
    if known_for_model > 0:
        return known_for_model
    limits: list[int] = []
    for item in configured or []:
        if not isinstance(item, dict):
            continue
        candidate_name = str(
            item.get("model") or item.get("name") or item.get("id") or ""
        ).strip()
        if not candidate_name:
            continue
        limit = configured_ctx_limit_for_model(candidate_name, configured)
        if limit <= 0:
            limit = _known_ctx_limit_for_model(candidate_name)
        if limit > 0:
            limits.append(limit)
    if limits:
        return min(limits)
    return 0


def get_current_ctx_limit() -> int:
    """Context window for the first profile in the primary route."""
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        return 0
    models = service.candidates_for_route("primary")
    if not models:
        return 0
    return effective_ctx_limit_for_model(
        str(models[0].get("profile_id") or models[0].get("model") or ""),
        models,
    )


def get_enabled_plugins() -> dict[str, bool]:
    return dict(get_setting("enabled_plugins", _DEFAULT_ENABLED_PLUGINS))


def save_enabled_plugins(plugins: dict[str, bool]) -> None:
    set_setting("enabled_plugins", dict(plugins))


def is_plugin_enabled(name: str) -> bool:
    return get_enabled_plugins().get(name, True)


def get_enabled_plugin_packs() -> dict[str, bool]:
    return dict(get_setting("enabled_plugin_packs", {}))


def save_enabled_plugin_packs(packs: dict[str, bool]) -> None:
    clean = {
        str(name): bool(enabled)
        for name, enabled in packs.items()
        if str(name).strip()
    }
    set_setting("enabled_plugin_packs", clean)


def is_plugin_pack_enabled(pack_id: str) -> bool:
    return get_enabled_plugin_packs().get(str(pack_id or ""), True)


def get_write_permission_mode() -> str:
    value = str(get_setting("write_permission_mode", "workspace_only") or "workspace_only").strip().lower()
    return value if value in {"workspace_only", "full_access"} else "workspace_only"


def set_write_permission_mode(mode: str) -> None:
    normalized = str(mode or "workspace_only").strip().lower()
    if normalized not in {"workspace_only", "full_access"}:
        normalized = "workspace_only"
    set_setting("write_permission_mode", normalized)
