"""Explicit service graph for Workbench task-session adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyrene.workbench import task_runs
from cyrene.workbench.task_execution_service import (
    TaskExecutionApplicationService,
    TaskExecutionDependencies,
)
from cyrene.workbench.task_session_workflow_service import (
    TaskInitializationApplicationService,
    TaskPlanningWorkflowService,
    TaskRunCoordinationService,
    TaskWorkspaceApplicationService,
)
from cyrene.workbench.task_services import (
    ArtifactApplicationService,
    PlanningApplicationService,
    TaskApplicationService,
    TaskRouteDependencies,
)


@dataclass(slots=True)
class TaskSessionRouteContext:
    db_path: str
    dependencies: TaskRouteDependencies
    tasks: TaskApplicationService
    artifacts: ArtifactApplicationService
    planning: PlanningApplicationService
    execution: TaskExecutionApplicationService
    workspace: TaskWorkspaceApplicationService
    planning_workflow: TaskPlanningWorkflowService
    initialization: TaskInitializationApplicationService
    run_coordination: TaskRunCoordinationService
    task_runs: Any


def build_task_session_context(
    db_path: str,
    runtime: Any,
    *,
    task_service: TaskApplicationService | None = None,
    artifact_service: ArtifactApplicationService | None = None,
    planning_service: PlanningApplicationService | None = None,
    execution_service: TaskExecutionApplicationService | None = None,
    route_dependencies: TaskRouteDependencies | None = None,
) -> TaskSessionRouteContext:
    deps = route_dependencies or TaskRouteDependencies.from_runtime(runtime)
    tasks = task_service or TaskApplicationService(
        read_store=deps.read_store, find_session=deps.find_session,
        project_shell=deps.project_shell, workspace_root=deps.workspace_root,
        write_store=deps.write_store, utc_now=deps.utc_now,
        prune_artifacts=deps.prune_artifacts, plan_signature=deps.plan_signature,
        normalize_plan=deps.normalize_plan, validate_plan=deps.validate_plan,
        mark_completed=deps.mark_completed, notify=deps.notify,
    )
    artifacts = artifact_service or ArtifactApplicationService(
        read_store=deps.read_store, find_session=deps.find_session,
        backfill_referenced_artifacts=deps.backfill_artifacts,
        write_store=deps.write_store, utc_now=deps.utc_now,
        resolve_download=deps.artifact_download_target,
    )
    planning = planning_service or PlanningApplicationService(
        lock=deps.store_lock, read_store=deps.read_store,
        find_session=deps.find_session, is_session_running=deps.is_session_running,
        is_task_run_active=task_runs.is_task_run_active, db_path=db_path,
        mutate_plan=deps.update_task_plan,
        generate_acceptance_criteria=deps.generate_acceptance_criteria,
        utc_now=deps.utc_now, short_id=deps.short_id, write_store=deps.write_store,
        run_reflection=deps.run_reflection, store_reflection=deps.store_reflection,
        dispatch_reflection_hints=deps.dispatch_reflection_hints,
        verify_acceptance=deps.verify_acceptance,
        generation_error=deps.generation_error, mark_completed=deps.mark_completed,
    )
    execution = execution_service or TaskExecutionApplicationService(
        dependencies=TaskExecutionDependencies.from_runtime(runtime),
        task_runs=task_runs, db_path=db_path,
    )
    return TaskSessionRouteContext(
        db_path=db_path, dependencies=deps, tasks=tasks, artifacts=artifacts,
        planning=planning, execution=execution,
        workspace=TaskWorkspaceApplicationService(deps),
        planning_workflow=TaskPlanningWorkflowService(deps),
        initialization=TaskInitializationApplicationService(deps),
        run_coordination=TaskRunCoordinationService(deps, task_runs, db_path),
        task_runs=task_runs,
    )
