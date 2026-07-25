"""Security boundary for user-selected Workbench workspace directories."""

from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path

from cyrene.config import WORKSPACE_DIR

_ALLOWED_ROOTS_ENV = "CYRENE_ALLOWED_WORKSPACE_ROOTS"


class WorkspacePathError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def allowed_workspace_roots() -> tuple[Path, ...]:
    candidates = [
        WORKSPACE_DIR,
        Path.home(),
        Path(tempfile.gettempdir()),
    ]
    system = platform.system()
    if system == "Darwin":
        candidates.append(Path("/Volumes"))
    elif system == "Linux":
        candidates.extend((Path("/mnt"), Path("/media")))

    configured = os.environ.get(_ALLOWED_ROOTS_ENV, "")
    if configured:
        candidates.extend(
            Path(item.strip())
            for item in configured.split(os.pathsep)
            if item.strip()
        )

    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def validate_workspace_path(path_value: str, *, create: bool = True) -> Path:
    raw = str(path_value or "").strip()
    if not raw or "\x00" in raw:
        raise WorkspacePathError("workspacePath is required", "workspace_path_required")

    try:
        target = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError(
            "workspacePath is invalid",
            "invalid_workspace_path",
        ) from exc

    if not any(target == root or target.is_relative_to(root) for root in allowed_workspace_roots()):
        raise WorkspacePathError(
            "workspacePath is outside the allowed roots",
            "workspace_path_not_allowed",
        )

    try:
        if target.exists() and not target.is_dir():
            raise WorkspacePathError(
                "workspacePath must be a directory",
                "workspace_path_not_directory",
            )
        if create:
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise WorkspacePathError(
                "workspacePath does not exist",
                "workspace_path_not_found",
            )

        probe_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".cyrene-write-test-",
                dir=target,
                delete=False,
            ) as probe:
                probe_path = Path(probe.name)
                probe.write(b"ok")
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
    except WorkspacePathError:
        raise
    except OSError as exc:
        raise WorkspacePathError(
            "workspacePath is not writable",
            "workspace_path_not_writable",
        ) from exc

    return target
