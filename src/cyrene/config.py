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

# === Bot 配置 ===
TELEGRAM_BOT_TOKEN = _store.get_env("TELEGRAM_BOT_TOKEN") or None
OWNER_ID = int(os.environ["OWNER_ID"]) if os.environ.get("OWNER_ID") else None

# === WeChat 配置 ===
WECHAT_BOT_TOKEN = _store.get_env("WECHAT_BOT_TOKEN", "")
WECHAT_OWNER_ID = _store.get_env("WECHAT_OWNER_ID", "")

# === 高德地图 ===
AMAP_API_KEY = _store.get_env("AMAP_API_KEY", "")

# === Agent 配置 ===
ASSISTANT_NAME = _store.get_env("ASSISTANT_NAME", "Cyrene")
MAX_TOOL_OUTPUT_CHARS = int(_store.get_env("MAX_TOOL_OUTPUT_CHARS", "0"))

# === Scheduler 配置 ===
SCHEDULER_INTERVAL = int(_store.get_env("SCHEDULER_INTERVAL", "60"))

# === 搜索配置 ===
SEARCH_PROXY = _store.get_env("SEARCH_PROXY", "")
SEARXNG_URL = _store.get_env("SEARXNG_URL", "")
SEARXNG_AUTO_START = (os.environ.get("SEARXNG_AUTO_START") or _store.get_env("SEARXNG_AUTO_START", "1")) not in ("0", "false", "no")
SEARXNG_PORT = int(_store.get_env("SEARXNG_PORT", "8888"))
SEARXNG_HOST = _store.get_env("SEARXNG_HOST", "127.0.0.1")

# === Steward 配置 ===
# Steward runs are model-backed maintenance. Keep a one-hour floor.
STEWARD_INTERVAL = max(3600, int(_store.get_env("STEWARD_INTERVAL", "3600")))

PATTERN_DETECTION_INTERVAL = int(_store.get_env("PATTERN_DETECTION_INTERVAL", "600"))

# Web UI
WEB_PORT = int(os.environ.get("WEB_PORT") or _store.get_env("WEB_PORT", "4242"))


# 可在 Web UI 中编辑的 key 白名单
_EDITABLE_KEYS = {
    "TELEGRAM_BOT_TOKEN": {"label": "Telegram Token","masked": True},
    "WECHAT_BOT_TOKEN":  {"label": "WeChat Token",  "masked": True},
    "AMAP_API_KEY":      {"label": "高德地图 Key",  "masked": True},
}


def read_env_file() -> dict[str, str]:
    """Read all editable .env keys from the encrypted store."""
    all_env = _store.get_all_env()
    return {k: v for k, v in all_env.items() if k in _EDITABLE_KEYS}


def write_env_keys(updates: dict[str, str]) -> bool:
    """Write one or more env keys to the encrypted store.  Also update os.environ + module globals."""
    filtered = {}
    for key, value in updates.items():
        if key not in _EDITABLE_KEYS and key != "WECHAT_OWNER_ID":
            continue
        filtered[key] = _strip_wrapping_quotes(value)

    if not filtered:
        return True

    _store.set_env_many(filtered)
    _apply_env_updates(filtered)
    return True


def _apply_env_updates(updates: dict[str, str]) -> None:
    """Reflect env changes in this module's globals."""
    import sys as _sys
    _mod = _sys.modules[__name__]
    for key, value in updates.items():
        if key == "TELEGRAM_BOT_TOKEN":
            _mod.TELEGRAM_BOT_TOKEN = value
        elif key == "WECHAT_BOT_TOKEN":
            _mod.WECHAT_BOT_TOKEN = value
        elif key == "WECHAT_OWNER_ID":
            _mod.WECHAT_OWNER_ID = value
        elif key == "AMAP_API_KEY":
            _mod.AMAP_API_KEY = value


def get_env_keys_meta() -> list[dict]:
    """Return editable .env keys with metadata for the Web UI."""
    return _store.get_editable_env_meta()


def editable_env_keys() -> dict[str, dict[str, object]]:
    """Return a defensive copy of the editable environment-key policy."""
    return {key: dict(meta) for key, meta in _EDITABLE_KEYS.items()}


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
