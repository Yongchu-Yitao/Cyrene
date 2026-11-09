"""Read-only project scopes for host-created personal-assistant tasks."""

from dataclasses import replace
from collections.abc import Mapping
from itertools import zip_longest
from typing import Any


def reads_all_projects(data: Mapping[str, Any]) -> bool:
    # These values are supplied by the host, never by tool arguments.
    from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID

    return (
        data.get("assistant_owner_session_id") == ASSISTANT_ID
        and data.get("project_id") == ASSISTANT_SCOPE_ID
    )


def read_projects() -> list[dict[str, Any]]:
    from cyrene.workbench.sessions.context import read_projects as load

    return [project for project in load() if project.get("id")]


def project_source(project: Mapping[str, Any]) -> dict[str, str]:
    return {"project_id": str(project["id"]),
            "project_name": str(project.get("name") or project["id"])}


def merge_project_results(groups: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """Interleave per-project rankings so the first project cannot consume the limit."""
    result = []
    for row in zip_longest(*groups):
        for item in row:
            if item is not None:
                result.append(item)
                if len(result) >= limit:
                    return result
    return result


async def read_all_projects(context, method, *, limit, **filters):
    groups = []
    total = 0
    for project in read_projects():
        # Scope only this read. The original context and write scope stay intact.
        scoped = replace(context, data={
            **context.data, "assistant_owner_session_id": "",
            "workspace_id": str(project["id"]), "project_id": str(project["id"]),
        })
        result = await method(scoped, limit=limit, **filters)
        items = result["items"] if isinstance(result, dict) else result
        total += result.get("total", len(items)) if isinstance(result, dict) else len(items)
        groups.append([{**item, **project_source(project)} for item in items])
    return {"items": merge_project_results(groups, limit), "total": total}
