"""Workbench project resolution for the schedule Plugin adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def safe_workspace_id(workspace_id: str | None) -> str:
    raw = str(workspace_id or "").strip()
    if not raw:
        return "default"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._") or "default"


class WorkspaceProjectResolver:
    """Resolve canonical project ids and data keys to one schedule scope key."""

    def __init__(
        self,
        *,
        find_project_lightweight: Callable[[str], dict[str, Any] | None],
        read_projects: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._find_project_lightweight = find_project_lightweight
        self._read_projects = read_projects

    def resolve(self, workspace_id: str | None) -> str:
        raw = str(workspace_id or "").strip()
        project = self._find_project_lightweight(raw)
        if project:
            return self._project_key(project)
        requested_key = safe_workspace_id(raw)
        for candidate in self._read_projects():
            if not isinstance(candidate, dict):
                continue
            if (
                str(candidate.get("id") or "").strip() == raw
                or self._project_key(candidate) == requested_key
            ):
                return self._project_key(candidate)
        return requested_key

    def scopes(self) -> tuple[str, ...]:
        values = tuple(
            dict.fromkeys(
                self._project_key(project)
                for project in self._read_projects()
                if isinstance(project, dict)
            )
        )
        return values or ("default",)

    @staticmethod
    def _project_key(project: dict[str, Any]) -> str:
        return safe_workspace_id(project.get("dataKey") or project.get("id"))


__all__ = ["WorkspaceProjectResolver", "safe_workspace_id"]
