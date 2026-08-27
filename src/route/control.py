"""Composition root for the versioned desktop-local Control API."""

from __future__ import annotations

from fastapi import APIRouter

from cyrene.workbench.chat_attachment_service import (
    referenced_chat_attachment_target,
)
from cyrene.workbench.control_services import (
    ControlArtifactQueryService,
    ControlProjectQueryService,
    ControlRunService,
    ControlTaskCommandService,
)
from cyrene.workbench.project_services import ProjectApplicationService
from cyrene.workbench.task_services import ArtifactApplicationService
from cyrene.workbench.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchGoalLoopApplicationPort,
    WorkbenchProjectApplicationPort,
    WorkbenchTaskApplicationPort,
)
from route.control_routes.artifacts import register_artifact_routes
from route.control_routes.capabilities import register_capability_routes
from route.control_routes.common import public_event
from route.control_routes.projects_chats import register_project_chat_routes
from route.control_routes.runs import register_run_routes
from route.control_routes.tasks import register_task_routes


def register_control_routes(
    router: APIRouter,
    chat: WorkbenchChatApplicationPort,
    project_port: WorkbenchProjectApplicationPort,
    task: WorkbenchTaskApplicationPort,
    goals: WorkbenchGoalLoopApplicationPort,
    *,
    project_service: ProjectApplicationService,
    artifact_service: ArtifactApplicationService,
) -> None:
    """Compose typed Control services and install focused HTTP registrars."""
    def interrupt_task(*, session_id: str = "") -> bool:
        """Interrupt the Task coordinator that owns the Plugin Agent turn."""

        return bool(
            task.context.task_runs.interrupt_task_run(
                task.context.db_path,
                session_id,
            )
        )

    projects = ControlProjectQueryService(
        projects=project_service,
        chat=chat,
        project_port=project_port,
    )
    runs = ControlRunService(chat=chat, public_event=public_event)
    tasks = ControlTaskCommandService(
        task=task,
        goals=goals,
        interrupt_task=interrupt_task,
    )
    artifacts = ControlArtifactQueryService(
        artifacts=artifact_service,
        chat=chat,
        resolve_attachment=referenced_chat_attachment_target,
    )

    register_capability_routes(router)
    register_project_chat_routes(router, projects, run_manager=chat.run_manager)
    register_run_routes(router, runs)
    register_task_routes(router, projects, tasks)
    register_artifact_routes(router, artifacts)


__all__ = ["register_control_routes"]
