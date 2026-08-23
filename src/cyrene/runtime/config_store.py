"""Encrypted unified config store — replaces .env + web_settings.json.

All sensitive and user-editable configuration lives in a single
Fernet-encrypted JSON blob under DATA_DIR / "config.enc".

On first access the store migrates data from the legacy files
(.env and web_settings.json), then writes the encrypted store.
The legacy files are renamed to .bak for safety.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from copy import deepcopy
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cyrene.runtime import paths as app_paths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution (self-contained — do NOT import from cyrene.config)
# ---------------------------------------------------------------------------


_PATHS = app_paths.resolve_app_paths()
_SOURCE_ROOT = _PATHS.install_resources
_BASE_DIR = _PATHS.runtime_base

DATA_DIR = _BASE_DIR / "data"
_ENCRYPTED_PATH = DATA_DIR / "config.enc"
_KEY_PATH = DATA_DIR / ".config_key"
_LEGACY_ENV_PATH = _BASE_DIR / ".env"
_LEGACY_SETTINGS_PATH = DATA_DIR / "web_settings.json"

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

_DEFAULT_ENV: dict[str, str] = {
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
    "OPENAI_MODEL": "deepseek-v4-flash",
    "TELEGRAM_BOT_TOKEN": "",
    "WECHAT_BOT_TOKEN": "",
    "WECHAT_OWNER_ID": "",
    "AMAP_API_KEY": "",
    "EMBEDDING_BASE_URL": "",
    "EMBEDDING_API_KEY": "",
    "EMBEDDING_MODEL": "",
    "ASSISTANT_NAME": "Cyrene",
    "MAX_HISTORY_MESSAGES": "40",
    "MAX_TOOL_OUTPUT_CHARS": "0",
    "HEARTBEAT_INTERVAL": "300",
    "HEARTBEAT_LOTTERY_INTERVAL": "1800",
    "SCHEDULER_INTERVAL": "60",
    "DAYTIME_START": "6",
    "DAYTIME_END": "22",
    "LOTTERY_DELTA": "0.15",
    "LOTTERY_MAX": "0.85",
    "SEARCH_PROXY": "",
    "TAVILY_API_KEY": "",
    "BRAVE_SEARCH_API_KEY": "",
    "SEARXNG_URL": "",
    "SEARXNG_AUTO_START": "1",
    "SEARXNG_PORT": "8888",
    "SEARXNG_HOST": "127.0.0.1",
    "STEWARD_INTERVAL": "3600",
    "PATTERN_DETECTION_INTERVAL": "600",
    "WEB_PORT": "4242",
}

_REMOVED_ENV_KEYS = frozenset({"MAX_TOOL_ROUNDS"})

# Older installs persisted the former 12k global tool-output default into the
# encrypted store.  Changing _DEFAULT_ENV to zero did not affect those stores,
# so large structured results such as Cyrene UI snapshots were still cut off.
# This key was never user-editable in Settings; treat the old default as legacy
# state while preserving any other explicitly supplied value.
_LEGACY_MAX_TOOL_OUTPUT_CHARS = "12000"

_DEFAULT_ENABLED_TOOLS: dict[str, bool] = {
    "Read": True, "Write": True, "Edit": True, "Glob": True, "Grep": True,
    "Bash": True, "StartShell": True, "SendShell": True, "ListShells": True,
    "ReadShell": True, "InterruptShell": True, "ShowShell": True, "DeleteShell": True,
    "WebFetch": True, "WebSearch": True,
    "spawn_subagent": True, "send_agent_message": True,
    "schedule_task": True, "list_tasks": True, "edit_task": True, "pause_task": True,
    "resume_task": True, "cancel_task": True,
    "send_message": True, "send_file": True, "send_wechat_file": True,
    "ask_user": True,
    "send_telegram": False, "query_round": True,
    "app_use": True,
}

_DEFAULT_SETTINGS: dict = {
    "search_mode": "builtin",
    "search_external_url": "",
    "search": {
        "provider_order": [],
        "provider_enabled": {
            "simplexng": True,
            "deepseek": True,
            "tavily": False,
            "brave": False,
        },
    },
    "spawn_policy": "conservative",
    "heartbeat_interval": 1800,
    "background_skill_learning": True,
    "write_permission_mode": "workspace_only",
    # Adapter definitions live in code; user-owned connections, model profiles,
    # and their independent role routes live in this encrypted document.
    "model_configuration": {
        "version": 1,
        "connections": [],
        "profiles": [],
        "routes": {"primary": [], "secondary": [], "vision": [], "embedding": []},
    },
    "enabled_tools": _DEFAULT_ENABLED_TOOLS,
    "enabled_tool_packs": {},
    "workspace_history": [],
    "workspace_active": True,
    "soul_active": True,
    "agent_proactive": True,
    "app_language": "",
    "timezone": "Asia/Shanghai",
    # Renderer-wide low-overhead visual profile. The frontend mirrors this
    # value into localStorage so it can apply before the first React paint.
    "performance_mode": False,
    # Explicit opt-in proxy for Cyrene-launched external Agent processes.
    # Disabled means proxy variables are not inherited from the parent app.
    "external_agent_proxy_enabled": False,
    "external_agent_proxy_port": 7897,
    # Custom/API models use a currency budget. Keep the persisted defaults in
    # sync with the values shown by BudgetPanel so enabling the switch without
    # editing the amount still creates a real, visible budget.
    "budget_enabled": False,
    "budget_monthly": 50.0,
    "budget_currency": "CNY",
    "budget_action": "warn",
    "budget_mode": "normal",
    "budget_start_day": 1,
    # Codex OAuth has a separate account quota and enforcement switch.
    "codex_budget_enabled": True,
    # Execution workers are completion-driven. These are wide lease/safety
    # controls, not the main agent's normal tool-round budget.
    "subagent_execution_max_tool_calls": 200,
    "subagent_execution_max_wall_seconds": 1800,
    "subagent_execution_no_progress_turns": 3,
    "subagent_execution_checkpoint_calls": 20,
    "subagent_execution_max_cost_usd": 5.0,
    # 0 means use the active model's configured context window.
    "subagent_execution_max_context_tokens": 0,
    # Discussion agents use conversational limits rather than execution turns.
    "subagent_discussion_max_rounds": 5,
    "subagent_discussion_max_messages_per_agent": 4,
    "subagent_discussion_max_total_messages": 20,
    "subagent_discussion_max_message_chars": 2000,
    "subagent_discussion_max_wall_seconds": 600,
    "subagent_discussion_max_tool_calls": 50,
    "subagent_discussion_no_new_info_rounds": 2,
    "redact_secrets": True,
    "notify_telegram": True,
    "notify_wechat": True,
    "shortcut_bindings": {},
    # Portable Extension Center declarations. Downloaded runtimes and caches
    # deliberately live outside this encrypted snapshot.
    "extension_sources": {},
    "extension_clis": [],
    "extension_toolchains": [],
    "extension_system_bindings": {},
    # None distinguishes a pre-migration installation from a deliberately
    # empty encrypted MCP declaration list.
    "mcp_servers": None,
    "zotero": {
        "base_url": "http://127.0.0.1:23119/api",
        "auto_sync": False,
        "copy_attachments": True,
    },
    "embedding": {
        "provider": "openai_compatible",
        "base_url": "",
        "api_key": "",
        "model": "",
        "dimensions": 0,
    },
}

_EDITABLE_ENV_KEYS = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "TELEGRAM_BOT_TOKEN", "WECHAT_BOT_TOKEN", "AMAP_API_KEY",
    "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL",
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
    global _fernet, _migrated

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
    _migrated = True
    return config


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict | None = None
_migrated: bool = False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _parse_legacy_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"").strip()
        if key in _DEFAULT_ENV:
            result[key] = val
    return result


def _parse_legacy_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Corrupted web_settings.json, skipping migration for settings")
        return {}


def _migrate_if_needed() -> dict:
    global _migrated
    if _migrated:
        return _cache or {
            "env": dict(_DEFAULT_ENV),
            "settings": dict(_DEFAULT_SETTINGS),
            "settings_revision": 0,
        }

    env_from_legacy: dict[str, str] = {}
    settings_from_legacy: dict = {}

    for env_path in (_LEGACY_ENV_PATH, _LEGACY_ENV_PATH.with_suffix(".env.bak")):
        if env_path.exists():
            env_from_legacy = _parse_legacy_env(env_path)
            break
    for settings_path in (_LEGACY_SETTINGS_PATH, _LEGACY_SETTINGS_PATH.with_suffix(".json.bak")):
        if settings_path.exists():
            settings_from_legacy = _parse_legacy_settings(settings_path)
            break

    merged_env = dict(_DEFAULT_ENV)
    merged_env.update(env_from_legacy)
    merged_settings = dict(_DEFAULT_SETTINGS)
    if settings_from_legacy:
        for key, val in settings_from_legacy.items():
            if key in merged_settings and isinstance(merged_settings[key], dict) and isinstance(val, dict):
                merged_settings[key] = {**merged_settings[key], **val}
            else:
                merged_settings[key] = val

    config = {"env": merged_env, "settings": merged_settings, "settings_revision": 0}
    _generate_key_if_missing()
    _persist(config)

    for legacy_path in (_LEGACY_ENV_PATH, _LEGACY_SETTINGS_PATH):
        if legacy_path.exists():
            try:
                legacy_path.rename(legacy_path.with_suffix(legacy_path.suffix + ".bak"))
            except OSError:
                logger.warning("Failed to archive legacy plaintext config %s", legacy_path, exc_info=True)

    _migrated = True
    logger.info("Migrated legacy config to encrypted store at %s", _ENCRYPTED_PATH)
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
        return _migrate_if_needed()
    if not _KEY_PATH.exists():
        return _recover_config_without_key()
    try:
        encrypted = _ENCRYPTED_PATH.read_bytes()
        plain = _cipher().decrypt(encrypted)
        config = json.loads(plain.decode("utf-8"))
        return _apply_settings_migrations(config)
    except InvalidToken as exc:
        raise RuntimeError(
            f"Cannot decrypt config with local key {_KEY_PATH}; "
            f"existing config was preserved at {_ENCRYPTED_PATH}"
        ) from exc


_SETTINGS_MIGRATIONS_DONE: bool = False


def _apply_settings_migrations(config: dict) -> dict:
    """One-time migrations for renamed/deprecated settings keys."""
    global _SETTINGS_MIGRATIONS_DONE
    if _SETTINGS_MIGRATIONS_DONE:
        return config

    env = config.setdefault("env", {})
    settings = config.setdefault("settings", {})
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

    if str(env.get("MAX_TOOL_OUTPUT_CHARS", "")).strip() == _LEGACY_MAX_TOOL_OUTPUT_CHARS:
        env["MAX_TOOL_OUTPUT_CHARS"] = "0"
        os.environ.pop("MAX_TOOL_OUTPUT_CHARS", None)
        changed = True

    # v1 → v2: wechat_notify_scheduled merged into notify_wechat
    if "wechat_notify_scheduled" in settings and "notify_wechat" not in settings:
        settings["notify_wechat"] = settings.pop("wechat_notify_scheduled")
        changed = True

    # Fix model entries created by older onboarding that lacked model/base_url/api_key.
    env_base_url = config.get("env", {}).get("OPENAI_BASE_URL", _DEFAULT_ENV.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    env_api_key = config.get("env", {}).get("OPENAI_API_KEY", _DEFAULT_ENV.get("OPENAI_API_KEY", ""))
    for model_key in ("models", "vision_models"):
        items = settings.get(model_key)
        if not isinstance(items, list):
            continue
        fixed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model") or item.get("name") or item.get("id") or "").strip()
            if not model_id:
                continue
            if not item.get("model"):
                item["model"] = model_id
                changed = True
            if not item.get("name"):
                item["name"] = model_id
                changed = True
            if not item.get("base_url"):
                item["base_url"] = env_base_url
                changed = True
            if "api_key" not in item:
                # Only backfill the active env key when the model uses the same endpoint.
                item["api_key"] = env_api_key if str(item.get("base_url") or "").rstrip("/") == env_base_url.rstrip("/") else ""
                changed = True
            fixed.append(item)
        if len(fixed) != len(items):
            settings[model_key] = fixed
            changed = True

    if changed:
        _persist(config)
        logger.info("Applied settings migration")

    _SETTINGS_MIGRATIONS_DONE = True
    return config


def _ensure_loaded() -> dict:
    global _cache
    with _PERSIST_LOCK:
        if _cache is None:
            _cache = _read_config()
        return _cache


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
        sources = settings.get("extension_sources")
        if isinstance(sources, dict) and sources.get("github_token"):
            sources["github_token"] = ""
            sources["github_token_requires_reentry"] = True
        servers = settings.get("mcp_servers")
        if isinstance(servers, list):
            for server in servers:
                if not isinstance(server, dict):
                    continue
                for field in ("env", "headers"):
                    values = server.get(field)
                    if isinstance(values, dict) and values:
                        server[field] = {str(key): "[requires re-entry]" for key in values}
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
    return {
        "env": normalized_env,
        "settings": deepcopy(settings),
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
    global _cache, _migrated
    with _PERSIST_LOCK:
        previous_env = set((_cache or {}).get("env", {}))
        _cache = normalized
        _migrated = True
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


def get_editable_env_meta() -> list[dict]:
    config = _ensure_loaded()
    env = config.get("env", {})
    meta = [
        {"key": "OPENAI_API_KEY", "label": "LLM API Key", "masked": True},
        {"key": "OPENAI_BASE_URL", "label": "LLM Endpoint", "masked": False},
        {"key": "OPENAI_MODEL", "label": "Model Name", "masked": False},
        {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram Token", "masked": True},
        {"key": "WECHAT_BOT_TOKEN", "label": "WeChat Token", "masked": True},
        {"key": "AMAP_API_KEY", "label": "高德地图 Key", "masked": True},
        {"key": "EMBEDDING_BASE_URL", "label": "Embedding Endpoint", "masked": False},
        {"key": "EMBEDDING_API_KEY", "label": "Embedding API Key", "masked": True},
        {"key": "EMBEDDING_MODEL", "label": "Embedding Model", "masked": False},
    ]
    result = []
    for m in meta:
        value = env.get(m["key"], _DEFAULT_ENV.get(m["key"], ""))
        entry = {"key": m["key"], "label": m["label"], "masked": m["masked"], "value": value}
        if m["masked"] and value:
            entry["value"] = _mask_value(value)
        result.append(entry)
    return result


def _mask_value(value: str, show: int = 4) -> str:
    if len(value) <= show:
        return "•" * min(len(value), 4)
    return "•" * min(len(value) - show, 24) + value[-show:]


# ---------------------------------------------------------------------------
# Public API — Settings
# ---------------------------------------------------------------------------


def get_setting(key: str, default=None):
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


def update_settings_and_env_atomic(
    settings_updates: dict[str, object],
    env_updates: dict[str, str],
    *,
    expected_revision: int | None = None,
    remove_setting_keys: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object]]:
    """Atomically replace settings and their legacy environment mirrors.

    Model configuration needs both namespaces to cross the persistence boundary
    together: older runtime code still reads ``OPENAI_*`` and ``EMBEDDING_*``
    while the normalized source of truth is stored under settings.  Validation
    belongs to the caller; this helper performs one encrypted-file replacement
    and only then updates the live process environment.
    """

    if not isinstance(settings_updates, dict) or not settings_updates:
        raise ValueError("settings patch must be a non-empty object")
    if not isinstance(env_updates, dict):
        raise ValueError("environment patch must be an object")
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


def get_models() -> list[dict]:
    """Return the primary route as a compatibility view, never stored state."""
    model_configuration = importlib.import_module(
        "cyrene.runtime.model_configuration"
    )
    return model_configuration.candidates_for_route("primary")


def save_models(models: list[dict]) -> None:
    """Accept a legacy flat write and immediately normalize it into the graph."""
    model_configuration = importlib.import_module(
        "cyrene.runtime.model_configuration"
    )
    model_configuration.save_primary_model_candidates(list(models))


def get_custom_models() -> list[dict]:
    """Return non-Codex chat profiles derived from the model graph."""
    return [
        model
        for model in importlib.import_module(
            "cyrene.runtime.model_configuration"
        ).selectable_model_candidates()
        if str(model.get("provider") or "") != "codex_oauth"
        and "chat" in (model.get("capabilities") or [])
    ]


def save_custom_models(models: list[dict]) -> None:
    set_setting("custom_models", list(models))


def get_codex_model() -> dict:
    return next(
        (
            model
            for model in importlib.import_module(
                "cyrene.runtime.model_configuration"
            ).selectable_model_candidates()
            if str(model.get("provider") or "") == "codex_oauth"
        ),
        {},
    )


def save_codex_model(model: dict) -> None:
    set_setting("codex_model", dict(model))


def get_model_source() -> str:
    models = get_models() or []
    return (
        "codex"
        if models and str(models[0].get("provider") or "") == "codex_oauth"
        else "custom"
    )


def save_model_source(source: str) -> None:
    normalized = str(source or "").strip().lower()
    if normalized not in {"custom", "codex"}:
        raise ValueError("model source must be custom or codex")
    set_setting("model_source", normalized)


def get_vision_models() -> list[dict]:
    return importlib.import_module(
        "cyrene.runtime.model_configuration"
    ).candidates_for_route("vision")


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
    """Read one saved profile window without importing the model service layer."""
    configuration = get_setting("model_configuration", {})
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


def ctx_limit_for_model(model_name: str) -> int:
    """Context-window size (in tokens) for a specific model name. 0 if unknown.

    Resolves a configured ``ctx`` first, then falls back to a family heuristic.
    Used per-conversation so each chat's context gauge reflects its OWN model,
    not just the globally-active one.
    """
    model_name = str(model_name or "").strip()
    if not model_name:
        return 0
    for model in (get_models() or []):
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
    for model in (get_vision_models() or []):
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
    # Profiles outside the primary/vision routes remain addressable by Agent
    # bindings and future chat selectors without expanding the legacy lists.
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
    configured = get_models() if models is None else models
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
    configured = get_models() if models is None else models
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
    models = get_models() or []
    if not models:
        return 0
    return effective_ctx_limit_for_model(
        str(models[0].get("profile_id") or models[0].get("model") or ""),
        models,
    )


def save_vision_models(models: list[dict]) -> None:
    set_setting("vision_models", list(models))


def get_secondary_model() -> dict:
    candidates = importlib.import_module(
        "cyrene.runtime.model_configuration"
    ).candidates_for_route("secondary")
    return dict(candidates[0]) if candidates else {}


def save_secondary_model(model: dict) -> None:
    set_setting("secondary_model", {
        "model": str(model.get("model") or "").strip(),
        "name": str(model.get("name") or str(model.get("model") or "")).strip(),
        "api_key": str(model.get("api_key") or "").strip(),
        "base_url": str(model.get("base_url") or "").strip(),
        "ctx_limit": int(model.get("ctx_limit") or 0),
        "max_concurrency": int(model.get("max_concurrency") or 0),
    })


def get_enabled_tools() -> dict[str, bool]:
    return dict(get_setting("enabled_tools", _DEFAULT_ENABLED_TOOLS))


def save_enabled_tools(tools: dict[str, bool]) -> None:
    protected = {"quit"}
    clean = {k: v for k, v in tools.items() if k not in protected}
    set_setting("enabled_tools", clean)


def is_tool_enabled(name: str) -> bool:
    if name == "quit":
        return True
    return get_enabled_tools().get(name, True)


def get_enabled_tool_packs() -> dict[str, bool]:
    return dict(get_setting("enabled_tool_packs", {}))


def save_enabled_tool_packs(packs: dict[str, bool]) -> None:
    clean = {
        str(name): bool(enabled)
        for name, enabled in packs.items()
        if str(name).strip()
    }
    set_setting("enabled_tool_packs", clean)


def is_tool_pack_enabled(wire_name: str) -> bool:
    return get_enabled_tool_packs().get(str(wire_name or ""), True)


def get_spawn_policy() -> str:
    value = str(get_setting("spawn_policy", "conservative") or "conservative").strip().lower()
    return value if value in {"aggressive", "conservative", "off"} else "conservative"


def get_workspace_history() -> list[str]:
    return get_setting("workspace_history", [])


def add_workspace_to_history(path: str) -> None:
    history = [p for p in get_workspace_history() if p != path]
    history.insert(0, path)
    if len(history) > 10:
        history = history[:10]
    set_setting("workspace_history", history)


def activate_workspace(path: str = "") -> None:
    """Enable workspace context and update its history in one durable write."""
    config = _ensure_loaded()
    settings = config.setdefault("settings", {})
    settings["workspace_active"] = True
    normalized = str(path or "").strip()
    if normalized:
        raw_history = settings.get(
            "workspace_history",
            _DEFAULT_SETTINGS["workspace_history"],
        )
        if not isinstance(raw_history, list):
            raw_history = []
        history = [
            item
            for item in raw_history
            if item != normalized
        ]
        settings["workspace_history"] = [normalized, *history][:10]
    _persist(config)


def is_workspace_active() -> bool:
    return get_setting("workspace_active", True)


def set_workspace_active(active: bool) -> None:
    set_setting("workspace_active", active)


def get_write_permission_mode() -> str:
    value = str(get_setting("write_permission_mode", "workspace_only") or "workspace_only").strip().lower()
    return value if value in {"workspace_only", "full_access"} else "workspace_only"


def set_write_permission_mode(mode: str) -> None:
    normalized = str(mode or "workspace_only").strip().lower()
    if normalized not in {"workspace_only", "full_access"}:
        normalized = "workspace_only"
    set_setting("write_permission_mode", normalized)


def is_soul_active() -> bool:
    return get_setting("soul_active", True)


def set_soul_active(active: bool) -> None:
    set_setting("soul_active", active)


def get_heartbeat_interval() -> int:
    return int(get_setting("heartbeat_interval", 1800) or 1800)
