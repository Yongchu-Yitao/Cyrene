"""Platform storage roots shared by source and packaged Cyrene runtimes."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

APP_NAME = "Cyrene"
DEVELOPMENT_APP_NAME = "Cyrene-dev"


def _strip_path_value(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _env_path(env: Mapping[str, str], key: str) -> Path | None:
    raw = _strip_path_value(env.get(key))
    return Path(raw).expanduser() if raw else None


def bundle_contents_dir(executable: str | Path | None = None) -> Path | None:
    exe = Path(executable or sys.executable).resolve()
    parts = exe.parts
    for idx, part in enumerate(parts):
        if part.endswith(".app") and idx + 2 < len(parts) and parts[idx + 1] == "Contents":
            return Path(*parts[: idx + 2])
    return None


def is_bundled(executable: str | Path | None = None) -> bool:
    return getattr(sys, "frozen", False) or bundle_contents_dir(executable) is not None


def storage_app_name(*, bundled: bool | None = None) -> str:
    bundled_value = is_bundled() if bundled is None else bundled
    return APP_NAME if bundled_value else DEVELOPMENT_APP_NAME


def user_data_dir(
    *,
    platform: str | None = None,
    home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    bundled: bool | None = None,
    executable: str | Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    override = _env_path(env, "CYRENE_USER_DATA_DIR")
    if override is not None:
        return override
    platform = platform or sys.platform
    home_path = Path(home) if home is not None else Path.home()
    bundled_value = is_bundled(executable) if bundled is None else bundled
    app_name = storage_app_name(bundled=bundled_value)
    if platform == "darwin":
        return home_path / "Library" / "Application Support" / app_name
    if platform == "win32":
        base = _env_path(env, "APPDATA") or home_path / "AppData" / "Roaming"
        return base / app_name
    base = _env_path(env, "XDG_DATA_HOME") or home_path / ".local" / "share"
    return base / app_name


def user_cache_dir(
    *,
    platform: str | None = None,
    home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    bundled: bool | None = None,
    executable: str | Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    override = _env_path(env, "CYRENE_CACHE_DIR")
    if override is not None:
        return override
    platform = platform or sys.platform
    home_path = Path(home) if home is not None else Path.home()
    bundled_value = is_bundled(executable) if bundled is None else bundled
    app_name = storage_app_name(bundled=bundled_value)
    if platform == "darwin":
        return home_path / "Library" / "Caches" / app_name
    if platform == "win32":
        base = (
            _env_path(env, "LOCALAPPDATA")
            or _env_path(env, "APPDATA")
            or home_path / "AppData" / "Local"
        )
        return base / app_name / "Cache"
    base = _env_path(env, "XDG_CACHE_HOME") or home_path / ".cache"
    return base / app_name


__all__ = [
    "APP_NAME",
    "DEVELOPMENT_APP_NAME",
    "bundle_contents_dir",
    "is_bundled",
    "storage_app_name",
    "user_cache_dir",
    "user_data_dir",
]
