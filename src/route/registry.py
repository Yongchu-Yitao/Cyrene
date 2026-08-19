"""Composition root for all Cyrene HTTP and WebSocket routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from cyrene.workbench import runtime as shared
from route.agent.browser import register_browser_routes
from route.agent.chat import register_chat_routes
from route.agent.claude_code import register_claude_code_routes
from route.agent.collaboration import register_collaboration_routes
from route.agent.sessions import register_session_routes
from route.agents import register_agent_routes
from route.agent_model_gateway import register_agent_model_gateway_routes
from route.app_control import register_app_control_routes
from route.backup import register_backup_routes
from route.channels.wechat import register_wechat_routes
from route.code import router as code_router
from route.control import register_control_routes
from route.custom_tools import register_custom_tool_routes
from route.entities import register_entity_routes
from route.extensions import register_extension_routes
from route.hooks import register_hook_routes
from route.errors import install_api_exception_handlers
from route.knowledge import register_knowledge_routes
from route.learning import register_learning_routes
from route.maps.amap import register_amap_routes
from route.maps.map import register_map_routes
from route.memory import register_memory_routes
from route.notifications import register_notification_routes
from route.pdf import register_pdf_routes
from route.remote import register_remote_routes
from route.search import register_search_routes
from route.settings.general import register_settings_routes
from route.settings.model_configuration import register_model_configuration_routes
from route.skills import register_skill_routes
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
from route.workbench.goal_loop import register_goal_loop_routes
from route.workbench.knowledge import register_workbench_knowledge_routes
from route.workbench.library import register_workbench_library_routes
from route.workbench.memory import register_workbench_memory_routes
from route.workbench.projects import register_project_routes
from route.workbench.project_memory import register_project_memory_routes
from route.workbench.schedule import register_workbench_schedule_routes
from route.workbench.task_sessions import register_task_session_routes


def _register_shared_adapter(
    factory: Any,
    router: APIRouter,
    bot: Any,
    db_path: str,
) -> Any:
    """Bind the shared service facade before installing a split adapter.

    The former monolithic adapter kept its route handlers and helper functions
    in one module namespace.  The domain adapters split from that
    file still deliberately consume the same facade while the underlying
    application services are extracted.  Refreshing their globals at
    registration time prevents stale imported values and preserves dependency
    injection/monkeypatch behavior across repeated app factories.
    """
    namespace = factory.__globals__
    namespace.update(
        {
            name: value
            for name, value in vars(shared).items()
            if not name.startswith("__")
        }
    )
    return factory(router, bot, db_path)


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
    register_instance_routes(router)
    register_app_control_routes(router)

    # Small, independent domain adapters.
    register_map_routes(router)
    register_amap_routes(router)
    register_entity_routes(router, db_path)
    register_knowledge_routes(router)
    register_workbench_knowledge_routes(router)
    register_workbench_library_routes(router)
    register_workbench_memory_routes(router, db_path)
    register_project_memory_routes(router, db_path)
    register_workbench_schedule_routes(router, db_path)
    chat_control_adapter = register_workbench_chat_routes(router, bot, db_path)
    project_control_adapter = _register_shared_adapter(
        register_project_routes,
        router,
        bot,
        db_path,
    )
    task_control_adapter = _register_shared_adapter(
        register_task_session_routes,
        router,
        bot,
        db_path,
    )
    goal_loop_manager = register_goal_loop_routes(router, app, db_path)
    goal_loop_control_adapter = goal_loop_manager.control_adapter
    register_control_routes(
        router,
        chat_control_adapter,
        project_control_adapter,
        task_control_adapter,
        goal_loop_control_adapter,
    )
    register_remote_routes(
        router,
        app,
        db_path,
        bot=bot,
        chat_adapter=chat_control_adapter,
        project_adapter=project_control_adapter,
        task_adapter=task_control_adapter,
        goal_loop_adapter=goal_loop_control_adapter,
    )
    register_pdf_routes(router)
    register_agent_model_gateway_routes(router)
    register_model_configuration_routes(router)
    register_custom_tool_routes(router, bot, db_path)
    register_voice_routes(router)
    register_terminal_routes(router)
    router.include_router(code_router)

    # Routes split from the former monolithic adapter.
    for factory in (
        register_shell_routes,
        register_chat_routes,
        register_collaboration_routes,
        register_event_routes,
        register_claude_code_routes,
        register_browser_routes,
        register_session_routes,
        register_learning_routes,
        register_extension_routes,
        register_agent_routes,
        register_hook_routes,
        register_skill_routes,
        register_search_routes,
        register_usage_routes,
        register_backup_routes,
        register_notification_routes,
        register_memory_routes,
        register_settings_routes,
        register_task_routes,
        register_update_routes,
        register_log_routes,
    ):
        _register_shared_adapter(factory, router, bot, db_path)

    app.include_router(router)

__all__ = ["register_routes"]
