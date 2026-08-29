"""Composition root for the split Workbench chat HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from cyrene.workbench.http.workbench.chat_routes.chats import register_chat_routes
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext
from cyrene.workbench.http.workbench.chat_routes.conversation_context import register_context_routes
from cyrene.workbench.http.workbench.chat_routes.files import register_file_routes
from cyrene.workbench.http.workbench.chat_routes.runs import register_run_routes


def register_workbench_chat_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
    *,
    context: ChatRouteContext | None = None,
) -> None:
    context = context or ChatRouteContext.create(bot=bot, db_path=db_path)
    run_api = register_run_routes(router, context)
    register_chat_routes(
        router,
        context,
        send_chat_detached=run_api["send_chat_detached"],
    )
    register_context_routes(
        router,
        context.conversation_context,
        context.conversation_inbox,
    )
    register_file_routes(router, context)


__all__ = [
    "register_workbench_chat_routes",
]
