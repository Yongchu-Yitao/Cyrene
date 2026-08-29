"""Explicit service graph for Workbench task-session adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyrene.workbench.core_adapter.task_runtime import TaskAgentRuntime
from cyrene.workbench.tasks import task_runs
from cyrene.workbench.tasks.task_execution_service import (
    TaskExecutionApplicationService,
    TaskExecutionDependencies,
)
from cyrene.workbench.tasks.task_session_workflow_service import (
    TaskInitializationApplicationService,
    TaskPlanningWorkflowService,
    TaskRunCoordinationService,
    TaskWorkspaceApplicationService,
)
from cyrene.workbench.tasks.task_services import (
    ArtifactApplicationService,
    PlanningApplicationService,
    TaskApplicationService,
    TaskRouteDependencies,
)


@dataclass(slots=True)
class TaskSessionRouteContext:
    db_path: str
    agent_runtime: TaskAgentRuntime
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

    async def resume_interrupted_run(
        self,
        session_id: str,
        run_type: str,
        body: dict[str, Any],
    ) -> Any:
        """Resume one admitted route operation without creating a second run."""

        handlers = {
            "execution": self.execution.create_run,
            "chat": self.execution.chat,
            "dispatch": self.execution.dispatch,
            "answer": self.execution.answer,
        }
        handler = handlers.get(str(run_type or ""))
        if handler is None:
            raise ValueError(f"unsupported Task run type: {run_type}")
        return await handler(session_id, body)


def build_task_session_context(
    db_path: str,
    *,
    bot: Any = None,
    agent_runtime: TaskAgentRuntime | None = None,
    task_service: TaskApplicationService | None = None,
    artifact_service: ArtifactApplicationService | None = None,
    planning_service: PlanningApplicationService | None = None,
    execution_service: TaskExecutionApplicationService | None = None,
    route_dependencies: TaskRouteDependencies | None = None,
) -> TaskSessionRouteContext:
    deps = route_dependencies or TaskRouteDependencies.from_modules(db_path)
    runtime = agent_runtime or TaskAgentRuntime(bot=bot, db_path=db_path)
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
        resolve_download=deps.artifact_download_target,
    )
    planning = planning_service or PlanningApplicationService(
        lock=deps.store_lock, read_store=deps.read_store,
        find_session=deps.find_session,
        is_task_run_active=task_runs.is_task_run_active, db_path=db_path,
        agent_runtime=runtime,
        mutate_plan=deps.update_task_plan,
        utc_now=deps.utc_now, short_id=deps.short_id, write_store=deps.write_store,
        store_reflection=deps.store_reflection,
        reflection_candidates=deps.reflection_candidates,
        apply_reflection_hints=deps.apply_reflection_hints,
        mark_completed=deps.mark_completed,
    )
    execution = execution_service or TaskExecutionApplicationService(
        dependencies=TaskExecutionDependencies.from_task_routes(deps),
        agent_runtime=runtime,
        task_runs=task_runs, db_path=db_path,
    )
    return TaskSessionRouteContext(
        db_path=db_path, agent_runtime=runtime, dependencies=deps,
        tasks=tasks, artifacts=artifacts,
        planning=planning, execution=execution,
        workspace=TaskWorkspaceApplicationService(deps),
        planning_workflow=TaskPlanningWorkflowService(deps, runtime),
        initialization=TaskInitializationApplicationService(deps, runtime),
        run_coordination=TaskRunCoordinationService(deps, task_runs, db_path),
        task_runs=task_runs,
    )
