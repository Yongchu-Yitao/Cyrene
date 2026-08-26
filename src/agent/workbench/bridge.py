"""Plain-Chat adapter between :mod:`agent` and Workbench ``ChatRun``."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from ..plugin import PluginRegistry
from ..session import AgentSession, AgentSessionEvent, DEFAULT_SYSTEM_PROMPT

WorkbenchPublisher: TypeAlias = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class AgentSessionRunError(RuntimeError):
    """The Agent reached a durable failed terminal node."""


class AgentSessionCancelledError(RuntimeError):
    """The Agent reached a durable cancelled terminal node."""


@dataclass(frozen=True, slots=True)
class WorkbenchChatResult:
    run_id: str
    status: str
    text: str
    node_id: str
    snapshot: Mapping[str, Any]


def _envelope(
    event: AgentSessionEvent,
    event_type: str,
    event_id: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "eventId": event_id,
        "runId": event.run_id,
        "type": event_type,
        "timestamp": event.time.isoformat(),
        "payload": dict(payload or {}),
    }


def workbench_events(event: AgentSessionEvent) -> tuple[dict[str, Any], ...]:
    """Project one Agent observation into the versioned Workbench Chat protocol."""

    data = dict(event.data)
    event_id = f"agent:{event.tree_id}:{event.node_id or event.sequence}:{event.type}"
    if event.type == "input.accepted":
        return (_envelope(event, "run.started", event_id, {"status": "running"}),)
    if event.type == "session.state":
        return (
            _envelope(
                event,
                "session.updated",
                event_id,
                {
                    "sessionId": event.tree_id,
                    "updateKind": "run_state",
                    "update": {
                        "status": str(data.get("status") or ""),
                        "detail": str(data.get("detail") or ""),
                        "leafId": str(data.get("leaf_id") or ""),
                    },
                },
            ),
        )
    if event.type == "assistant.tool_calls":
        projected = []
        for call in data.get("tool_calls") or ():
            if not isinstance(call, Mapping):
                continue
            call_id = str(call.get("id") or "")
            projected.append(
                _envelope(
                    event,
                    "tool.started",
                    f"agent:{event.tree_id}:{event.run_id}:tool:{call_id}:started",
                    {
                        "toolCallId": call_id,
                        "name": str(call.get("name") or ""),
                        "status": "running",
                        "args": dict(call.get("arguments") or {}),
                    },
                )
            )
        return tuple(projected)
    if event.type == "tool.completed":
        call_id = str(data.get("call_id") or "")
        success = bool(data.get("success"))
        return (
            _envelope(
                event,
                "tool.completed",
                f"agent:{event.tree_id}:{event.run_id}:tool:{call_id}:completed",
                {
                    "toolCallId": call_id,
                    "name": str(data.get("name") or ""),
                    "status": "completed" if success else "failed",
                    "failed": not success,
                    "outputSummary": data.get("value"),
                    "error": str(data.get("error") or ""),
                },
            ),
        )
    if event.type == "tools.completed":
        projected = []
        for result in data.get("results") or ():
            if not isinstance(result, Mapping):
                continue
            call_id = str(result.get("call_id") or "")
            success = bool(result.get("success"))
            projected.append(
                _envelope(
                    event,
                    "tool.completed",
                    f"agent:{event.tree_id}:{event.run_id}:tool:{call_id}:completed",
                    {
                        "toolCallId": call_id,
                        "name": str(result.get("name") or ""),
                        "status": "completed" if success else "failed",
                        "failed": not success,
                        "outputSummary": result.get("value"),
                        "error": str(result.get("error") or ""),
                    },
                )
            )
        return tuple(projected)
    if event.type == "assistant.completed":
        content = str(data.get("content") or "")
        return (
            {
                **_envelope(event, "reply_start", event_id + ":started"),
                "type": "reply_start",
            },
            {
                **_envelope(event, "reply_delta", event_id + ":delta"),
                "type": "reply_delta",
                "delta": content,
            },
            {
                **_envelope(event, "reply_done", event_id + ":completed"),
                "type": "reply_done",
                "response": content,
            },
        )
    if event.type == "run.failed":
        message = str(data.get("content") or data.get("error") or "Agent run failed")
        return (
            _envelope(
                event,
                "run.failed",
                event_id,
                {"failureKind": "agent_run_failed", "message": message},
            ),
        )
    if event.type == "run.cancelled":
        return (
            _envelope(
                event,
                "run.cancelled",
                event_id,
                {"reason": str(data.get("cancel_reason") or "user_cancelled")},
            ),
        )
    return ()


class _PublisherBinding:
    def __init__(
        self,
        session: AgentSession,
        publish: WorkbenchPublisher,
        *,
        run_id: str,
        replay: bool,
    ) -> None:
        self._publish = publish
        self._run_id = str(run_id)
        self._loop = asyncio.get_running_loop()
        self._lock = threading.RLock()
        self._futures: list[Future[Any]] = []
        self._event_ids: set[str] = set()
        self._unsubscribe = session.subscribe(self._receive)
        if replay:
            for event in session.events():
                self._receive(event)

    async def _send(self, payload: dict[str, Any]) -> None:
        result = self._publish(payload)
        if inspect.isawaitable(result):
            await result

    def _receive(self, event: AgentSessionEvent) -> None:
        if event.run_id != self._run_id:
            return
        for payload in workbench_events(event):
            event_id = str(payload.get("eventId") or "")
            with self._lock:
                if event_id and event_id in self._event_ids:
                    continue
                if event_id:
                    self._event_ids.add(event_id)
                self._futures.append(
                    asyncio.run_coroutine_threadsafe(self._send(payload), self._loop)
                )

    async def close(self) -> None:
        self._unsubscribe()
        while True:
            with self._lock:
                pending = self._futures
                self._futures = []
            if not pending:
                return
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in pending),
            )


class WorkbenchSessionBridge:
    """Drive one durable Agent tree from the ordinary Workbench Chat lifecycle."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session

    @classmethod
    def open(
        cls,
        data_directory: str | Path,
        workspace: str | Path,
        plugin_directory: str | Path,
        *,
        registry: PluginRegistry,
        model_plugin: str,
        chat_id: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        host_context: Mapping[str, Any] | None = None,
        plugin_context_data: Mapping[str, Any] | None = None,
        max_model_calls: int = 12,
    ) -> WorkbenchSessionBridge:
        return cls(
            AgentSession(
                data_directory,
                workspace,
                plugin_directory,
                registry=registry,
                model_plugin=model_plugin,
                tree_id=str(chat_id),
                system_prompt=system_prompt,
                host_context=host_context,
                plugin_context_data=plugin_context_data,
                max_model_calls=max_model_calls,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return self.session.snapshot()

    async def _result(
        self,
        run_id: str,
        *,
        publish: WorkbenchPublisher | None,
        replay: bool,
    ) -> WorkbenchChatResult:
        binding = (
            _PublisherBinding(self.session, publish, run_id=run_id, replay=replay)
            if publish is not None
            else None
        )
        try:
            await self.session.drain()
        except asyncio.CancelledError:
            try:
                await asyncio.shield(
                    self.session.cancel("workbench_run_cancelled", timeout=5.0)
                )
            except (TimeoutError, asyncio.CancelledError):
                self.session.request_cancel("workbench_run_cancelled")
            raise
        finally:
            if binding is not None:
                await asyncio.shield(binding.close())

        output = self.session.final_output(run_id)
        if output is None:
            raise AgentSessionRunError("Agent run finished without a terminal response")
        if output.get("cancelled") is True:
            raise AgentSessionCancelledError(
                str(output.get("cancel_reason") or "Agent run was cancelled")
            )
        if output.get("error") is True:
            raise AgentSessionRunError(str(output.get("content") or "Agent run failed"))
        return WorkbenchChatResult(
            run_id=run_id,
            status="completed",
            text=str(output.get("content") or ""),
            node_id=str(output.get("node_id") or ""),
            snapshot=self.session.snapshot(),
        )

    async def submit_result(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        publish: WorkbenchPublisher | None = None,
    ) -> WorkbenchChatResult:
        normalized_run_id = str(run_id or f"run_{uuid4().hex}")
        binding = (
            _PublisherBinding(
                self.session,
                publish,
                run_id=normalized_run_id,
                replay=False,
            )
            if publish is not None
            else None
        )
        try:
            self.session.submit(
                text,
                run_id=normalized_run_id,
                metadata=metadata,
            )
            await self.session.drain()
        except asyncio.CancelledError:
            try:
                await asyncio.shield(
                    self.session.cancel("workbench_run_cancelled", timeout=5.0)
                )
            except (TimeoutError, asyncio.CancelledError):
                self.session.request_cancel("workbench_run_cancelled")
            raise
        finally:
            if binding is not None:
                await asyncio.shield(binding.close())

        output = self.session.final_output(normalized_run_id)
        if output is None:
            raise AgentSessionRunError("Agent run finished without a terminal response")
        if output.get("cancelled") is True:
            raise AgentSessionCancelledError(
                str(output.get("cancel_reason") or "Agent run was cancelled")
            )
        if output.get("error") is True:
            raise AgentSessionRunError(str(output.get("content") or "Agent run failed"))
        return WorkbenchChatResult(
            run_id=normalized_run_id,
            status="completed",
            text=str(output.get("content") or ""),
            node_id=str(output.get("node_id") or ""),
            snapshot=self.session.snapshot(),
        )

    async def submit(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        publish: WorkbenchPublisher | None = None,
    ) -> str:
        result = await self.submit_result(
            text,
            run_id=run_id,
            metadata=metadata,
            publish=publish,
        )
        return result.text

    async def resume_result(
        self,
        *,
        publish: WorkbenchPublisher | None = None,
    ) -> WorkbenchChatResult:
        run_id = self.session.current_run_id
        if not run_id:
            raise AgentSessionRunError("Agent session has no run to resume")
        return await self._result(run_id, publish=publish, replay=True)

    async def resume(
        self,
        *,
        publish: WorkbenchPublisher | None = None,
    ) -> str:
        return (await self.resume_result(publish=publish)).text

    async def cancel(self, reason: str = "user_cancelled") -> bool:
        return await self.session.cancel(reason)

    def close(self) -> None:
        self.session.close()


__all__ = [
    "AgentSessionCancelledError",
    "AgentSessionRunError",
    "WorkbenchChatResult",
    "WorkbenchPublisher",
    "WorkbenchSessionBridge",
    "workbench_events",
]
