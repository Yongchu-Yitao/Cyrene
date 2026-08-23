"""Composition root for Workbench task-session HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from cyrene.workbench import runtime as runtime_module
from cyrene.workbench.task_execution_service import TaskExecutionApplicationService
from cyrene.workbench.task_services import (
    ArtifactApplicationService,
    PlanningApplicationService,
    TaskApplicationService,
    TaskRouteDependencies,
)
from route.workbench.task_session_routes.context import build_task_session_context
from route.workbench.task_session_routes.context import TaskSessionRouteContext
from route.workbench.task_session_routes.events_artifacts import register_event_artifact_routes
from route.workbench.task_session_routes.execution import register_execution_routes
from route.workbench.task_session_routes.initialization import register_initialization_routes
from route.workbench.task_session_routes.planning import register_hint_routes, register_planning_routes
from route.workbench.task_session_routes.sessions import register_session_routes


def register_task_session_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    *,
    task_service: TaskApplicationService | None = None,
    artifact_service: ArtifactApplicationService | None = None,
    planning_service: PlanningApplicationService | None = None,
    execution_service: TaskExecutionApplicationService | None = None,
    route_dependencies: TaskRouteDependencies | None = None,
    runtime_service: Any = runtime_module,
    context: TaskSessionRouteContext | None = None,
) -> None:
    """Build the task-session service graph and register its route slices."""
    del bot
    context = context or build_task_session_context(
        db_path,
        runtime_service,
        task_service=task_service,
        artifact_service=artifact_service,
        planning_service=planning_service,
        execution_service=execution_service,
        route_dependencies=route_dependencies,
    )
    register_session_routes(router, context)
    register_planning_routes(router, context)
    register_hint_routes(router, context)
    register_execution_routes(router, context)
    register_initialization_routes(router, context)
    register_event_artifact_routes(router, context)
