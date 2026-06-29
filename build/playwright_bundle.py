"""Helpers for bundling Playwright browser binaries with PyInstaller."""

from __future__ import annotations

import os
from pathlib import Path


def has_required_chromium_bundles(browser_root: Path) -> bool:
    """Return whether both headed and default-headless Chromium are installed."""
    return (
        any(browser_root.glob("chromium-*"))
        and any(browser_root.glob("chromium_headless_shell-*"))
    )


def collect_browser_toc(browser_root: Path) -> list[tuple[str, str, str]]:
    """Build PyInstaller TOC entries while preserving browser symlinks."""
    root = browser_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Playwright browser root does not exist: {root}")

    entries: list[tuple[str, str, str]] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in os.scandir(directory):
            if entry.name == ".DS_Store":
                continue

            source = Path(entry.path)
            relative = source.relative_to(root)
            destination = str(Path("ms-playwright") / relative)

            if entry.is_symlink():
                target = os.readlink(entry.path)
                if os.path.isabs(target):
                    resolved_target = source.resolve()
                    try:
                        resolved_target.relative_to(root)
                    except ValueError as exc:
                        raise ValueError(
                            f"Playwright browser symlink points outside its bundle: "
                            f"{source} -> {target}"
                        ) from exc
                    target = os.path.relpath(resolved_target, source.parent)
                entries.append((destination, target, "SYMLINK"))
            elif entry.is_dir(follow_symlinks=False):
                stack.append(source)
            elif entry.is_file(follow_symlinks=False):
                entries.append((destination, str(source), "DATA"))

    return entries


def find_bundled_browser_dir(
    meipass: str | os.PathLike[str] | None,
    executable: str | os.PathLike[str],
) -> Path | None:
    """Find ``ms-playwright`` across supported PyInstaller layouts."""
    candidates: list[Path] = []
    if meipass:
        base = Path(meipass)
        candidates.extend(
            (
                base / "ms-playwright",
                base.parent / "ms-playwright",
                base.parent / "Resources" / "ms-playwright",
            )
        )

    executable_dir = Path(executable).resolve().parent
    candidates.extend(
        (
            executable_dir / "_internal" / "ms-playwright",
            executable_dir / "ms-playwright",
            executable_dir.parent / "Resources" / "ms-playwright",
        )
    )

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_dir():
            return candidate
    return None
