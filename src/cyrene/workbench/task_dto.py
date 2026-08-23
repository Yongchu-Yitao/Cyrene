"""Typed transport shapes for Workbench task application services."""

from __future__ import annotations

from typing import Any, TypedDict


class TaskSessionDTO(TypedDict, total=False):
    id: str
    projectId: str
    kind: str
    title: str
    goal: str
    status: str
    priority: str
    createdAt: str
    updatedAt: str
    events: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]


class ProjectShellDTO(TypedDict, total=False):
    id: str
    name: str
    description: str
    workspacePath: str
    sessions: list[dict[str, Any]]


class TaskSessionViewDTO(TypedDict):
    projectId: str
    project: ProjectShellDTO | None
    session: TaskSessionDTO


class WorkspacePathStatusDTO(TypedDict, total=False):
    exists: bool
    path: str
    isDir: bool
    error: str


__all__ = [
    "ProjectShellDTO",
    "TaskSessionDTO",
    "TaskSessionViewDTO",
    "WorkspacePathStatusDTO",
]
