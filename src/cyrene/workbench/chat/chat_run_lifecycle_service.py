"""Chat run lifecycle orchestration independent from the HTTP adapter."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from cyrene.localization import localized
from cyrene.workbench.chat.chat_runs import ChatRun

logger = logging.getLogger(__name__)

AsyncTurn = Callable[[ChatRun], Awaitable[str]]
AsyncFinalize = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class ChatRunLifecycleDependencies:
    run_manager: Any
    capture_workspace_baseline: Callable[..., Awaitable[Any]]
    finalize_workspace_changes: Callable[..., Awaitable[Any]]
    schedule_workspace_finalize: Callable[..., None]
    publish_chat_changed: Callable[..., Awaitable[Any]]
    load_chat_summary: Callable[[str], dict[str, Any]]
    public_message: Callable[[dict[str, Any]], dict[str, Any]]
    error_message: Callable[[Exception, str], str]
    error_metadata: Callable[[Exception], dict[str, Any]]


@dataclass(slots=True)
class ChatRunLifecycleRequest:
    chat_id: str
    project_id: str
    workspace_dir: str
    lang: str
    client_request_id: str
    retry: bool
    detached: bool
    wants_stream: bool
    is_external_agent: bool
    user_entry: dict[str, Any]
    retry_replaced_message_ids: set[str]
    truncate_after_id: str
    state_ids_before: set[str]
    awaiting_user_sentinel: str
    run_turn: AsyncTurn
    finalize_reply: AsyncFinalize
    restore_retry_state: Callable[[], None]
    settle_status: Callable[[], None]
    commit_retry: Callable[[], None]
    stash_pending: Callable[[dict[str, Any] | None], list[dict[str, Any]]]
    notify_attention: Callable[[dict[str, Any] | None], None]
    pending_question_for: Callable[[str], dict[str, Any] | None]
    reply_stream_chunks: Callable[[str], list[str]]
    running_summary: dict[str, Any]


@dataclass(slots=True)
class ChatRunDispatchResult:
    payload: dict[str, Any] | None = None
    status_code: int = 200
    stream: Any = None


class ChatRunLifecycleApplicationService:
    def __init__(self, dependencies: ChatRunLifecycleDependencies) -> None:
        self.dependencies = dependencies

    async def dispatch(self, request: ChatRunLifecycleRequest) -> ChatRunDispatchResult:
        if request.wants_stream:
            return await self._dispatch_streaming(request)
        return await self._dispatch_non_streaming(request)

    async def _dispatch_non_streaming(
        self,
        request: ChatRunLifecycleRequest,
    ) -> ChatRunDispatchResult:
        async def runner(run: ChatRun) -> None:
            await self._run_non_streaming(request, run)

        run, is_new = self.dependencies.run_manager.start_or_get(
            request.chat_id,
            {"type": "ack", "chatId": request.chat_id},
            runner,
            stream=False,
        )
        if not is_new:
            return self._already_running(request.lang)
        await self.dependencies.publish_chat_changed(
            request.chat_id,
            request.project_id,
            "running",
            run_id=run.run_id,
            run_status="running",
            chatSummary=request.running_summary,
            userMessage=self.dependencies.public_message(request.user_entry),
        )
        await run.done.wait()
        await self._publish_settled(request, run)
        return self._non_streaming_response(request, run)

    async def _run_non_streaming(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
    ) -> None:
        before = await self.dependencies.capture_workspace_baseline(
            request.workspace_dir,
            run.run_id,
        )
        try:
            reply = await request.run_turn(run)
        except asyncio.CancelledError:
            await self._finalize_workspace(request, run, before, "cancelled")
            await asyncio.to_thread(request.restore_retry_state)
            raise
        except Exception as exc:
            logger.exception("Workbench chat run failed for %s", request.chat_id)
            await self._finalize_workspace(request, run, before, "error")
            await asyncio.to_thread(request.restore_retry_state)
            run.outcome = {"kind": "error", "exc": exc}
            await self._settle_status_projection(request)
            return
        run.status = "finishing"
        if reply == request.awaiting_user_sentinel:
            await self._finish_awaiting(request, run, before)
            return
        finalized = await request.finalize_reply(reply)
        self._schedule_workspace_finalize(request, run, before)
        self._schedule_origin_finalize(request)
        run.outcome = {"kind": "reply", "payload": finalized}

    def _non_streaming_response(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
    ) -> ChatRunDispatchResult:
        outcome = run.outcome or {}
        kind = str(outcome.get("kind") or "")
        if kind == "error":
            exc = outcome.get("exc")
            if not isinstance(exc, Exception):
                exc = RuntimeError("agent run failed")
            message = self.dependencies.error_message(exc, request.lang)
            metadata = self.dependencies.error_metadata(exc)
            return ChatRunDispatchResult(
                payload={
                    "error": message,
                    "detail": message,
                    "code": "model_call_failed",
                    **metadata,
                },
                status_code=502,
            )
        if kind == "awaiting":
            return ChatRunDispatchResult(
                payload={
                    "ok": True,
                    "awaitingUser": True,
                    "pendingQuestion": outcome.get("pending"),
                    "assistantMessages": outcome.get("assistantMessages") or [],
                    "userMessage": self.dependencies.public_message(request.user_entry),
                    "retry": request.retry,
                    "retryReplacedMessageIds": sorted(request.retry_replaced_message_ids),
                }
            )
        finalized = outcome.get("payload")
        if not isinstance(finalized, dict):
            finalized = {}
        return ChatRunDispatchResult(
            payload={
                "ok": True,
                "userMessage": self.dependencies.public_message(request.user_entry),
                "assistantMessage": finalized.get("assistantMessage") or {},
                "assistantMessages": finalized.get("assistantMessages") or [],
                "chatSummary": finalized.get("chatSummary") or {},
                "retry": request.retry,
            }
        )

    async def _dispatch_streaming(
        self,
        request: ChatRunLifecycleRequest,
    ) -> ChatRunDispatchResult:
        ack: dict[str, Any] = {"type": "ack", "chatId": request.chat_id}
        if request.retry:
            ack["retry"] = True
            ack["truncateAfterMessageId"] = request.truncate_after_id
        else:
            ack["userMessage"] = self.dependencies.public_message(request.user_entry)

        async def runner(run: ChatRun) -> None:
            await self._run_streaming(request, run)

        run, is_new = self.dependencies.run_manager.start_or_get(
            request.chat_id,
            ack,
            runner,
            stream=True,
        )
        if is_new:
            await self.dependencies.publish_chat_changed(
                request.chat_id,
                request.project_id,
                "running",
                run_id=run.run_id,
                run_status="running",
                chatSummary=request.running_summary,
                userMessage=self.dependencies.public_message(request.user_entry),
            )
        if request.detached:
            if not is_new:
                return self._already_running(request.lang)
            return ChatRunDispatchResult(
                payload={
                    "run_id": run.run_id,
                    "chat_id": request.chat_id,
                    "status": run.status,
                    "created_at": run.created_at,
                    "event_cursor": 0,
                },
                status_code=202,
            )
        return ChatRunDispatchResult(stream=self.dependencies.run_manager.stream(run))

    async def _run_streaming(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
    ) -> None:
        logger.info("Workbench chat run_streaming entered [chat=%s run=%s]", request.chat_id, run.run_id)
        before = await self.dependencies.capture_workspace_baseline(request.workspace_dir, run.run_id)
        try:
            try:
                reply = await request.run_turn(run)
            except asyncio.CancelledError:
                await self._finalize_workspace(request, run, before, "cancelled")
                await asyncio.to_thread(request.restore_retry_state)
                raise
            except Exception as exc:
                await self._stream_error(request, run, before, exc)
                return
            run.status = "finishing"
            if reply == request.awaiting_user_sentinel:
                await self._finish_awaiting(request, run, before, publish=True)
                return
            await self._finish_stream_reply(request, run, before, reply)
        finally:
            await self._settle_stream(request, run)

    async def _stream_error(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
        before: Any,
        exc: Exception,
    ) -> None:
        logger.exception("Workbench chat streaming run failed for %s", request.chat_id)
        await self._finalize_workspace(request, run, before, "error")
        await asyncio.to_thread(request.restore_retry_state)
        run.outcome = {"kind": "error", "exc": exc}
        await run.publish(
            {
                "type": "error",
                "error": "model_call_failed",
                "message": self.dependencies.error_message(exc, request.lang),
                **self.dependencies.error_metadata(exc),
            }
        )

    async def _finish_awaiting(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
        before: Any,
        *,
        publish: bool = False,
    ) -> None:
        await self._finalize_workspace(request, run, before, "awaiting_user")
        if request.retry:
            await asyncio.to_thread(request.commit_retry)
        pending = await asyncio.to_thread(request.pending_question_for, request.chat_id)
        messages = await asyncio.to_thread(request.stash_pending, pending)
        await asyncio.to_thread(request.notify_attention, pending)
        run.outcome = {
            "kind": "awaiting",
            "pending": pending,
            "assistantMessages": messages,
        }
        if not publish:
            return
        await run.publish(
            {
                "type": "awaiting_user",
                "pending_question": pending,
                "assistantMessages": messages,
                "retry": request.retry,
                "retryReplacedMessageIds": sorted(request.retry_replaced_message_ids),
                "truncateAfterMessageId": request.truncate_after_id,
            }
        )

    async def _finish_stream_reply(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
        before: Any,
        reply: str,
    ) -> None:
        if not run.saw_reply_events and not request.is_external_agent:
            await run.publish({"type": "reply_start"})
            for chunk in request.reply_stream_chunks(reply):
                await run.publish({"type": "reply_delta", "delta": chunk})
        if not request.is_external_agent:
            await run.publish({"type": "reply_done", "response": reply})
        await run.publish({"type": "run_finalizing", "chatId": request.chat_id, "runId": run.run_id})
        finalized = await request.finalize_reply(reply)
        self._schedule_workspace_finalize(request, run, before)
        saved_event = {
            "type": "saved",
            **finalized,
            "retry": request.retry,
            "retryReplacedMessageIds": sorted(request.retry_replaced_message_ids),
            "truncateAfterMessageId": request.truncate_after_id,
        }
        run.outcome = {"kind": "reply", "payload": saved_event}
        await run.publish(saved_event)
        self._schedule_origin_finalize(request)

    async def _settle_stream(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
    ) -> None:
        outcome = run.outcome if isinstance(run.outcome, dict) else {}
        # A persisted reply/awaiting state already wrote ``status=idle``.  A
        # second point mutation here is both redundant and dangerous: if this
        # compatibility repair is delayed by SQLite contention, it can raise
        # after ``saved`` and incorrectly reverse a completed run into a driver
        # failure.  Error/cancel paths still need the repair, but it is a
        # projection update and must never replace the run's real outcome.
        if str(outcome.get("kind") or "") not in {"reply", "awaiting"}:
            await self._settle_status_projection(request)
        await self._publish_settled(request, run)

    async def _settle_status_projection(
        self,
        request: ChatRunLifecycleRequest,
    ) -> None:
        try:
            await asyncio.to_thread(request.settle_status)
        except Exception:
            logger.exception(
                "Failed to repair terminal chat status for %s",
                request.chat_id,
            )

    async def _publish_settled(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
    ) -> None:
        outcome = run.outcome if isinstance(run.outcome, dict) else {}
        kind = str(outcome.get("kind") or "")
        payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
        run_status = {
            "reply": "completed",
            "awaiting": "awaiting_user",
            "error": "failed",
        }.get(kind, "cancelled" if run.termination_reason else "idle")
        details: dict[str, Any] = {
            "run_id": run.run_id,
            "run_status": run_status,
        }
        summary = payload.get("chatSummary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict) or not summary:
            summary = await asyncio.to_thread(
                self.dependencies.load_chat_summary,
                request.chat_id,
            )
        if isinstance(summary, dict) and summary:
            details["chatSummary"] = summary
        messages = (
            payload.get("assistantMessages")
            if kind == "reply"
            else outcome.get("assistantMessages")
        )
        if isinstance(messages, list) and messages:
            details["assistantMessages"] = messages
        await self.dependencies.publish_chat_changed(
            request.chat_id,
            request.project_id,
            "settled",
            **details,
        )

    async def _finalize_workspace(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
        before: Any,
        status: str,
    ) -> None:
        await self.dependencies.finalize_workspace_changes(
            chat_id=request.chat_id,
            run_id=run.run_id,
            workspace_dir=request.workspace_dir,
            before=before,
            status=status,
            run=run,
        )

    def _schedule_workspace_finalize(
        self,
        request: ChatRunLifecycleRequest,
        run: ChatRun,
        before: Any,
    ) -> None:
        self.dependencies.schedule_workspace_finalize(
            chat_id=request.chat_id,
            run_id=run.run_id,
            workspace_dir=request.workspace_dir,
            before=before,
            status="completed",
        )

    @staticmethod
    def _schedule_origin_finalize(request: ChatRunLifecycleRequest) -> None:
        from cyrene.runtime.host_actions import finalize_origin

        asyncio.create_task(
            finalize_origin(request.chat_id, "", origin_run_id=request.client_request_id)
        )

    @staticmethod
    def _already_running(language: str = "") -> ChatRunDispatchResult:
        return ChatRunDispatchResult(
            payload={
                "error": localized(
                    "This chat already has a reply in progress.",
                    "此对话已有正在生成的回复。",
                    language=language,
                ),
                "code": "chat_run_in_progress",
            },
            status_code=409,
        )


__all__ = [
    "ChatRunDispatchResult",
    "ChatRunLifecycleApplicationService",
    "ChatRunLifecycleDependencies",
    "ChatRunLifecycleRequest",
]
