"""Composition root for all Cyrene HTTP and WebSocket routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from cyrene.agent.auto_review import review_elevation
from cyrene.agent.state import session_state_file
from cyrene.config import BASE_DIR, DATA_DIR, TEMP_DIR, WORKSPACE_DIR
from cyrene.observability import debug
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.runtime.log_repository import LogRepository
from cyrene.runtime.backup import BackupRepository
from cyrene.tooling.runtime_api import (
    resolve_workspace_path,
    resolve_workspace_write_target,
)
from cyrene.extensions.application_service import (
    ExtensionApplicationService,
    ExtensionInstallInputService,
)
from cyrene.extensions.service import (
    audit_records,
    get_extension_service,
    source_settings,
    update_source_settings,
)
from cyrene.hooks.application_service import HookApplicationService
from cyrene.hooks.config_agent import configuration_results, schedule_cli_configuration
from cyrene.hooks.service import (
    get_hook_service,
    hook_audit_records,
    public_hook_config,
    public_hook_proposal,
)
from cyrene.learning.application_service import (
    LearningApplicationService,
    MediaRepository,
    ProjectResolver,
    ToolChainProjection,
)
from cyrene.runtime.update_service import build_update_application_service
from cyrene.workbench import runtime as shared
from cyrene.workbench import goal_loop as goal_loop_runtime
from cyrene.workbench import task_runs as task_run_service
from cyrene.workbench.chat_repository import ChatRepository
from cyrene.workbench.conversation_context_service import SessionStateRepository
from cyrene.workbench.code_format_service import CodeFormatService
from cyrene.workbench.memory import MemoryApplicationService
from cyrene.workbench.project_memory_prompt import (
    ProjectMemoryApplicationService,
    ProjectQueryPort,
)
from cyrene.workbench.project_composition import build_project_application_service
from cyrene.workbench.project_files import ProjectFileService
from cyrene.workbench.goal_loop_repository import SqliteGoalLoopRepository
from cyrene.workbench.goal_loop_service import GoalLoopApplicationService
from cyrene.workbench.global_chat_service import GlobalChatApplicationService
from cyrene.workbench.context import (
    read_projects as read_workbench_projects,
    resolve_workbench_project_id,
)
from cyrene.workbench.presentation_runtime import build_status
from cyrene.workbench.presentation_service import PresentationQueryService
from cyrene.workbench.notifications import append_notification as append_workbench_notification
from cyrene.workbench.project_services import (
    ProjectApplicationService,
    ProjectRouteDependencies,
)
from cyrene.workbench.task_services import (
    ArtifactApplicationService,
    PlanningApplicationService,
    TaskApplicationService,
    TaskRouteDependencies,
)
from cyrene.workbench.task_execution_service import (
    TaskExecutionApplicationService,
    TaskExecutionDependencies,
)
from cyrene.workbench.schedule_repository import ScheduleRepository, WorkspaceProjectResolver
from cyrene.workbench.schedule_service import ScheduleApplicationService
from cyrene.workbench.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchGoalLoopApplicationPort,
    WorkbenchProjectApplicationPort,
    WorkbenchTaskApplicationPort,
)
from cyrene.workbench.subagent_messaging_service import SubagentMessagingService
from cyrene.workbench.workspace_diff_service import WorkspaceDiffService
from cyrene.runtime.scheduler import reset_lottery as reset_agent_lottery
from route.agent.browser import register_browser_routes
from route.agent.chat import register_chat_routes
from route.agent.collaboration import register_collaboration_routes
from route.agent.sessions import register_session_routes
from route.agents import register_agent_routes
from route.agent_model_gateway import register_agent_model_gateway_routes
from route.app_control import register_app_control_routes
from route.backup import register_backup_routes
from route.channels.wechat import register_wechat_routes
from route.code import register_code_routes
from route.control import register_control_routes
from route.custom_tools import register_custom_tool_routes
from route.entities import register_entity_routes
from route.extensions import register_extension_routes
from route.hooks import register_hook_routes
from route.errors import install_api_exception_handlers
from route.learning import register_learning_routes
from route.maps.amap import register_amap_routes
from route.maps.map import register_map_routes
from route.memory import register_memory_routes
from route.notifications import register_notification_routes
from route.pdf import register_pdf_routes
from route.plugins import register_plugin_routes
from route.remote import register_remote_routes
from route.search import register_search_routes
from route.settings.general import register_settings_routes
from route.settings.model_configuration import register_model_configuration_routes
from route.settings.office import register_office_integration_routes
from route.system.events import register_event_routes
from route.system.instance import register_instance_routes
from route.system.logs import register_log_routes
from route.system.shell import register_shell_routes
from route.system.terminal import register_terminal_routes
from route.system.updates import register_update_routes
from route.tasks import register_task_routes
from route.usage import register_usage_routes
from route.voice import register_voice_routes
from route.workbench.chat import register_workbench_chat_routes
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.run_answer_routes import ChatAnswerController
from route.workbench.chat_routes.run_send_routes import ChatSendController
from route.workbench.goal_loop import register_goal_loop_routes
from route.workbench.library import register_workbench_library_routes
from route.workbench.memory import register_workbench_memory_routes
from route.workbench.projects import register_project_routes
from route.workbench.project_memory import register_project_memory_routes
from route.workbench.schedule import register_workbench_schedule_routes
from route.workbench.task_sessions import register_task_session_routes
from route.workbench.task_session_routes.context import build_task_session_context
from route.workspace import validate_workspace_path


def _build_project_services(
    db_path: str,
) -> tuple[ProjectFileService, ProjectApplicationService]:
    dependencies = ProjectRouteDependencies.from_runtime(shared)
    files = ProjectFileService(
        find_project=dependencies.find_project_lightweight,
        resolve_workspace=dependencies.resolve_workspace,
        resolve_workspace_async=dependencies.resolve_workspace_async,
        resolve_active_path=resolve_workspace_path,
        resolve_active_write_target=resolve_workspace_write_target,
    )
    projects = build_project_application_service(
        db_path,
        dependencies,
        validate_workspace=validate_workspace_path,
    )
    return files, projects


def _build_task_services(
    db_path: str,
) -> tuple[TaskApplicationService, ArtifactApplicationService, PlanningApplicationService]:
    route_dependencies = TaskRouteDependencies.from_runtime(shared)
    tasks = TaskApplicationService(
        read_store=shared._read_workbench_store,
        find_session=shared._workbench_find_session,
        project_shell=shared._workbench_project_shell,
        workspace_root=shared._workbench_workspace_root,
        write_store=shared._write_workbench_store,
        utc_now=shared._utc_now_iso,
        prune_artifacts=shared._workbench_prune_non_file_artifacts,
        plan_signature=shared._workbench_plan_definition_signature,
        normalize_plan=shared._workbench_normalize_plan,
        validate_plan=shared._workbench_validate_plan_graph,
        mark_completed=shared._workbench_mark_completed_if_acceptance_passed,
        notify=shared.append_notification,
    )
    artifacts = ArtifactApplicationService(
        read_store=shared._read_workbench_store,
        find_session=shared._workbench_find_session,
        backfill_referenced_artifacts=shared._workbench_backfill_referenced_file_artifacts,
        write_store=shared._write_workbench_store,
        utc_now=shared._utc_now_iso,
        resolve_download=shared._workbench_artifact_download_target,
    )
    planning = PlanningApplicationService(
        lock=shared._WORKBENCH_STORE_LOCK,
        read_store=shared._read_workbench_store,
        find_session=shared._workbench_find_session,
        is_session_running=route_dependencies.is_session_running,
        is_task_run_active=task_run_service.is_task_run_active,
        db_path=db_path,
        mutate_plan=route_dependencies.update_task_plan,
        generate_acceptance_criteria=route_dependencies.generate_acceptance_criteria,
        utc_now=route_dependencies.utc_now,
        short_id=route_dependencies.short_id,
        write_store=route_dependencies.write_store,
        run_reflection=route_dependencies.run_reflection,
        store_reflection=route_dependencies.store_reflection,
        dispatch_reflection_hints=route_dependencies.dispatch_reflection_hints,
        verify_acceptance=route_dependencies.verify_acceptance,
        generation_error=route_dependencies.generation_error,
        mark_completed=route_dependencies.mark_completed,
    )
    return tasks, artifacts, planning


def _register_workbench_routes(
    router: APIRouter,
    app: FastAPI,
    bot: Any,
    db_path: str,
) -> tuple[
    WorkbenchChatApplicationPort,
    WorkbenchProjectApplicationPort,
    WorkbenchTaskApplicationPort,
    WorkbenchGoalLoopApplicationPort,
    ProjectApplicationService,
    ArtifactApplicationService,
    ProjectFileService,
    ScheduleApplicationService,
]:
    register_workbench_library_routes(router)
    memory_service = MemoryApplicationService(db_path)
    register_workbench_memory_routes(router, memory_service)
    chat_repository = ChatRepository()
    chat_repository.configure(db_path)
    register_project_memory_routes(
        router,
        ProjectMemoryApplicationService(
            db_path,
            ProjectQueryPort(shared._workbench_find_project_lightweight),
            chat_repository,
            memory_service,
        ),
    )
    schedule = ScheduleApplicationService(
        ScheduleRepository(db_path),
        WorkspaceProjectResolver(
            find_project_lightweight=shared._workbench_find_project_lightweight,
            read_projects=read_workbench_projects,
        ),
        append_workbench_notification,
    )
    register_workbench_schedule_routes(router, application_service=schedule)
    chat_context = ChatRouteContext.create(bot=bot, db_path=db_path)
    register_workbench_chat_routes(router, bot, db_path, context=chat_context)
    chat = WorkbenchChatApplicationPort(
        context=chat_context,
        send=ChatSendController(chat_context).send_domain,
        answer=ChatAnswerController(chat_context).answer,
    )
    files, projects = _build_project_services(db_path)
    register_project_routes(
        router,
        bot,
        db_path,
        file_service=files,
        project_service=projects,
    )
    tasks, artifacts, planning = _build_task_services(db_path)
    task_dependencies = TaskRouteDependencies.from_runtime(shared)
    task_context = build_task_session_context(
        db_path,
        shared,
        task_service=tasks,
        artifact_service=artifacts,
        planning_service=planning,
        execution_service=TaskExecutionApplicationService(
            dependencies=TaskExecutionDependencies.from_runtime(shared),
            task_runs=task_run_service,
            db_path=db_path,
        ),
        route_dependencies=task_dependencies,
    )
    register_task_session_routes(
        router,
        bot,
        db_path,
        context=task_context,
    )
    goal_manager = goal_loop_runtime.GoalLoopManager(db_path)
    goal_loop_runtime.register_goal_loop_manager(db_path, goal_manager)
    goal_repository = SqliteGoalLoopRepository(db_path)
    goal_service = GoalLoopApplicationService(
        goal_repository,
        goal_repository,
        goal_loop_runtime.WorkbenchGoalLoopTransaction(),
        goal_manager,
    )
    register_goal_loop_routes(
        router,
        app,
        application_service=goal_service,
        manager=goal_manager,
    )
    return (
        chat,
        WorkbenchProjectApplicationPort(projects),
        WorkbenchTaskApplicationPort(task_context),
        WorkbenchGoalLoopApplicationPort(goal_service),
        projects,
        artifacts,
        files,
        schedule,
    )


def _register_global_agent_routes(
    router: APIRouter, bot: Any, db_path: str
) -> None:
    subagent_messaging = SubagentMessagingService(bot, db_path)
    register_chat_routes(
        router,
        GlobalChatApplicationService(
            db_path,
            bot=bot,
            subagents=subagent_messaging,
            reset_agent_lottery=reset_agent_lottery,
        ),
    )
    register_collaboration_routes(router, subagent_messaging)


def _register_remaining_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    queries = PresentationQueryService()
    register_shell_routes(router, queries)
    register_event_routes(
        router,
        DebugEventRepository(
            DATA_DIR,
            recent_events=debug.get_recent_events,
            full_event=debug.get_full_event,
            subscribe_events=debug.subscribe,
        ),
    )
    for factory in (register_browser_routes, register_session_routes):
        factory(router, bot, db_path)
    media = MediaRepository(DATA_DIR)
    register_learning_routes(
        router,
        LearningApplicationService(
            ProjectResolver(resolve_workbench_project_id),
            media,
            ToolChainProjection(media),
            build_status,
        ),
    )
    extension_service = get_extension_service()
    register_extension_routes(
        router,
        ExtensionApplicationService(
            extension_service,
            ExtensionInstallInputService(extension_service, TEMP_DIR),
            source_get=source_settings,
            source_update=update_source_settings,
            audit_get=audit_records,
        ),
    )
    register_agent_routes(router, bot, db_path)
    register_hook_routes(
        router,
        HookApplicationService(
            get_hook_service(),
            reviewer=review_elevation,
            public_hook=public_hook_config,
            public_proposal=public_hook_proposal,
            configuration_results=configuration_results,
            audit_records=hook_audit_records,
            extension_cards=extension_service.list_extensions,
            schedule_configuration=schedule_cli_configuration,
        ),
    )
    register_search_routes(router, queries)
    register_usage_routes(router, bot, db_path)
    register_backup_routes(router, BackupRepository())
    register_notification_routes(router, bot, db_path)
    register_memory_routes(router, queries)
    register_settings_routes(router, bot, db_path, queries=queries)
    register_update_routes(
        router, build_update_application_service(BASE_DIR / "CHANGELOG.md")
    )
    register_log_routes(router, LogRepository(DATA_DIR))


def register_routes(app: FastAPI, bot: Any, db_path: str) -> None:
    """Install every Cyrene API adapter on ``app`` exactly once."""
    shared._bot = bot
    shared._db_path = db_path
    shared._configure_workbench_store(db_path)

    from cyrene.workbench.context import configure_store as configure_workbench_context
    from cyrene.workbench.notifications import (
        configure_store as configure_notifications_store,
    )

    configure_notifications_store(db_path)
    configure_workbench_context(db_path)
    install_api_exception_handlers(app)
    register_wechat_routes(app)

    router = APIRouter()
    from cyrene.plugins import get_plugin_manager

    plugin_manager = get_plugin_manager()
    app.state.plugin_manager = plugin_manager
    register_plugin_routes(router, plugin_manager)
    register_instance_routes(router)
    register_app_control_routes(router)

    # Small, independent domain adapters.
    register_map_routes(router, SessionStateRepository(session_state_file))
    register_amap_routes(router)
    register_entity_routes(router, db_path)
    (
        chat_control_port,
        project_control_port,
        task_control_port,
        goal_loop_control_port,
        control_project_service,
        control_artifact_service,
        code_file_service,
        schedule_service,
    ) = _register_workbench_routes(
        router, app, bot, db_path
    )
    register_control_routes(
        router,
        chat_control_port,
        project_control_port,
        task_control_port,
        goal_loop_control_port,
        project_service=control_project_service,
        artifact_service=control_artifact_service,
    )
    register_remote_routes(
        router,
        app,
        db_path,
        bot=bot,
        chat=chat_control_port,
        projects=project_control_port,
        tasks=task_control_port,
        goals=goal_loop_control_port,
        utc_now=shared._utc_now_iso,
    )
    register_task_routes(
        router,
        application_service=schedule_service,
        request_shutdown=lambda: app.state.request_shutdown(),
    )
    register_pdf_routes(router)
    register_agent_model_gateway_routes(router)
    register_model_configuration_routes(router)
    register_office_integration_routes(router)
    register_custom_tool_routes(router, bot, db_path)
    register_voice_routes(router)
    register_terminal_routes(router)
    register_code_routes(
        router,
        code_file_service,
        WorkspaceDiffService(code_file_service, WORKSPACE_DIR),
        CodeFormatService(TEMP_DIR),
    )

    _register_global_agent_routes(router, bot, db_path)
    _register_remaining_routes(router, bot, db_path)

    app.include_router(router)

    from cyrene.runtime.shell_wake import get_shell_wake_service

    app.state.terminal_wake_bridge = get_shell_wake_service()

__all__ = ["register_routes"]
