"""Shared lifecycle helpers for Workbench chat run routes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from cyrene.workbench.chat.chat_runs import schedule_post_reply_bookkeeping
from cyrene.workbench.chat.chat_service import ChatService

logger = logging.getLogger(__name__)

_SESSION_TITLE_TASKS: set[asyncio.Task[Any]] = set()


def finish_detached_done(
    registry: set[asyncio.Task[Any]],
    error_context: str,
    task: asyncio.Task[Any],
) -> None:
    registry.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("%s", error_context, exc_info=exc)


def track_session_title_task(task: asyncio.Task[Any]) -> None:
    _SESSION_TITLE_TASKS.add(task)
    task.add_done_callback(
        lambda completed: finish_detached_done(
            _SESSION_TITLE_TASKS,
            "Failed to inspect Workbench session naming task",
            completed,
        )
    )


def schedule_workspace_changes_finalize(
    service: ChatService,
    *,
    chat_id: str,
    run_id: str,
    workspace_dir: str | Path | None,
    before: Any,
    status: str,
) -> None:
    async def finalize() -> None:
        try:
            await service.finalize_workspace_changes(
                chat_id=chat_id,
                run_id=run_id,
                workspace_dir=workspace_dir,
                before=before,
                status=status,
            )
        except Exception:
            logger.exception(
                "Background workspace changes finalize failed for chat %s",
                chat_id,
            )

    schedule_post_reply_bookkeeping(
        finalize(),
        error_context=f"workspace changes finalize for chat {chat_id}",
    )


__all__ = [
    "schedule_workspace_changes_finalize",
    "track_session_title_task",
]
