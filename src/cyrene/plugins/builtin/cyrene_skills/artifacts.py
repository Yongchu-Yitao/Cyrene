"""File references carried separately from human-readable tool summaries.

Only explicit path fields in structured tool data are file references. Shell
commands, stdout, Markdown and other text are never a source of file authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_PATH_FIELDS = frozenset({"path", "file_path", "image_path", "screenshot_path", "output_path"})


def local_file_path(value: Any) -> str | None:
    """Validate an absolute path on this host without touching the filesystem."""
    if not isinstance(value, str) or not value or value.startswith("//"):
        return None
    if any(ord(char) < 32 for char in value):
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    # The file-reference contract excludes invalid filesystem components before
    # any consumer performs stat/copy/open, including explicit but malformed data.
    if any(len(part.encode("utf-8", errors="surrogatepass")) > 255 for part in path.parts):
        return None
    return value


def structured_paths(value: Any) -> list[str]:
    """Read explicit fields; accept a complete JSON tool result at the boundary."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    paths: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in _PATH_FIELDS:
                    path = local_file_path(child)
                    if path is not None and path not in paths:
                        paths.append(path)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return paths


def step_artifact_paths(step: dict[str, Any]) -> list[str]:
    if "artifacts" in step:
        return structured_paths(step["artifacts"])
    # Older records lack the manifest. Only recover explicit fields from intact
    # structured data; truncated summaries and arbitrary prose cannot be migrated.
    return list(dict.fromkeys(
        path
        for value in (step.get("args"), step.get("input_summary"), step.get("output_summary"))
        for path in structured_paths(value)
    ))
