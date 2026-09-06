"""Composition root for all Cyrene HTTP and WebSocket routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI

from cyrene.config import BASE_DIR, DATA_DIR, DB_PATH, WORKSPACE_DIR
from cyrene.observability import debug
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.platform.log_repository import LogRepository
from cyrene.platform.backup import BackupRepository
from cyrene.platform.update_service import build_update_application_service
from cyrene.workbench.projects import project_repository
from cyrene.workbench.projects.project_composition import build_project_application_service
from cyrene.workbench.projects.project_files import ProjectFileService
from cyrene.workbench.artifacts.presentation_service import PresentationQueryService
from cyrene.workbench.projects.project_services import (
    ProjectApplicationService,
    ProjectRouteDependencies,
)
from cyrene.workbench.control.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchProjectApplicationPort,
)
from cyrene.workbench.http.agent.sessions import register_session_routes
from cyrene.workbench.http.app_control import register_app_control_routes
from cyrene.workbench.http.backup import register_backup_routes
from cyrene.workbench.http.control import register_control_routes
from cyrene.workbench.http.errors import install_api_exception_handlers
from cyrene.workbench.http.notifications import register_notification_routes
from cyrene.workbench.http.plugins import register_plugin_routes
from cyrene.workbench.http.settings.general import register_settings_routes
from cyrene.workbench.http.system.events import register_event_routes
from cyrene.workbench.http.system.instance import register_instance_routes
from cyrene.workbench.http.system.logs import register_log_routes
from cyrene.workbench.http.system.shell import register_shell_routes
from cyrene.workbench.http.system.updates import register_update_routes
from cyrene.workbench.http.system.shutdown import register_shutdown_route
from cyrene.workbench.http.usage import register_usage_routes
from cyrene.workbench.http.workbench.chat import register_workbench_chat_routes
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext
from cyrene.workbench.http.workbench.chat_routes.run_answer_routes import ChatAnswerController
from cyrene.workbench.http.workbench.chat_routes.run_send_routes import ChatSendController
from cyrene.workbench.http.workbench.projects import register_project_routes
from cyrene.workbench.http.workspace import validate_workspace_path


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
) -> tuple[ProjectFileService, ProjectApplicationService]:
    dependencies = ProjectRouteDependencies.from_modules()
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
        validate_workspace=validate_workspace_path,
    )
    return files, projects


def _register_workbench_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
) -> tuple[
    WorkbenchChatApplicationPort,
    WorkbenchProjectApplicationPort,
    ProjectApplicationService,
    ProjectFileService,
]:
    chat_context = ChatRouteContext.create(
        bot=bot,
        db_path=db_path,
    )
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
    return (
        chat,
        WorkbenchProjectApplicationPort(projects),
        projects,
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
    register_session_routes(router, bot, db_path)
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

    from cyrene.workbench.sessions.context import configure_store as configure_workbench_context
    from cyrene.workbench.application.notifications import (
        configure_store as configure_notifications_store,
    )

    configure_notifications_store(db_path)
    configure_workbench_context(db_path)
    install_api_exception_handlers(app)
    router = APIRouter()
    plugin_application_host = getattr(
        app.state,
        "plugin_application_host",
        None,
    )
    if plugin_application_host is None:
        from cyrene.plugins import (
            PluginApplicationHost,
            set_application_plugin_scope,
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
        set_application_plugin_scope(plugin_application_host)
    register_plugin_routes(router, plugin_application_host)
    from cyrene.platform.doctor.service import DoctorService
    from cyrene.workbench.http.system.doctor import register_doctor_routes
    doctor = DoctorService(data=Path(plugin_application_host.data_directory), database=Path(db_path),
                           plugins=plugin_application_host.plugin_directory, host=plugin_application_host)
    app.state.doctor_service = doctor
    plugin_application_host.services["doctor"] = doctor
    from cyrene.platform.doctor.http import DoctorIncidentMiddleware
    app.add_middleware(DoctorIncidentMiddleware, service=doctor)
    register_doctor_routes(router, doctor)
    plugin_application_host.attach(router)
    queries = PresentationQueryService(
        db_path=db_path,
        plugin_host=plugin_application_host,
    )
    register_instance_routes(router)
    register_app_control_routes(router)

    # Small, independent domain adapters.
    (
        chat_control_port,
        project_control_port,
        control_project_service,
        _code_file_service,
    ) = _register_workbench_routes(
        router, bot, db_path
    )
    plugin_application_host.services["workbench_chat"] = chat_control_port
    plugin_application_host.services["workbench_projects"] = project_control_port
    register_control_routes(
        router,
        chat_control_port,
        project_service=control_project_service,
    )
    register_shutdown_route(
        router,
        request_shutdown=lambda: app.state.request_shutdown(),
    )
    _register_remaining_routes(router, bot, db_path, queries)

    app.include_router(router)

__all__ = ["register_routes"]
