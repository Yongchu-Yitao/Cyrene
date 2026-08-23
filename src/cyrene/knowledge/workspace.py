"""Project-scoped knowledge database resolution and initialization."""

from __future__ import annotations

import asyncio

from cyrene.workbench.context import resolve_workbench_project_id


_initialized_databases: set[str] = set()
_initialization_lock = asyncio.Lock()

# Public compatibility view used by migration/reset adapters.  The owning
# module remains responsible for mutating the cache.
initialized_databases = _initialized_databases


class WorkspaceResolutionError(ValueError):
    """Base error for invalid project-scoped knowledge workspace references."""


class WorkspaceRequiredError(WorkspaceResolutionError):
    """Raised when a caller omits the required project workspace reference."""


class WorkspaceNotFoundError(WorkspaceResolutionError):
    """Raised when a workspace reference does not identify a current project."""


def resolve_workspace_id(workspace_id: str | None) -> str:
    """Resolve a Workbench project id or legacy data key to its project id."""
    project_ref = str(workspace_id or "").strip()
    if not project_ref:
        raise WorkspaceRequiredError("workspace is required")
    project_id = resolve_workbench_project_id(project_ref)
    if project_id is None:
        raise WorkspaceNotFoundError(f"workspace not found: {project_ref}")
    return project_id


async def ensure_workspace_db(workspace_id: str | None) -> str:
    """Return the initialized knowledge database for a Workbench project."""
    from cyrene.config import get_knowledge_db_path
    from cyrene.runtime.database import init_knowledge_db

    db_path = str(get_knowledge_db_path(resolve_workspace_id(workspace_id)))
    if db_path not in _initialized_databases:
        async with _initialization_lock:
            if db_path not in _initialized_databases:
                await init_knowledge_db(db_path)
                _initialized_databases.add(db_path)
    return db_path


def clear_initialized_databases() -> None:
    """Forget initialized paths after application data has been reset."""
    _initialized_databases.clear()


__all__ = [
    "WorkspaceNotFoundError",
    "WorkspaceRequiredError",
    "WorkspaceResolutionError",
    "clear_initialized_databases",
    "ensure_workspace_db",
    "initialized_databases",
    "resolve_workspace_id",
]
