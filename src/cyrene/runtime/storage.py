"""Storage usage scan for the settings → Data panel.

The category paths intentionally parallel ``_MANAGED_DIRECTORIES``
(cyrene/runtime/backup.py) and ``_reset_app_data`` (cyrene/workbench/runtime.py).
Grouping differs (one storage chip may cover several backup roots), but when
directories are added or removed all three lists must be updated together.

Plugin-owned roots are supplied at runtime by the corresponding application
service. Core deliberately does not know their category names, locations, or
legacy filename conventions. A path claimed by a Plugin is excluded from any
enclosing core category and measured exactly once under the Plugin's category.

A category tuple may carry a third element: a name filter. When present only
entries whose name matches the filter are counted (a matching directory is
walked recursively).
"""

import fnmatch
import os
import stat
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Callable

from cyrene.config import BASE_DIR, DATA_DIR, STORE_DIR, WORKSPACE_DIR, cyrene_dir

_MAX_SCAN_ENTRIES = 200_000

_NameFilter = Callable[[str], bool]


def _name_matches(*patterns: str) -> _NameFilter:
    return lambda name: any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _name_excludes(*patterns: str) -> _NameFilter:
    match = _name_matches(*patterns)
    return lambda name: not match(name)


STORAGE_CATEGORIES: list[tuple[str, tuple[Path, ...], _NameFilter | None]] = [
    ("database", (STORE_DIR,), None),
    ("plans", (cyrene_dir(WORKSPACE_DIR) / "plan",), None),
    ("projects", (cyrene_dir(WORKSPACE_DIR) / "projects",), None),
    ("sessions", (DATA_DIR / "sessions",), None),
    ("inbox", (DATA_DIR / "inbox",), None),
    ("attachments", (DATA_DIR / "webui_uploads", DATA_DIR / "webui_exports"), None),
    ("backups", (BASE_DIR / "backups",), None),
    # Disposable core data that portable backups deliberately omit.
    ("caches", (DATA_DIR / "attachment_cache", DATA_DIR / "generated_reports"), None),
]


def _active_plugin_storage_paths() -> Mapping[str, Iterable[Path]]:
    try:
        from agent.plugin import active_plugin_application_host

        host = active_plugin_application_host()
        if host is None:
            return {}
        result: dict[str, list[Path]] = {}
        for service in host.active_services.values():
            provider = getattr(service, "storage_paths", None)
            value = provider() if callable(provider) else {}
            if not isinstance(value, Mapping):
                continue
            for raw_key, paths in value.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                result.setdefault(key, []).extend(Path(path) for path in paths)
        return result
    except Exception:
        return {}


def scan_storage(
    plugin_storage: Mapping[str, Iterable[Path]] | None = None,
) -> dict:
    """Walk every category and return byte totals without blocking the loop."""
    remaining = _MAX_SCAN_ENTRIES
    totals: dict[str, list[int]] = {key: [0, 0] for key, _, _ in STORAGE_CATEGORIES}
    dynamic_paths = (
        _active_plugin_storage_paths()
        if plugin_storage is None
        else plugin_storage
    )

    # Resolve declarations before scanning core roots. This is the generic
    # ownership boundary that lets a Plugin classify legacy files living
    # inside a core directory without teaching core their filenames.
    normalized_dynamic: dict[str, list[tuple[Path, Path]]] = {}
    claimed_paths: dict[Path, str] = {}
    for raw_key, paths in dynamic_paths.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        totals.setdefault(key, [0, 0])
        seen: set[Path] = set()
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            try:
                identity = path.resolve(strict=False)
            except OSError:
                identity = path.absolute()
            if identity in seen:
                continue
            seen.add(identity)
            # The first active contribution owns an exact path. Services are
            # traversed deterministically by the application host, and an
            # accidental collision must not double-count the same bytes.
            if identity in claimed_paths and claimed_paths[identity] != key:
                continue
            claimed_paths[identity] = key
            normalized_dynamic.setdefault(key, []).append((path, identity))

    def identity(path: Path) -> Path:
        try:
            return path.resolve(strict=False)
        except OSError:
            return path.absolute()

    def claimed_elsewhere(path: Path, category: str) -> bool:
        owner = claimed_paths.get(identity(path))
        return owner is not None and owner != category

    def walk(
        root: Path,
        name_filter: _NameFilter | None,
        category: str,
    ) -> tuple[int, int]:
        nonlocal remaining
        total = 0
        files = 0
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if remaining <= 0:
                        return total, files
                    remaining -= 1
                    entry_path = Path(entry.path)
                    if claimed_elsewhere(entry_path, category):
                        continue
                    if name_filter is not None and not name_filter(entry.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(st.st_mode):
                            sub_total, sub_files = walk(
                                entry_path,
                                name_filter,
                                category,
                            )
                            total += sub_total
                            files += sub_files
                        elif stat.S_ISREG(st.st_mode):
                            total += st.st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            pass
        return total, files

    def measure(
        path: Path,
        name_filter: _NameFilter | None,
        category: str,
    ) -> tuple[int, int]:
        nonlocal remaining
        if claimed_elsewhere(path, category):
            return 0, 0
        try:
            st = path.stat(follow_symlinks=False)
        except OSError:
            return 0, 0
        if stat.S_ISDIR(st.st_mode):
            return walk(path, name_filter, category)
        if stat.S_ISREG(st.st_mode):
            if remaining <= 0 or (name_filter is not None and not name_filter(path.name)):
                return 0, 0
            remaining -= 1
            return st.st_size, 1
        return 0, 0

    for key, paths, name_filter in STORAGE_CATEGORIES:
        for path in paths:
            size, count = measure(path, name_filter, key)
            totals[key][0] += size
            totals[key][1] += count

    for key, paths in normalized_dynamic.items():
        for path, _identity in paths:
            size, count = measure(path, None, key)
            totals[key][0] += size
            totals[key][1] += count

    return {
        "total": sum(size for size, _ in totals.values()),
        "categories": [
            {"key": key, "bytes": totals[key][0], "files": totals[key][1]}
            for key in totals
        ],
        "truncated": remaining <= 0,
    }
