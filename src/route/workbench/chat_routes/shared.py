"""Shared lifecycle helpers for Workbench chat run routes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from cyrene.workbench.chat_runs import schedule_post_reply_bookkeeping
from cyrene.workbench.chat_service import ChatService

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


def schedule_structured_memory_capture(
    runtime: Any,
    *,
    project_id: str,
    user_text: str,
    agent_text: str,
    state_messages: list[dict[str, Any]],
    prior_message_ids: set[str],
    session_id: str,
) -> None:
    from cyrene.workbench.memory import build_verified_tool_evidence

    round_id = next(
        (
            str(item.get("round_id") or item.get("roundId") or "").strip()
            for item in reversed(state_messages)
            if isinstance(item, dict) and str(item.get("round_id") or item.get("roundId") or "").strip()
        ),
        "",
    )
    runtime.schedule_capture(
        project_id,
        user_text,
        agent_text,
        verified_evidence=build_verified_tool_evidence(
            state_messages,
            prior_message_ids,
        ),
        session_id=session_id,
        round_id=round_id,
    )


def schedule_reply_bookkeeping(
    service: ChatService,
    *,
    chat_id: str,
    project_id: str,
    user_text: str,
    reply_text: str,
    prior_message_ids: set[str],
    command: str,
    retry: bool,
    turn_count: int,
) -> None:
    from cyrene.agent.context import session_state_file, state_file_signature

    state_path = session_state_file(chat_id)
    signature_before = state_file_signature(state_path)

    async def bookkeeping() -> None:
        try:
            if not command and not retry:
                state_messages = await asyncio.to_thread(
                    service.session_state_messages,
                    chat_id,
                )
                if state_file_signature(state_path) != signature_before:
                    logger.info(
                        "Skip post-reply bookkeeping for %s: state changed mid-read",
                        chat_id,
                    )
                    return
                from cyrene.workbench import runtime

                schedule_structured_memory_capture(
                    runtime,
                    project_id=project_id,
                    user_text=user_text,
                    agent_text=reply_text,
                    state_messages=state_messages,
                    prior_message_ids=prior_message_ids,
                    session_id=chat_id,
                )

            from cyrene.workbench.project_memory_prompt import (
                completed_context_snapshot,
                context_auto_trigger_threshold,
                schedule_learning,
            )

            snapshot = await asyncio.to_thread(
                completed_context_snapshot,
                chat_id,
                project_id,
                completed_turn_count=turn_count,
                final_assistant_text=reply_text,
            )
            threshold = (
                context_auto_trigger_threshold(
                    project_id,
                    chat_id,
                    snapshot.get("messages") or [],
                )
                if snapshot and not command and not retry
                else None
            )
            if snapshot and threshold is not None:
                if state_file_signature(state_path) != signature_before:
                    logger.info(
                        "Skip learning schedule for %s: state changed mid-read",
                        chat_id,
                    )
                    return
                snapshot["contextThresholdPercent"] = threshold
                schedule_learning(
                    project_id,
                    snapshot,
                    source="conversation_auto",
                    reason=f"context_{threshold}_percent",
                )
        except Exception:
            logger.exception("Post-reply bookkeeping failed for chat %s", chat_id)

    schedule_post_reply_bookkeeping(
        bookkeeping(),
        error_context=f"post-reply bookkeeping for chat {chat_id}",
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
    "schedule_reply_bookkeeping",
    "schedule_structured_memory_capture",
    "schedule_workspace_changes_finalize",
    "track_session_title_task",
]
