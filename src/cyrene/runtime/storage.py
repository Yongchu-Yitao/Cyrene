"""Storage usage scan for the settings → Data panel.

The category paths intentionally parallel ``_MANAGED_DIRECTORIES``
(cyrene/runtime/backup.py) and ``_reset_app_data`` (cyrene/workbench/runtime.py).
Grouping differs (one storage chip may cover several backup roots), but when
directories are added or removed all three lists must be updated together.

A category tuple may carry a third element: a name filter. When present only
entries whose name matches the filter are counted (a matching directory is
walked recursively). This keeps store/ file families (knowledge bases, memory
snapshots) split into separate chips without adding a third path list.
"""

import fnmatch
import os
import stat
from pathlib import Path
from typing import Callable

from cyrene.config import BASE_DIR, CACHE_DIR, DATA_DIR, STORE_DIR, WORKSPACE_DIR

_MAX_SCAN_ENTRIES = 200_000

_NameFilter = Callable[[str], bool]


def _name_matches(*patterns: str) -> _NameFilter:
    return lambda name: any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _name_excludes(*patterns: str) -> _NameFilter:
    match = _name_matches(*patterns)
    return lambda name: not match(name)


# store/ families: the core database and its remote-control satellites vs the
# per-workspace knowledge bases vs the workbench memory snapshots.
_KB_FILES = _name_matches("kb_*.db*")
_MEMORY_FILES = _name_matches("wb_memory_*.json", "project_memory_*.json")
_DATABASE_FILES = _name_excludes("kb_*.db*", "wb_memory_*.json", "project_memory_*.json")
_STORE_FAMILIES: list[tuple[str, _NameFilter]] = [
    ("database", _DATABASE_FILES),
    ("knowledge", _KB_FILES),
    ("memory", _MEMORY_FILES),
]
_STORE_FAMILY_KEYS = {key for key, _ in _STORE_FAMILIES}

STORAGE_CATEGORIES: list[tuple[str, tuple[Path, ...], _NameFilter | None]] = [
    ("database", (STORE_DIR,), _DATABASE_FILES),
    ("knowledge", (STORE_DIR,), _KB_FILES),
    ("memory", (STORE_DIR,), _MEMORY_FILES),
    ("conversations", (WORKSPACE_DIR / "conversations",), None),
    ("plans", (WORKSPACE_DIR / "plan",), None),
    ("deliverables", (WORKSPACE_DIR / "deliverables",), None),
    ("projects", (WORKSPACE_DIR / "projects",), None),
    ("sessions", (DATA_DIR / "sessions",), None),
    ("inbox", (DATA_DIR / "inbox",), None),
    ("skills", (DATA_DIR / "installed_skills", DATA_DIR / "learned_skill_scripts"), None),
    ("attachments", (DATA_DIR / "webui_uploads", DATA_DIR / "webui_exports", DATA_DIR / "behavior-media"), None),
    ("backups", (BASE_DIR / "backups",), None),
    ("local_models", (CACHE_DIR / "knowledge_models",), None),
    ("codex_cli", (CACHE_DIR / "codex_cli",), None),
    ("opencv_runtime", (CACHE_DIR / "opencv_runtime",), None),
    ("browser", (DATA_DIR / "browser_profile",), None),
    # Mirrors backup.py _EXCLUDED_DATA_DIRECTORIES: the disposable data the
    # backup deliberately omits.
    ("caches", (DATA_DIR / "attachment_cache", DATA_DIR / "generated_reports", CACHE_DIR / "voice"), None),
]


def scan_storage() -> dict:
    """Walk every category and return byte totals without blocking the loop."""
    remaining = _MAX_SCAN_ENTRIES
    totals: dict[str, list[int]] = {key: [0, 0] for key, _, _ in STORAGE_CATEGORIES}

    def walk(root: Path, name_filter: _NameFilter | None) -> tuple[int, int]:
        nonlocal remaining
        total = 0
        files = 0
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if remaining <= 0:
                        return total, files
                    remaining -= 1
                    if name_filter is not None and not name_filter(entry.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(st.st_mode):
                            sub_total, sub_files = walk(Path(entry.path), name_filter)
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

    def walk_store(root: Path) -> None:
        # The three store/ families share one tree; walk it once and dispatch
        # each entry (and its whole subtree) to the matching bucket.
        nonlocal remaining
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if remaining <= 0:
                        return
                    remaining -= 1
                    for key, name_filter in _STORE_FAMILIES:
                        if not name_filter(entry.name):
                            continue
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(st.st_mode):
                                size, count = walk(Path(entry.path), name_filter)
                            elif stat.S_ISREG(st.st_mode):
                                size, count = st.st_size, 1
                            else:
                                break
                            totals[key][0] += size
                            totals[key][1] += count
                        except OSError:
                            pass
                        break
        except OSError:
            pass

    store_roots = {
        path
        for key, paths, _ in STORAGE_CATEGORIES
        if key in _STORE_FAMILY_KEYS
        for path in paths
    }
    for root in store_roots:
        walk_store(root)
    for key, paths, name_filter in STORAGE_CATEGORIES:
        if key in _STORE_FAMILY_KEYS:
            continue
        for path in paths:
            if path.is_dir():
                size, count = walk(path, name_filter)
                totals[key][0] += size
                totals[key][1] += count

    return {
        "total": sum(size for size, _ in totals.values()),
        "categories": [
            {"key": key, "bytes": totals[key][0], "files": totals[key][1]}
            for key, _, _ in STORAGE_CATEGORIES
        ],
        "truncated": remaining <= 0,
    }
