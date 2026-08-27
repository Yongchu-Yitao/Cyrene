from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from route.workbench.chat_routes.attachment_routes import register_attachment_routes
from route.workbench.chat_routes.agent_config_routes import register_agent_config_routes
from route.workbench.chat_routes.collection_routes import register_collection_routes
from route.workbench.chat_routes.context_catalog_routes import register_context_catalog_routes
from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.delete_routes import register_delete_routes
from route.workbench.chat_routes.detail_routes import register_detail_routes
from route.workbench.chat_routes.fork_routes import register_fork_routes
from route.workbench.chat_routes.groups_routes import register_groups_routes
from route.workbench.chat_routes.pinned_routes import register_pinned_routes
from route.workbench.chat_routes.side_agents_routes import register_side_agents_routes
from route.workbench.chat_routes.to_task_routes import register_to_task_routes
from route.workbench.chat_routes.voice_routes import register_voice_routes


def register_chat_routes(
    router: APIRouter,
    context: ChatRouteContext,
    *,
    send_chat_detached,
) -> dict[str, Any]:
    register_attachment_routes(router)
    register_pinned_routes(router, context)
    register_context_catalog_routes(router)
    handlers: dict[str, Any] = {}
    handlers.update(register_collection_routes(router, context) or {})
    register_voice_routes(
        router,
        context,
        send_chat_detached=send_chat_detached,
    )
    register_side_agents_routes(router, context)
    handlers.update(register_detail_routes(router, context) or {})
    register_agent_config_routes(router, context)
    register_groups_routes(router, context)
    handlers.update(register_delete_routes(router, context) or {})
    register_fork_routes(router, context)
    register_to_task_routes(router, context)
    return handlers
