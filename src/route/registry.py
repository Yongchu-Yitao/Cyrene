"""Composition root for all Cyrene HTTP and WebSocket routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI

from agent.workbench.task_runtime import TaskAgentRuntime
from cyrene.config import BASE_DIR, DATA_DIR, DB_PATH, TEMP_DIR, WORKSPACE_DIR
from cyrene.observability import debug
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.runtime.log_repository import LogRepository
from cyrene.runtime.backup import BackupRepository
from cyrene.learning.application_service import (
    LearningApplicationService,
    MediaRepository,
    ProjectResolver,
    ToolChainProjection,
)
from cyrene.runtime.update_service import build_update_application_service
from cyrene.workbench import goal_loop as goal_loop_runtime
from cyrene.workbench import project_repository, project_runtime
from cyrene.workbench import task_runs as task_run_service
from cyrene.workbench.code_format_service import CodeFormatService
from cyrene.workbench.project_composition import build_project_application_service
from cyrene.workbench.project_files import ProjectFileService
from cyrene.workbench.goal_loop_repository import SqliteGoalLoopRepository
from cyrene.workbench.goal_loop_service import GoalLoopApplicationService
from cyrene.workbench.context import (
    resolve_workbench_project_id,
)
from cyrene.workbench.presentation_runtime import build_status
from cyrene.workbench.presentation_service import PresentationQueryService
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
from cyrene.workbench.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchGoalLoopApplicationPort,
    WorkbenchProjectApplicationPort,
    WorkbenchTaskApplicationPort,
)
from cyrene.workbench.workspace_diff_service import WorkspaceDiffService
from route.agent.browser import register_browser_routes
from route.agent.sessions import register_session_routes
from route.agents import register_agent_routes
from route.agent_model_gateway import register_agent_model_gateway_routes
from route.app_control import register_app_control_routes
from route.backup import register_backup_routes
from route.channels.wechat import register_wechat_routes
from route.code import register_code_routes
from route.control import register_control_routes
from route.errors import install_api_exception_handlers
from route.learning import register_learning_routes
from route.media import register_media_routes
from route.notifications import register_notification_routes
from route.pdf import register_pdf_routes
from route.plugins import register_plugin_routes
from route.remote import register_remote_routes
from route.search import register_search_routes
from route.settings.general import register_settings_routes
from route.settings.model_configuration import register_model_configuration_routes
from route.settings.media import register_media_settings_routes
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
from route.workbench.projects import register_project_routes
from route.workbench.task_sessions import register_task_session_routes
from route.workbench.task_session_routes.context import build_task_session_context
from route.workspace import validate_workspace_path


def _resolve_active_workspace_path(path_value: str) -> Path:
    """Resolve a direct UI file operation inside Cyrene's workspace."""

    root = Path(WORKSPACE_DIR).expanduser().resolve()
    candidate = Path(str(path_value or ".")).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Path is outside the Cyrene workspace")
    return resolved


def _build_project_services(
    db_path: str,
    agent_runtime: TaskAgentRuntime,
    knowledge_service: Any = None,
    memory_service: Any = None,
    schedule_service: Any = None,
) -> tuple[ProjectFileService, ProjectApplicationService]:
    dependencies = ProjectRouteDependencies.from_modules(
        generate_init_form=agent_runtime.generate_init_form,
    )
    files = ProjectFileService(
        find_project=dependencies.find_project_lightweight,
        resolve_workspace=dependencies.resolve_workspace,
        resolve_workspace_async=dependencies.resolve_workspace_async,
        resolve_active_path=_resolve_active_workspace_path,
        resolve_active_write_target=_resolve_active_workspace_path,
    )
    projects = build_project_application_service(
        db_path,
        dependencies,
        agent_runtime=agent_runtime,
        validate_workspace=validate_workspace_path,
        knowledge_service=knowledge_service,
        memory_service=memory_service,
        schedule_service=schedule_service,
    )
    return files, projects


