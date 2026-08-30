"""External Agent turn execution and durable chat projection.

The HTTP adapter owns neither Agent-runtime event semantics nor the mutable
projection accumulated for final persistence.  Keeping both here makes the
event order explicit and gives the chat send application flow one dependency
with a small, stable result.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from cyrene.agents.events import CORE_EVENT_TYPES, event_envelope
from cyrene.agents.notices import LeadingOperationalNoticeFilter
from cyrene.workbench.chat.chat_runs import ChatRun


@dataclass(slots=True)
class ExternalTurnProjection:
    usage: dict[str, int] = field(default_factory=dict)
    latest_request_usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    model_identity: dict[str, Any] = field(default_factory=dict)
    generation_duration_ms: float | None = None
    output_tokens_per_second: float | None = None
    activity_messages: list[dict[str, Any]] = field(default_factory=list)
    context_report: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    commands: list[Any] | None = None
    plan: dict[str, Any] | None = None
    agent_mode: Any = None
    config_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ExternalTurnDependencies:
    run_turn: Callable[..., Awaitable[dict[str, Any]]]
    set_session_id: Callable[[str, str], Any]
    update_context_report: Callable[[str, dict[str, Any]], Any]
    utc_now_iso: Callable[[], str]


class ExternalAgentTurnApplicationService:
    """Run one external Agent turn while projecting its ordered event stream."""

    def __init__(self, dependencies: ExternalTurnDependencies) -> None:
        self.dependencies = dependencies

    async def run(
        self,
        *,
        run: ChatRun,
        chat_id: str,
        chat: dict[str, Any],
        message: str,
        attachments: list[dict[str, Any]],
        workspace_path: str,
        projection: ExternalTurnProjection,
    ) -> str:
        projector = _ExternalEventProjector(
            run=run,
            chat_id=chat_id,
            projection=projection,
            utc_now_iso=self.dependencies.utc_now_iso,
        )
        result = await self.dependencies.run_turn(
            chat=chat,
            message=message,
            publish=projector.publish,
            attachments=attachments,
            workspace_path=workspace_path,
            run_id=run.run_id,
        )
        if projection.usage:
            from cyrene.workbench.application.usage_events import publish_usage_event

            await publish_usage_event(
                {
                    "type": "llm_call",
                    "timestamp": self.dependencies.utc_now_iso(),
                    "status": "completed",
                    "caller": "external_agent",
                    "phase": "agent",
                    "model": "external_agent/" + str(result.get("agentId") or "unknown"),
                    "session_id": chat_id,
                    "round_id": run.run_id,
                    "duration_ms": 0,
                    "usage": dict(projection.usage),
                },
                session_id=chat_id,
            )
        session_id = str(result.get("sessionId") or projector.session_id or "")
        if session_id:
            await asyncio.to_thread(self.dependencies.set_session_id, chat_id, session_id)
        if projection.context_report:
            await asyncio.to_thread(
                self.dependencies.update_context_report,
                chat_id,
                projection.context_report,
            )
        return projector.completed_reply or "".join(projector.reply_parts)


class _ExternalEventProjector:
    def __init__(
        self,
        *,
        run: ChatRun,
        chat_id: str,
        projection: ExternalTurnProjection,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.run = run
        self.chat_id = chat_id
        self.projection = projection
        self.utc_now_iso = utc_now_iso
        self.notice_filter = LeadingOperationalNoticeFilter()
        self.notification_keys: set[str] = set()
        self.reply_parts: list[str] = []
        self.completed_reply = ""
        self.session_id = ""

    async def publish(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("type") or "")
        if event_type == "message.delta":
            event = await self._message_delta(event, payload)
            if event is None:
                return
        elif event_type == "message.completed":
            event = await self._message_completed(event, payload)
        elif event_type in {"run.completed", "run.failed", "run.cancelled"}:
            await self._run_terminal(event)
        elif event_type == "notification.created":
            self._notification(event, payload)
        elif event_type in {"reasoning.delta", "reasoning.completed"}:
            self._reasoning(event_type, payload)
        elif event_type in {"tool.started", "tool.updated", "tool.completed"}:
            self._tool(event_type, event, payload)
        elif event_type == "usage.updated":
            self._usage(payload)
        elif event_type == "session.updated":
            self._session(payload)
        elif event_type in {"artifact.created", "artifact.updated"}:
            self._artifact(payload)
        elif event_type and event_type not in CORE_EVENT_TYPES:
            self._extension_event(event_type, event, payload)
        await self.run.publish(event)

    async def _message_delta(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        delta = str(payload.get("delta") or payload.get("text") or "")
        notices, visible_delta = self.notice_filter.feed(delta)
        for notice in notices:
            await self._publish_notice(notice, event)
        if not visible_delta:
            return None
        self.reply_parts.append(visible_delta)
        if visible_delta == delta:
            return event
        next_payload = {**payload, "delta": visible_delta}
        if "text" in next_payload:
            next_payload["text"] = visible_delta
        return {**event, "payload": next_payload}

    async def _message_completed(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_reply = str(
            payload.get("response") or payload.get("text") or payload.get("content") or ""
        )
        if raw_reply:
            notices, self.completed_reply = self.notice_filter.complete(raw_reply)
        else:
            notices, visible_tail = self.notice_filter.finish()
            if visible_tail:
                self.reply_parts.append(visible_tail)
                await self.run.publish(self._delta_event(event, visible_tail))
        for notice in notices:
            await self._publish_notice(notice, event)
        if not raw_reply or self.completed_reply == raw_reply:
            return event
        next_payload = dict(payload)
        for key in ("response", "text", "content"):
            if key in next_payload:
                next_payload[key] = self.completed_reply
        return {**event, "payload": next_payload}

    async def _run_terminal(self, event: dict[str, Any]) -> None:
        notices, visible_tail = self.notice_filter.finish()
        for notice in notices:
            await self._publish_notice(notice, event)
        if visible_tail:
            await self.publish(self._delta_event(event, visible_tail))

    async def _publish_notice(
        self,
        notice: dict[str, Any],
        source_event: dict[str, Any],
    ) -> None:
        key = "\n".join(
            (
                str(notice.get("category") or "transport_warning"),
                str(notice.get("message") or "").strip(),
            )
        )
        if not key.strip() or key in self.notification_keys:
            return
        await self.publish(
            event_envelope(
                type="notification.created",
                payload=notice,
                timestamp=str(source_event.get("timestamp") or ""),
                agent_id=str(source_event.get("agentId") or ""),
                installation_id=str(source_event.get("installationId") or ""),
                chat_id=str(source_event.get("chatId") or self.chat_id),
                run_id=str(source_event.get("runId") or self.run.run_id),
                session_id=str(source_event.get("sessionId") or ""),
                actor_id=str(source_event.get("actorId") or "primary"),
                parent_run_id=source_event.get("parentRunId"),
                extensions={
                    "originEventId": str(source_event.get("eventId") or ""),
                    "normalizedFrom": "message_text",
                },
            )
        )

    def _delta_event(self, event: dict[str, Any], delta: str) -> dict[str, Any]:
        return event_envelope(
            type="message.delta",
            payload={"delta": delta},
            timestamp=str(event.get("timestamp") or ""),
            agent_id=str(event.get("agentId") or ""),
            installation_id=str(event.get("installationId") or ""),
            chat_id=str(event.get("chatId") or self.chat_id),
            run_id=str(event.get("runId") or self.run.run_id),
            session_id=str(event.get("sessionId") or ""),
            actor_id=str(event.get("actorId") or "primary"),
            parent_run_id=event.get("parentRunId"),
        )

    def _notification(self, event: dict[str, Any], payload: dict[str, Any]) -> None:
        message = str(payload.get("message") or payload.get("detail") or "").strip()
        category = str(payload.get("category") or "transport_warning")
        key = "\n".join((category, message))
        if not message or key in self.notification_keys:
            return
        self.notification_keys.add(key)
        self.projection.notifications.append(
            {
                "eventId": str(event.get("eventId") or ""),
                "createdAt": str(event.get("timestamp") or self.utc_now_iso()),
                "severity": str(payload.get("severity") or "warning"),
                "category": category,
                "message": message,
                "source": str(payload.get("source") or "agent_runtime"),
                "terminal": bool(payload.get("terminal")),
            }
        )

    def _reasoning(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "reasoning.delta":
            text = str(payload.get("delta") or payload.get("text") or "")
            if text:
                self.projection.reasoning_parts.append(text)
            return
        text = str(
            payload.get("response") or payload.get("text") or payload.get("content") or ""
        )
        if text:
            self.projection.reasoning_parts[:] = [text]

    def _tool(
        self,
        event_type: str,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        call_id = str(payload.get("toolCallId") or payload.get("tool_call_id") or "")
        status = str(
            payload.get("status") or ("completed" if event_type == "tool.completed" else "running")
        ).strip().lower()
        entry = self._tool_entry(call_id, status, payload)
        matching = [
            index
            for index, item in enumerate(self.projection.trace)
            if call_id and str(item.get("toolCallId") or "") == call_id
        ]
        terminal = {"completed", "failed", "error", "failure", "expired", "cancelled"}
        open_indices = [
            index
            for index in matching
            if str(self.projection.trace[index].get("status") or "").strip().lower() not in terminal
        ]
        existing = open_indices[-1] if open_indices else (matching[-1] if matching and status in terminal else -1)
        if existing >= 0:
            preview = self.projection.trace[existing].get("preview")
            if preview not in (None, "") and event_type == "tool.completed":
                entry["preview"] = preview
            self.projection.trace[existing] = {**self.projection.trace[existing], **entry}
        else:
            entry["reasoningOffset"] = len("".join(self.projection.reasoning_parts))
            entry["startedAt"] = str(event.get("timestamp") or self.utc_now_iso())
            self.projection.trace.append(entry)
        if len(self.projection.trace) > 40:
            del self.projection.trace[:-40]

    @staticmethod
    def _tool_entry(
        call_id: str,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kind": "tool",
            "toolCallId": call_id,
            "tool": str(payload.get("name") or payload.get("tool") or payload.get("title") or "tool"),
            "status": status,
            "failed": bool(payload.get("failed"))
            or status in {"failed", "error", "failure", "expired", "cancelled"},
        }
        if payload.get("inputSummary") is not None:
            entry["input"] = payload.get("inputSummary")
        if payload.get("outputSummary") is not None:
            entry["output"] = payload.get("outputSummary")
        summary = payload.get("inputSummary")
        if summary is None:
            summary = payload.get("outputSummary")
        if isinstance(summary, (str, int, float, bool)):
            entry["preview"] = str(summary)
        elif summary is not None:
            try:
                entry["preview"] = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))[:600]
            except (TypeError, ValueError):
                entry["preview"] = str(summary)[:600]
        if isinstance(payload.get("presentation"), dict):
            entry["presentation"] = payload.get("presentation")
        return entry

    def _usage(self, payload: dict[str, Any]) -> None:
        for source, target in (
            ("inputTokens", "prompt_tokens"),
            ("outputTokens", "completion_tokens"),
            ("totalTokens", "total_tokens"),
            ("used", "total_tokens"),
        ):
            try:
                value = int(payload.get(source) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                self.projection.usage[target] = value
        context = next(
            (
                payload.get(key)
                for key in ("contextComposition", "context", "contextWindow")
                if isinstance(payload.get(key), dict)
            ),
            {},
        )
        self.projection.context_report.update(context)
        if isinstance(payload.get("segments"), list):
            self.projection.context_report["segments"] = payload.get("segments")
        for key in ("used", "size"):
            if payload.get(key) is not None:
                self.projection.context_report[key] = payload.get(key)

    def _session(self, payload: dict[str, Any]) -> None:
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip()
        if session_id:
            self.session_id = session_id
        if isinstance(payload.get("commands"), list):
            self.projection.commands = payload["commands"][:200]
        if payload.get("mode") is not None:
            self.projection.agent_mode = payload.get("mode")
        if isinstance(payload.get("plan"), dict):
            self.projection.plan = dict(payload["plan"])
            self.projection.plan.setdefault("status", "active")
        options = [payload.get("configOption"), *(payload.get("configOptions") or [])]
        for option in options:
            if isinstance(option, dict) and str(option.get("id") or ""):
                self.projection.config_options[str(option["id"])] = option

    def _artifact(self, payload: dict[str, Any]) -> None:
        attachment = payload.get("attachment")
        if not isinstance(attachment, dict):
            return
        public = {
            key: attachment[key]
            for key in ("id", "name", "content_type", "size", "kind", "url", "width", "height")
            if key in attachment
        }
        artifact_id = str(payload.get("artifactId") or "")
        if artifact_id:
            public["artifactId"] = artifact_id
        key = str(public.get("artifactId") or public.get("id") or public.get("url") or "")
        if not key:
            return
        index = next(
            (
                index
                for index, item in enumerate(self.projection.artifacts)
                if str(item.get("artifactId") or item.get("id") or item.get("url") or "") == key
            ),
            -1,
        )
        if index >= 0:
            self.projection.artifacts[index] = public
        else:
            self.projection.artifacts.append(public)

    def _extension_event(
        self,
        event_type: str,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self.projection.trace.append(
            {
                "kind": "event",
                "toolCallId": str(event.get("eventId") or event.get("event_id") or ""),
                "tool": f"Agent event · {event_type}",
                "status": "completed",
                "reasoningOffset": len("".join(self.projection.reasoning_parts)),
                "startedAt": str(event.get("timestamp") or self.utc_now_iso()),
                "output": payload,
                "presentation": {"kind": "event"},
            }
        )


__all__ = [
    "ExternalAgentTurnApplicationService",
    "ExternalTurnDependencies",
    "ExternalTurnProjection",
]
