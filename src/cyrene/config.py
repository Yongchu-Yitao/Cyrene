import os
from pathlib import Path

from cyrene.runtime import paths as app_paths
from cyrene.runtime import config_store as _store


def _strip_wrapping_quotes(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def strip_wrapping_quotes(value: str | None) -> str:
    """Public normalization used by configuration consumers."""
    return _strip_wrapping_quotes(value)


SOURCE_ROOT = app_paths.INSTALL_RESOURCES_DIR
INSTALL_RESOURCES_DIR = app_paths.INSTALL_RESOURCES_DIR
USER_DATA_DIR = app_paths.USER_DATA_DIR
BASE_DIR = app_paths.BASE_DIR
cyrene_dir = app_paths.cyrene_dir

# 路径
WORKSPACE_DIR = BASE_DIR / "workspace"      # 工作区，存放 SOUL.md、CLAUDE.md 等运行时文件
STORE_DIR = BASE_DIR / "store"              # 持久化存储，数据库文件
DATA_DIR = BASE_DIR / "data"                # 运行时数据，状态文件、收件箱等
CACHE_DIR = app_paths.CACHE_DIR             # 平台特定缓存目录
TEMP_DIR = app_paths.TEMP_DIR               # 应用临时产物目录（启动时按 TTL 清理）
DB_PATH = STORE_DIR / "cyrene.runtime.database"           # SQLite 数据库路径
INBOX_DIR = DATA_DIR / "inbox"              # 收件箱目录，存放外部消息

# —— 从加密配置加载环境变量并注入 os.environ ——
_env = _store.get_all_env()
for _k, _v in _env.items():
    if _v:
        os.environ.setdefault(_k, _v)

# === Shared external identity ===
OWNER_ID = int(os.environ["OWNER_ID"]) if os.environ.get("OWNER_ID") else None

# === Agent 配置 ===
ASSISTANT_NAME = _store.get_env("ASSISTANT_NAME", "Cyrene")
MAX_TOOL_OUTPUT_CHARS = int(_store.get_env("MAX_TOOL_OUTPUT_CHARS", "0"))

# Web UI
WEB_PORT = int(os.environ.get("WEB_PORT") or _store.get_env("WEB_PORT", "4242"))


# Core does not own provider credentials. Active Plugins contribute the
# editable key policy below.
_EDITABLE_KEYS: dict[str, dict[str, object]] = {}


def _plugin_editable_env_keys() -> dict[str, dict[str, object]]:
    """Collect credential policies from active application Plugins."""

    try:
        from cyrene.core.plugin import application_plugin_scope

        host = application_plugin_scope()
        services = host.active_services.values() if host is not None else ()
    except Exception:
        return {}
    result: dict[str, dict[str, object]] = {}
    seen: set[int] = set()
    for service in services:
        if id(service) in seen:
            continue
        seen.add(id(service))
        provider = getattr(service, "editable_env_keys", None)
        if not callable(provider):
            continue
        contributed = provider()
        if not isinstance(contributed, dict):
            raise TypeError("Plugin editable_env_keys contribution must be a mapping")
        for key, meta in contributed.items():
            normalized = str(key or "").strip()
            if not normalized or not isinstance(meta, dict):
                raise TypeError("Plugin editable environment-key policy is invalid")
            if normalized in result or normalized in _EDITABLE_KEYS:
                raise ValueError(f"Duplicate editable environment key: {normalized}")
            result[normalized] = dict(meta)
    return result


def read_env_file() -> dict[str, str]:
    """Read all editable .env keys from the encrypted store."""
    all_env = _store.get_all_env()
    allowed = editable_env_keys()
    return {k: v for k, v in all_env.items() if k in allowed}


def write_env_keys(updates: dict[str, str]) -> bool:
    """Write one or more env keys to the encrypted store.  Also update os.environ + module globals."""
    filtered = {}
    for key, value in updates.items():
        if key not in editable_env_keys():
            continue
        filtered[key] = _strip_wrapping_quotes(value)

    if not filtered:
        return True

    _store.set_env_many(filtered)
    return True


def get_env_keys_meta() -> list[dict]:
    """Return editable .env keys with metadata for the Web UI."""
    result = []
    for key, meta in editable_env_keys().items():
        value = _store.get_env(key, "")
        if bool(meta.get("masked")) and value:
            value = mask_value(value)
        result.append({"key": key, **meta, "value": value})
    return result


def editable_env_keys() -> dict[str, dict[str, object]]:
    """Return a defensive copy of the editable environment-key policy."""
    core = {
        key: {**dict(meta), "label": str(meta.get("label") or key)}
        for key, meta in _EDITABLE_KEYS.items()
    }
    return {**core, **_plugin_editable_env_keys()}


def mask_value(value: str, show: int = 4) -> str:
    """Mask a secret value, showing only the last N chars."""
    if len(value) <= show:
        return "•" * min(len(value), 4)
    return "•" * min(len(value) - show, 24) + value[-show:]


def get_chat_workspace(chat_id: int) -> Path:
    """Get workspace directory for a specific chat.

    Currently all chats share the same workspace (single-user mode).
    Future: Each chat can have isolated workspace for multi-user/group support.

    Example future structure:
        workspace/
        └── chats/
            ├── 123456/       # user chat
            │   ├── CLAUDE.md
            │   └── conversations/
            └── -987654/      # group chat (negative ID)
                ├── CLAUDE.md
                └── conversations/
    """
    # Single-user mode: all chats use the same workspace
    return WORKSPACE_DIR

    # Future multi-user mode (uncomment when needed):
    # chat_dir = WORKSPACE_DIR / "chats" / str(chat_id)
    # chat_dir.mkdir(parents=True, exist_ok=True)
    # return chat_dir
