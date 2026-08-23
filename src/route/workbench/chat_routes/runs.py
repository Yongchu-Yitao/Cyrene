from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from route.workbench.chat_routes.context import ChatRouteContext
from route.workbench.chat_routes.run_action_routes import register_run_action_routes
from route.workbench.chat_routes.run_answer_routes import register_run_answer_routes
from route.workbench.chat_routes.run_respond_routes import register_run_respond_routes
from route.workbench.chat_routes.run_send_routes import register_run_send_routes
from route.workbench.chat_routes.run_stream_routes import register_run_stream_routes


def register_run_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    handlers: dict[str, Any] = {}
    handlers.update(register_run_stream_routes(router, context))
    send_handlers = register_run_send_routes(router, context)
    handlers.update(send_handlers)
    register_run_respond_routes(router, context)
    register_run_action_routes(
        router,
        context,
        send_chat=send_handlers["send_chat_detached"],
    )
    handlers.update(register_run_answer_routes(router, context))
    handlers["run_manager"] = context.service.run_manager
    return handlers
