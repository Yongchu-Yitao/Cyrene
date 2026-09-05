"""Version helpers shared by runtime components and build metadata."""

from __future__ import annotations

import importlib.metadata
import re
import sys
from functools import lru_cache
from pathlib import Path


def _public_version(value: str) -> str:
    """Map normalized package metadata to Cyrene's public release label."""
    value = re.sub(
        r"^(\d+(?:\.\d+)*)(a|b|rc)(\d+)(?=$|[.+])",
        lambda match: (
            f"{match[1]}-" + {"a": "alpha", "b": "beta", "rc": "rc"}[match[2]] + match[3]
        ),
        value,
    )
    if value.endswith("+fix"):
        return value[:-4] + "-fix"
    return value


def _bundle_contents_dir() -> Path | None:
    exe = Path(sys.executable).resolve()
    parts = exe.parts
    for idx, part in enumerate(parts):
        if part.endswith(".app") and idx + 2 < len(parts) and parts[idx + 1] == "Contents":
            return Path(*parts[: idx + 2])
    return None


def _pyproject_candidates() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "pyproject.toml")
    bundle_contents = _bundle_contents_dir()
    if bundle_contents is not None:
        candidates.append(bundle_contents / "Resources" / "pyproject.toml")
        candidates.append(bundle_contents / "Frameworks" / "pyproject.toml")
    candidates.append(Path(__file__).resolve().parents[3] / "pyproject.toml")
    return candidates


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the application version, preferring pyproject.toml."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-relevant-import]

    for pyproject in _pyproject_candidates():
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                return _public_version(tomllib.load(f)["project"]["version"])

    try:
        return _public_version(importlib.metadata.version("cyrene"))
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def get_version_label() -> str:
    """Return the user-facing version label."""
    return f"v{get_version()}"
