"""Content identity shared by registry and plugin lifecycle owners."""
from __future__ import annotations

import hashlib
from pathlib import Path


def python_source_signature(entry: Path) -> str | None:
    files = (
        tuple(path for path in sorted(entry.rglob("*.py"))
              if "__pycache__" not in path.parts)
        if entry.is_dir() else (entry,) if entry.is_file() else ()
    )
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(entry) if entry.is_dir() else Path(path.name)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