def _build_task_services(
    db_path: str,
    route_dependencies: TaskRouteDependencies,
    agent_runtime: TaskAgentRuntime,
) -> tuple[TaskApplicationService, ArtifactApplicationService, PlanningApplicationService]:
    deps = route_dependencies
    tasks = TaskApplicationService(
        read_store=deps.read_store,
        find_session=deps.find_session,
        project_shell=deps.project_shell,
        workspace_root=deps.workspace_root,
        write_store=deps.write_store,
        utc_now=deps.utc_now,
        prune_artifacts=deps.prune_artifacts,
        plan_signature=deps.plan_signature,
        normalize_plan=deps.normalize_plan,
        validate_plan=deps.validate_plan,
        mark_completed=deps.mark_completed,
        notify=deps.notify,
    )
    artifacts = ArtifactApplicationService(
        read_store=deps.read_store,
        find_session=deps.find_session,
        resolve_download=deps.artifact_download_target,
    )
    planning = PlanningApplicationService(
        lock=deps.store_lock,
        read_store=deps.read_store,
        find_session=deps.find_session,
        is_task_run_active=task_run_service.is_task_run_active,
        db_path=db_path,
        agent_runtime=agent_runtime,
        mutate_plan=deps.update_task_plan,
        utc_now=deps.utc_now,
        short_id=deps.short_id,
        write_store=deps.write_store,
        store_reflection=deps.store_reflection,
        reflection_candidates=deps.reflection_candidates,
        apply_reflection_hints=deps.apply_reflection_hints,
        mark_completed=deps.mark_completed,
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
]:
    plugin_application_host = getattr(app.state, "plugin_application_host", None)
    knowledge_service = (
        plugin_application_host.service("knowledge")
        if plugin_application_host is not None
        else None
    )
    schedule_service = (
        plugin_application_host.service("schedules")
        if plugin_application_host is not None
        else None
    )
    memory_service = (
        plugin_application_host.service("memory")
        if plugin_application_host is not None
        else None
    )
    chat_context = ChatRouteContext.create(
        bot=bot,
        db_path=db_path,
        knowledge_service=knowledge_service,
        memory_service=memory_service,
    )
    register_workbench_chat_routes(router, bot, db_path, context=chat_context)
    chat = WorkbenchChatApplicationPort(
        context=chat_context,
        send=ChatSendController(chat_context).send_domain,
        answer=ChatAnswerController(chat_context).answer,
    )
    task_agent_runtime = TaskAgentRuntime(bot=bot, db_path=db_path)
    files, projects = _build_project_services(
        db_path,
        task_agent_runtime,
        knowledge_service,
        memory_service,
        schedule_service,
    )
    register_project_routes(
        router,
        bot,
        db_path,
        file_service=files,
        project_service=projects,
    )
    task_dependencies = TaskRouteDependencies.from_modules(db_path)
    tasks, artifacts, planning = _build_task_services(
        db_path, task_dependencies, task_agent_runtime
    )
    task_context = build_task_session_context(
        db_path,
        bot=bot,
        task_service=tasks,
        artifact_service=artifacts,
        planning_service=planning,
        agent_runtime=task_agent_runtime,
        route_dependencies=task_dependencies,
    )
    app.state.task_session_context = task_context
    register_task_session_routes(
        router,
        bot,
        db_path,
        context=task_context,
    )
    goal_manager = goal_loop_runtime.GoalLoopManager(db_path, task_agent_runtime)
    goal_loop_runtime.register_goal_loop_manager(db_path, goal_manager)
    goal_repository = SqliteGoalLoopRepository(db_path)
    goal_service = GoalLoopApplicationService(
        goal_repository,
        goal_repository,
        goal_loop_runtime.WorkbenchGoalLoopTransaction(task_agent_runtime),
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
    )


def _register_remaining_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    queries: PresentationQueryService,
) -> None:
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
    register_agent_routes(router, bot, db_path)
    register_search_routes(router, queries)
    register_usage_routes(router, bot, db_path)
    register_backup_routes(router, BackupRepository())
    register_notification_routes(router, bot, db_path)
    register_settings_routes(router, bot, db_path, queries=queries)
    register_update_routes(
        router, build_update_application_service(BASE_DIR / "CHANGELOG.md")
    )
    register_log_routes(router, LogRepository(DATA_DIR))


def register_routes(app: FastAPI, bot: Any, db_path: str) -> None:
    """Install every Cyrene API adapter on ``app`` exactly once."""
    project_repository._configure_workbench_store(db_path)

    from cyrene.workbench.context import configure_store as configure_workbench_context
    from cyrene.workbench.notifications import (
        configure_store as configure_notifications_store,
    )

    configure_notifications_store(db_path)
    configure_workbench_context(db_path)
    install_api_exception_handlers(app)
    register_wechat_routes(app)

    router = APIRouter()
    plugin_application_host = getattr(
        app.state,
        "plugin_application_host",
        None,
    )
    if plugin_application_host is None:
        from agent.plugin import (
            PluginApplicationHost,
            set_active_plugin_application_host,
        )

        requested_db = Path(str(db_path or DB_PATH)).expanduser().resolve()
        default_db = Path(DB_PATH).expanduser().resolve()
        isolated_root = requested_db.parent
        plugin_application_host = PluginApplicationHost.load_user_plugins(
            app=app,
            bot=bot,
            db_path=db_path,
            data_directory=(
                DATA_DIR if requested_db == default_db else isolated_root / "data"
            ),
            plugin_directory=(
                None
                if requested_db == default_db
                else isolated_root / "plugin_impl"
            ),
        )
        app.state.plugin_application_host = plugin_application_host
        set_active_plugin_application_host(plugin_application_host)
    register_plugin_routes(router, plugin_application_host)
    plugin_application_host.attach(router)
    queries = PresentationQueryService(
        db_path=db_path,
        frontend_modules=(
            plugin_application_host.frontend_modules
            if plugin_application_host is not None
            else ()
        ),
        search_providers=(
            plugin_application_host.search_providers
            if plugin_application_host is not None
            else {}
        ),
    )
    register_instance_routes(router)
    register_app_control_routes(router)

    # Small, independent domain adapters.
    (
        chat_control_port,
        project_control_port,
        task_control_port,
        goal_loop_control_port,
        control_project_service,
        control_artifact_service,
        code_file_service,
    ) = _register_workbench_routes(
        router, app, bot, db_path
    )
    plugin_application_host.services["workbench_chat"] = chat_control_port
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
        utc_now=project_runtime._utc_now_iso,
    )
    register_task_routes(
        router,
        request_shutdown=lambda: app.state.request_shutdown(),
    )
    register_pdf_routes(router)
    register_agent_model_gateway_routes(router)
    register_model_configuration_routes(router)
    register_media_settings_routes(router)
    register_office_integration_routes(router)
    register_voice_routes(router)
    register_terminal_routes(router)
    register_media_routes(router)
    register_code_routes(
        router,
        code_file_service,
        WorkspaceDiffService(code_file_service, WORKSPACE_DIR),
        CodeFormatService(TEMP_DIR),
    )

    _register_remaining_routes(router, bot, db_path, queries)

    app.include_router(router)

    from cyrene.runtime.shell_wake import get_shell_wake_service
    from cyrene.media.daemon import get_media_daemon
    from cyrene.media.wake import get_media_wake_bridge

    app.state.terminal_wake_bridge = get_shell_wake_service()
    app.state.media_daemon = get_media_daemon()
    app.state.media_wake_bridge = get_media_wake_bridge()

__all__ = ["register_routes"]
