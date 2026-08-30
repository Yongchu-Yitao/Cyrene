"""Cross-platform application path resolution and temporary cleanup."""

from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cyrene.path_policy import (
    APP_NAME as APP_NAME,
    bundle_contents_dir,
    is_bundled,
    user_cache_dir,
    user_data_dir,
)

TEMP_ARTIFACT_TTL_SECONDS = 24 * 60 * 60
CYRENE_DIR_NAME = ".cyrene"


@dataclass(frozen=True)
class AppPaths:
    install_resources: Path
    user_data: Path
    runtime_base: Path
    workspace: Path
    store: Path
    data: Path
    cache: Path
    temp: Path


def _strip_path_value(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _env_path(env: Mapping[str, str], key: str) -> Path | None:
    raw = _strip_path_value(env.get(key))
    return Path(raw).expanduser() if raw else None


def source_root() -> Path:
    """Return the checkout root regardless of this module's package depth."""
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    # Packaged layouts may not ship pyproject.toml. Keep a deterministic
    # source-layout fallback for ``src/cyrene/platform/paths.py``.
    return module_path.parents[3]


def install_resources_dir(
    *,
    env: Mapping[str, str] | None = None,
    executable: str | Path | None = None,
    bundled: bool | None = None,
) -> Path:
    env = os.environ if env is None else env
    override = _env_path(env, "CYRENE_INSTALL_RESOURCES_DIR")
    if override is not None:
        return override
    if (is_bundled(executable) if bundled is None else bundled) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    contents = bundle_contents_dir(executable)
    if contents is not None:
        for candidate in (contents / "Resources", contents / "Frameworks"):
            if (candidate / "pyproject.toml").exists() or (candidate / "agent" / "plugin" / "plugin_impl").is_dir():
                return candidate
    return source_root()


def app_temp_dir(
    *,
    platform: str | None = None,
    home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    bundled: bool | None = None,
) -> Path:
    env = os.environ if env is None else env
    override = _env_path(env, "CYRENE_TEMP_DIR")
    if override is not None:
        return override
    return user_cache_dir(
        platform=platform,
        home=home,
        env=env,
        bundled=bundled,
    ) / "tmp"


def resolve_app_paths(
    *,
    platform: str | None = None,
    home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    bundled: bool | None = None,
    install_resources: str | Path | None = None,
) -> AppPaths:
    env = os.environ if env is None else env
    bundled_value = is_bundled() if bundled is None else bundled
    resources = Path(install_resources) if install_resources is not None else install_resources_dir(env=env, bundled=bundled_value)
    data_root = user_data_dir(
        platform=platform,
        home=home,
        env=env,
        bundled=bundled_value,
    )
    cache_root = user_cache_dir(
        platform=platform,
        home=home,
        env=env,
        bundled=bundled_value,
    )
    temp_root = app_temp_dir(
        platform=platform,
        home=home,
        env=env,
        bundled=bundled_value,
    )
    runtime_base = _env_path(env, "CYRENE_BASE_DIR") or data_root
    return AppPaths(
        install_resources=resources,
        user_data=data_root,
        runtime_base=runtime_base,
        workspace=runtime_base / "workspace",
        store=runtime_base / "store",
        data=runtime_base / "data",
        cache=cache_root,
        temp=temp_root,
    )


def cyrene_dir(workspace_root: str | Path) -> Path:
    """Return the hidden Cyrene-managed directory inside a workspace root.

    All Cyrene-owned subdirectories (conversations, plan, patterns, projects,
    scratch, SOUL.md) live under this folder so user files stay visible at the
    workspace root. Applies uniformly to the global workspace and every
    Workbench project workspace.
    """
    return Path(workspace_root) / CYRENE_DIR_NAME


def ensure_runtime_dirs(paths: AppPaths | None = None) -> None:
    paths = paths or PATHS
    for directory in (paths.workspace, paths.store, paths.data, paths.cache, paths.temp):
        directory.mkdir(parents=True, exist_ok=True)


def cleanup_temporary_artifacts(
    temp_dir: str | Path | None = None,
    *,
    ttl_seconds: int = TEMP_ARTIFACT_TTL_SECONDS,
    now: float | None = None,
) -> list[Path]:
    """Remove immediate children of the app temp directory older than ``ttl``."""
    root = Path(temp_dir) if temp_dir is not None else TEMP_DIR
    root.mkdir(parents=True, exist_ok=True)
    cutoff = (time.time() if now is None else now) - max(0, int(ttl_seconds))
    removed: list[Path] = []
    for child in root.iterdir():
        try:
            if child.stat().st_mtime > cutoff:
                continue
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                continue
            removed.append(child)
        except OSError:
            continue
    return removed


PATHS = resolve_app_paths()
INSTALL_RESOURCES_DIR = PATHS.install_resources
USER_DATA_DIR = PATHS.user_data
BASE_DIR = PATHS.runtime_base
WORKSPACE_DIR = PATHS.workspace
STORE_DIR = PATHS.store
DATA_DIR = PATHS.data
CACHE_DIR = PATHS.cache
TEMP_DIR = PATHS.temp
