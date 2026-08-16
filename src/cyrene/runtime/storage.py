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


def _walk_size(root: Path, budget: list[int], name_filter: _NameFilter | None) -> tuple[int, int]:
    total = 0
    files = 0
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                if budget[0] <= 0:
                    return total, files
                budget[0] -= 1
                if name_filter is not None and not name_filter(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        sub_total, sub_files = _walk_size(Path(entry.path), budget, name_filter)
                        total += sub_total
                        files += sub_files
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                        files += 1
                except OSError:
                    continue
    except OSError:
        pass
    return total, files


def scan_storage() -> dict:
    """Walk every category and return byte totals without blocking the loop."""
    budget = [_MAX_SCAN_ENTRIES]
    categories = []
    total = 0
    for entry in STORAGE_CATEGORIES:
        key, paths = entry[0], entry[1]
        name_filter = entry[2] if len(entry) > 2 else None
        category_bytes = 0
        category_files = 0
        for path in paths:
            if path.is_dir():
                size, count = _walk_size(path, budget, name_filter)
                category_bytes += size
                category_files += count
        categories.append({"key": key, "bytes": category_bytes, "files": category_files})
        total += category_bytes
    return {
        "total": total,
        "categories": categories,
        "truncated": budget[0] <= 0,
    }
