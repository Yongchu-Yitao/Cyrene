"""Workspace identity rules owned by the knowledge Plugin."""

from __future__ import annotations

from typing import Mapping, Any


class WorkspaceRequiredError(ValueError):
    pass


class WorkspaceNotFoundError(LookupError):
    pass


def resolve_workspace(value: str) -> str:
    workspace = str(value or "").strip()
    if not workspace:
        raise WorkspaceRequiredError("workspace is required")
    from cyrene.workbench.sessions.context import read_projects

    if any(str(project.get("id") or "") == workspace for project in read_projects()):
        return workspace
    raise WorkspaceNotFoundError(f"workspace was not found: {workspace}")


def resolve_context_workspace(data: Mapping[str, Any]) -> str:
    run_context = data.get("run_context")
    run_context = run_context if isinstance(run_context, Mapping) else {}
    for key in ("workspace_id", "project_id"):
        value = str(data.get(key) or run_context.get(key) or "").strip()
        if value:
            return resolve_workspace(value)
    session_id = str(data.get("session_id") or run_context.get("session_id") or "").strip()
    if not session_id:
        raise WorkspaceRequiredError(
            "Knowledge tools require context.data.project_id, workspace_id, or session_id"
        )
    from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

    project_id = str(resolve_workbench_project_id_for_session(session_id) or "").strip()
    if not project_id:
        raise WorkspaceNotFoundError(
            f"Workbench session is not attached to a project: {session_id}"
        )
    return resolve_workspace(project_id)


__all__ = [
    "WorkspaceNotFoundError",
    "WorkspaceRequiredError",
    "resolve_context_workspace",
    "resolve_workspace",
]
