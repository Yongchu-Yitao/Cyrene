"""Composition root for the versioned desktop-local Control API."""

from __future__ import annotations

from fastapi import APIRouter

from cyrene.workbench.chat.chat_attachment_service import (
    referenced_chat_attachment_target,
)
from cyrene.workbench.control.control_services import (
    ControlArtifactQueryService,
    ControlProjectQueryService,
    ControlRunService,
)
from cyrene.workbench.projects.project_services import ProjectApplicationService
from cyrene.workbench.control.control_ports import (
    WorkbenchChatApplicationPort,
)
from cyrene.workbench.http.control_routes.artifacts import register_artifact_routes
from cyrene.workbench.http.control_routes.capabilities import register_capability_routes
from cyrene.workbench.http.control_routes.common import public_event
from cyrene.workbench.http.control_routes.projects_chats import register_project_chat_routes
from cyrene.workbench.http.control_routes.runs import register_run_routes
from cyrene.workbench.http.control_routes.goals import register_goal_routes


def register_control_routes(
    router: APIRouter,
    chat: WorkbenchChatApplicationPort,
    *,
    project_service: ProjectApplicationService,
) -> None:
    """Compose typed Control services and install focused HTTP registrars."""
    projects = ControlProjectQueryService(
        projects=project_service,
        chat=chat,
    )
    runs = ControlRunService(chat=chat, public_event=public_event)
    artifacts = ControlArtifactQueryService(
        chat=chat,
        resolve_attachment=referenced_chat_attachment_target,
    )

    register_capability_routes(router)
    register_project_chat_routes(router, projects, run_manager=chat.run_manager)
    register_run_routes(router, runs)
    register_goal_routes(router)
    register_artifact_routes(router, artifacts)


__all__ = ["register_control_routes"]
