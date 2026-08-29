"""Composition root for Workbench project HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from cyrene.workbench.projects.project_files import ProjectFileService
from cyrene.workbench.projects.project_services import ProjectApplicationService
from cyrene.workbench.http.workbench.project_routes.files import register_project_query_file_routes
from cyrene.workbench.http.workbench.project_routes.lifecycle import register_project_lifecycle_routes
from cyrene.workbench.http.workbench.project_routes.notifications import (
    register_project_notification_routes,
)
from cyrene.workbench.http.workbench.project_routes.tasks import register_project_task_routes


def register_project_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    *,
    file_service: ProjectFileService,
    project_service: ProjectApplicationService,
) -> dict[str, Any]:
    """Register project adapters in their historical OpenAPI order."""
    del bot, db_path
    register_project_query_file_routes(router, project_service, file_service)
    register_project_notification_routes(router, project_service)
    register_project_lifecycle_routes(router, project_service)
    return register_project_task_routes(router, project_service)


__all__ = ["register_project_routes"]
