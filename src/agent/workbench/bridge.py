"""Plain-Chat adapter between :mod:`agent` and Workbench ``ChatRun``."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from ..plugin import PluginRegistry
from ..session import AgentSession, AgentSessionEvent

WorkbenchPublisher: TypeAlias = Callable[[dict[str, Any]], Any | Awaitable[Any]]

logger = logging.getLogger(__name__)


class AgentSessionRunError(RuntimeError):
    """The Agent reached a durable failed terminal node."""


class AgentSessionCancelledError(RuntimeError):
    """The Agent reached a durable cancelled terminal node."""


@dataclass(frozen=True, slots=True)
class WorkbenchPendingQuestion:
    id: str
    text: str
    options: tuple[str, ...]
    allow_custom: bool
    kind: str
    round_id: str
    client_request_id: str
    asked_at: str
    tool_name: str
    plan: Any = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WorkbenchPendingQuestion:
        options: list[str] = []
        for item in raw.get("options") if isinstance(raw.get("options"), list) else ():
            if isinstance(item, Mapping):
                label = str(item.get("label") or item.get("text") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                options.append(label)
        return cls(
            id=str(raw.get("id") or raw.get("question_id") or ""),
            text=str(raw.get("text") or ""),
            options=tuple(options[:6]),
            allow_custom=bool(raw.get("allow_custom", True)),
            kind=str(raw.get("kind") or "clarification"),
            round_id=str(raw.get("round_id") or ""),
            client_request_id=str(raw.get("client_request_id") or ""),
            asked_at=str(raw.get("asked_at") or ""),
            tool_name=str(raw.get("tool_name") or ""),
            plan=raw.get("plan"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "options": list(self.options),
            "allowCustom": self.allow_custom,
            "kind": self.kind,
            "roundId": self.round_id,
            "clientRequestId": self.client_request_id,
            "askedAt": self.asked_at,
        }


@dataclass(frozen=True, slots=True)
class WorkbenchChatResult:
    run_id: str
    status: str
    text: str
    node_id: str
    usage: Mapping[str, int]
    latest_request_usage: Mapping[str, int]
    model: str
    model_identity: Mapping[str, Any]
    generation_duration_ms: float | None
    output_tokens_per_second: float | None
    activity_messages: tuple[Mapping[str, Any], ...]
    snapshot: Mapping[str, Any]
    pending_question: WorkbenchPendingQuestion | None = None
    active_plan: Any = None


_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


def _usage_integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalized_usage(raw: Any) -> dict[str, int]:
    usage = dict(raw) if isinstance(raw, Mapping) else {}
    prompt = _usage_integer(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or usage.get("promptTokens")
        or usage.get("inputTokens")
    )
    completion = _usage_integer(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("completionTokens")
        or usage.get("outputTokens")
    )
    total = _usage_integer(
        usage.get("total_tokens")
        or usage.get("totalTokens")
        or prompt + completion
    )
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    hit_value = next(
        (
            value
            for value in (
                usage.get("prompt_cache_hit_tokens"),
                usage.get("cache_hit_tokens"),
                usage.get("cached_tokens"),
                usage.get("cached_input_tokens"),
                usage.get("cache_read_input_tokens"),
                details.get("cached_tokens"),
            )
            if value is not None
        ),
        None,
    )
    miss_value = next(
        (
            value
            for value in (
                usage.get("prompt_cache_miss_tokens"),
                usage.get("cache_miss_tokens"),
                usage.get("cache_creation_input_tokens"),
            )
            if value is not None
        ),
        None,
    )
    cache_hit = _usage_integer(hit_value) if hit_value is not None else 0
    cache_miss = (
        _usage_integer(miss_value)
        if miss_value is not None
        else max(0, prompt - cache_hit)
        if hit_value is not None
        else 0
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
    }


_TurnMetrics = tuple[
    dict[str, int], dict[str, int], str, dict[str, Any], float | None, float | None
]


def _turn_metrics(
    snapshot: Mapping[str, Any],
    run_id: str,
    final_node_id: str,
) -> _TurnMetrics:
    """Project one Agent run's model usage without counting ContextTree nodes twice."""
    raw_nodes = snapshot.get("nodes")
    nodes = (
        [item for item in raw_nodes if isinstance(item, Mapping)]
        if isinstance(raw_nodes, list)
        else []
    )
    by_id = {
        str(item.get("id") or ""): item
        for item in nodes
        if str(item.get("id") or "")
    }
    totals = {key: 0 for key in _USAGE_KEYS}
    latest_request_usage = {key: 0 for key in _USAGE_KEYS}
    model = ""
    model_identity: dict[str, Any] = {}
    model_calls: list[tuple[str, float]] = []

    def add_usage(raw: Any) -> None:
        normalized = _normalized_usage(raw)
        for key in _USAGE_KEYS:
            totals[key] += normalized[key]

    def remember_call(raw: Mapping[str, Any]) -> None:
        try:
            latency_ms = float(raw.get("model_latency_ms") or 0.0)
        except (TypeError, ValueError, OverflowError):
            latency_ms = 0.0
        if math.isfinite(latency_ms) and latency_ms > 0:
            model_calls.append(
                (str(raw.get("model_observation_id") or ""), latency_ms)
            )

    for item in nodes:
        value = item.get("value")
        if not isinstance(value, Mapping):
            continue
        if value.get("role") != "assistant" or str(value.get("run_id") or "") != run_id:
            continue
        if value.get("cancelled") is True or value.get("error") is True:
            continue
        add_usage(value.get("usage"))
        remember_call(value)
        auxiliary = value.get("auxiliary_usage")
        for entry in auxiliary if isinstance(auxiliary, list) else ():
            if isinstance(entry, Mapping):
                add_usage(entry.get("usage"))
                remember_call(entry)
        if str(item.get("id") or "") == final_node_id:
            latest_request_usage = _normalized_usage(value.get("usage"))
            model = str(value.get("model") or "")
            identity = value.get("model_identity")
            if isinstance(identity, Mapping):
                model_identity = dict(identity)

    def ancestor_run_id(item: Mapping[str, Any]) -> str:
        current: Mapping[str, Any] | None = item
        visited: set[str] = set()
        while current is not None:
            value = current.get("value")
            if isinstance(value, Mapping) and str(value.get("run_id") or ""):
                return str(value.get("run_id") or "")
            parent_id = str(current.get("parent_id") or "")
            if not parent_id or parent_id in visited:
                return ""
            visited.add(parent_id)
            current = by_id.get(parent_id)
        return ""

    generation_duration_ms = 0.0
    observation_ids: set[str] = set()
    for item in nodes:
        value = item.get("value")
        if not isinstance(value, Mapping) or value.get("role") != "model_observation":
            continue
        if ancestor_run_id(item) != run_id:
            continue
        call_kind = str(value.get("call_kind") or "model")
        if call_kind not in {"agent", "permission"}:
            continue
        try:
            latency_ms = float(value.get("latency_ms") or 0.0)
        except (TypeError, ValueError, OverflowError):
            latency_ms = 0.0
        if math.isfinite(latency_ms) and latency_ms > 0:
            generation_duration_ms += latency_ms
            observation_ids.add(str(item.get("id") or ""))
    generation_duration_ms += sum(
        latency_ms
        for observation_id, latency_ms in model_calls
        if not observation_id or observation_id not in observation_ids
    )
    duration = generation_duration_ms if generation_duration_ms > 0 else None
    rate = None
    if duration is not None and totals["completion_tokens"] > 0:
        rate = totals["completion_tokens"] * 1000.0 / duration
    return totals, latest_request_usage, model, model_identity, duration, rate


def _turn_plan(snapshot: Mapping[str, Any], run_id: str) -> Any:
    candidates: list[tuple[str, Any]] = []
    raw_nodes = snapshot.get("nodes")
    for item in raw_nodes if isinstance(raw_nodes, list) else ():
        if not isinstance(item, Mapping):
            continue
        value = item.get("value")
        if (
            not isinstance(value, Mapping)
            or value.get("role") != "tool_results"
            or str(value.get("run_id") or "") != str(run_id)
        ):
            continue
        pending = value.get("pending_question")
        if not isinstance(pending, Mapping) or not isinstance(pending.get("plan"), Mapping):
            continue
        plan = dict(pending["plan"])
        if str(pending.get("status") or "") == "answered":
            answer = str(pending.get("answer") or "").strip().lower()
            rejected = answer in {"拒绝", "不同意", "reject", "no", "cancel", "取消"}
            plan["status"] = "rejected" if rejected else "active"
        candidates.append((str(item.get("updated_at") or item.get("created_at") or ""), plan))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


_TRACE_SKIP_TOOLS = {
    "use_tools",
    "send_message",
    "update_plan_progress",
}


def _trace_preview(value: Any, *, limit: int = 400) -> str:
    """Return a bounded, display-only summary for one durable tool entry."""

    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("result", "message", "error", "output"):
            preferred = value.get(key)
            if isinstance(preferred, (str, int, float, bool)) and str(preferred):
                return str(preferred)[:limit]
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _trace_display_name(name: str, arguments: Any) -> str:
    """Expose the invoked Plugin name instead of the generic toolbox wrapper."""

    if name != "toolbox" or not isinstance(arguments, Mapping):
        return name
    operation = str(arguments.get("operation") or "").strip()
    target = str(arguments.get("name") or "").strip()
    if operation == "invoke" and target:
        return target
    return ".".join(part for part in ("toolbox", operation) if part) or name


def project_tool_activity_messages(
    snapshot: Mapping[str, Any],
    run_id: str = "",
) -> tuple[dict[str, Any], ...]:
    """Project durable ContextTree tool nodes into Workbench activity cards.

    The ContextTree is the source of truth after the Agent-kernel rewrite.  A
    completed chat must therefore be reconstructible from these nodes without
    depending on the browser's temporary SSE state or the removed legacy Agent
    session file.
    """

    raw_nodes = snapshot.get("nodes")
    nodes = (
        [item for item in raw_nodes if isinstance(item, Mapping)]
        if isinstance(raw_nodes, list)
        else []
    )
    requested_run_id = str(run_id or "")
    terminal_run_ids = {
        str(value.get("run_id") or "")
        for item in nodes
        for value in [item.get("value")]
        if isinstance(value, Mapping)
        and value.get("role") == "assistant"
        and str(value.get("run_id") or "")
        and (
            value.get("session_end_complete") is True
            or value.get("error") is True
            or value.get("cancelled") is True
        )
    }
    results_by_call_id: dict[str, Mapping[str, Any]] = {}
    for item in nodes:
        value = item.get("value")
        if not isinstance(value, Mapping) or value.get("role") != "tool_results":
            continue
        node_run_id = str(value.get("run_id") or "")
        if requested_run_id and node_run_id != requested_run_id:
            continue
        if not requested_run_id and node_run_id not in terminal_run_ids:
            continue
        results = value.get("results")
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, Mapping):
                continue
            call_id = str(result.get("call_id") or "")
            if call_id:
                results_by_call_id[call_id] = result

    activities: list[dict[str, Any]] = []
    for item in nodes:
        value = item.get("value")
        if not isinstance(value, Mapping) or value.get("role") != "assistant":
            continue
        node_run_id = str(value.get("run_id") or "")
        if requested_run_id and node_run_id != requested_run_id:
            continue
        if not requested_run_id and node_run_id not in terminal_run_ids:
            continue
        calls = value.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            continue
        trace: list[dict[str, Any]] = []
        for index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                continue
            raw_name = str(call.get("name") or "").strip()
            if not raw_name or raw_name in _TRACE_SKIP_TOOLS:
                continue
            arguments = call.get("arguments")
            call_id = str(call.get("id") or "").strip()
            if not call_id:
                call_id = f"{str(item.get('id') or 'assistant')}:{index}"
            result = results_by_call_id.get(call_id)
            success = bool(result.get("success")) if result is not None else True
            preview_source: Any = arguments
            if result is not None:
                preview_source = (
                    result.get("value")
                    if success
                    else result.get("error") or result.get("value")
                )
            entry: dict[str, Any] = {
                "kind": "tool",
                "toolCallId": call_id,
                "text": _trace_display_name(raw_name, arguments),
                "tool": raw_name,
                "status": "completed" if success else "failed",
                "failed": not success,
            }
            preview = _trace_preview(preview_source)
            if preview:
                entry["preview"] = preview
            if result is None:
                entry["inferredCompletion"] = True
            trace.append(entry)
        reasoning = str(
            value.get("reasoning") or value.get("reasoning_content") or ""
        )
        if not trace and not reasoning.strip():
            continue
        node_id = str(item.get("id") or "")
        activity: dict[str, Any] = {
            "id": f"activity_{node_id}" if node_id else f"activity_{len(activities)}",
            "role": "assistant",
            "content": "",
            "createdAt": str(item.get("created_at") or ""),
            "activityCard": True,
            "reasoning": reasoning,
            "trace": trace,
            "intermediate": True,
        }
        model = str(value.get("model") or "").strip()
        if model:
            activity["model"] = model
        activities.append(activity)
    return tuple(activities)


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


def _stream_events(
    event: AgentSessionEvent,
    data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...] | None:
    mapping = {
        "assistant.stream.started": ("reply_start", "stream", "started", None),
        "assistant.stream.delta": ("reply_delta", "stream", "delta", "delta"),
        "assistant.stream.done": ("reply_done", "stream", "done", "response"),
        "assistant.reasoning.started": ("reasoning_start", "reasoning", "started", None),
        "assistant.reasoning.delta": ("reasoning_delta", "reasoning", "delta", "delta"),
        "assistant.reasoning.done": ("reasoning_done", "reasoning", "done", "response"),
    }
    projection = mapping.get(event.type)
    if projection is None:
        return None
    event_type, channel, phase, content_key = projection
    payload = {
        **_envelope(
            event,
            event_type,
            f"agent:{event.tree_id}:{event.run_id}:{channel}:{event.sequence}:{phase}",
        ),
        "type": event_type,
    }
    if content_key is not None:
        payload[content_key] = str(data.get(content_key) or "")
    return (payload,)


def _assistant_completed_events(
    event: AgentSessionEvent,
    data: Mapping[str, Any],
    event_id: str,
) -> tuple[dict[str, Any], ...]:
    if data.get("intermediate") is True:
        return ()
    content = str(data.get("content") or "")
    completed = {
        **_envelope(event, "reply_done", event_id + ":completed"),
        "type": "reply_done",
        "response": content,
    }
    if data.get("streamed") is True:
        return (completed,)
    return (
        {**_envelope(event, "reply_start", event_id + ":started"), "type": "reply_start"},
        {
            **_envelope(event, "reply_delta", event_id + ":delta"),
            "type": "reply_delta",
            "delta": content,
        },
        completed,
    )


def workbench_events(event: AgentSessionEvent) -> tuple[dict[str, Any], ...]:
    """Project one Agent observation into the versioned Workbench Chat protocol."""

    data = dict(event.data)
    event_id = f"agent:{event.tree_id}:{event.node_id or event.sequence}:{event.type}"
    stream_projection = _stream_events(event, data)
    if stream_projection is not None:
        return stream_projection
    if event.type == "input.accepted":
        return (_envelope(event, "run.started", event_id, {"status": "running"}),)
    if event.type == "input.answered":
        return (
            _envelope(
                event,
                "answer.accepted",
                event_id,
                {"questionId": str(data.get("question_id") or "")},
            ),
        )
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
        decoded_value = data.get("value")
        if isinstance(decoded_value, str):
            try:
                decoded_value = json.loads(decoded_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        awaiting = (
            success
            and isinstance(decoded_value, Mapping)
            and str(decoded_value.get("status") or "") == "awaiting_user"
        )
        return (
            _envelope(
                event,
                "tool.completed",
                f"agent:{event.tree_id}:{event.run_id}:tool:{call_id}:completed",
                {
                    "toolCallId": call_id,
                    "name": str(data.get("name") or ""),
                    "status": "awaiting_user" if awaiting else "completed" if success else "failed",
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
        pending = data.get("pending_question")
        if isinstance(pending, Mapping):
            question = WorkbenchPendingQuestion.from_mapping(pending)
            projected.append(
                _envelope(
                    event,
                    "awaiting_user",
                    f"agent:{event.tree_id}:{event.run_id}:question:{question.id}",
                    {
                        "pendingQuestion": question.as_dict(),
                        "plan": question.plan,
                    },
                )
            )
        return tuple(projected)
    if event.type == "assistant.completed":
        return _assistant_completed_events(event, data, event_id)
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
        self._session = session
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
        driver = self._session.session_driver
        pending_driver = bool(
            driver is not None and driver.has_pending_work
        )
        if pending_driver and event.type == "assistant.completed":
            return
        if (
            pending_driver
            and event.type == "session.state"
            and str(event.data.get("status") or "") == "idle"
        ):
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
            results = await asyncio.gather(
                *(asyncio.wrap_future(future) for future in pending),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, Exception)]
            if failures:
                first = failures[0]
                logger.error(
                    "Workbench event projection failed for run %s; "
                    "%d event(s) were not projected, but the durable Agent result remains valid",
                    self._run_id,
                    len(failures),
                    exc_info=(type(first), first, first.__traceback__),
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
        load_plugins: bool = True,
        model_plugin: str,
        chat_id: str,
        host_context: Mapping[str, Any] | None = None,
        plugin_context_data: Mapping[str, Any] | None = None,
        plugin_services: Mapping[str, Any] | None = None,
        max_model_calls: int = 12,
    ) -> WorkbenchSessionBridge:
        return cls(
            AgentSession(
                data_directory,
                workspace,
                plugin_directory,
                registry=registry,
                load_plugins=load_plugins,
                model_plugin=model_plugin,
                tree_id=str(chat_id),
                host_context=host_context,
                plugin_context_data=plugin_context_data,
                plugin_services=plugin_services,
                max_model_calls=max_model_calls,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return self.session.snapshot()

    def prepare_retry(self) -> dict[str, str]:
        """Select the durable parent used by the next retried submission."""

        return self.session.prepare_retry()

    async def compact(
        self,
        *,
        context_limit: int,
    ) -> dict[str, Any]:
        """Force an idle conversation tree through its native compactor."""

        return await self.session.compact_context(
            context_limit=context_limit,
        )

    def completed_result(self, run_id: str) -> WorkbenchChatResult:
        """Read the authoritative terminal node and its per-turn model metrics."""

        output = self.session.final_output(run_id)
        if output is None:
            raise AgentSessionRunError("Agent run finished without a terminal response")
        if output.get("cancelled") is True:
            raise AgentSessionCancelledError(
                str(output.get("cancel_reason") or "Agent run was cancelled")
            )
        if output.get("error") is True:
            raise AgentSessionRunError(str(output.get("content") or "Agent run failed"))
        node_id = str(output.get("node_id") or "")
        snapshot = self.session.snapshot()
        usage, latest_usage, model, identity, generation_duration_ms, rate = _turn_metrics(
            snapshot,
            str(run_id),
            node_id,
        )
        activity_messages = project_tool_activity_messages(snapshot, str(run_id))
        return WorkbenchChatResult(
            run_id=str(run_id),
            status="completed",
            text=str(output.get("content") or ""),
            node_id=node_id,
            usage=usage,
            latest_request_usage=latest_usage,
            model=model,
            model_identity=identity,
            generation_duration_ms=generation_duration_ms,
            output_tokens_per_second=rate,
            activity_messages=activity_messages,
            snapshot=snapshot,
            active_plan=_turn_plan(snapshot, str(run_id)),
        )

    def pending_result(self, run_id: str) -> WorkbenchChatResult:
        """Read the authoritative paused Plugin result for one Agent run."""

        output = self.session.pending_output()
        if output is None:
            raise AgentSessionRunError("Agent session is awaiting input without a pending question")
        pending = WorkbenchPendingQuestion.from_mapping(output)
        if not pending.id:
            raise AgentSessionRunError("Agent pending question has no id")
        node_id = str(output.get("node_id") or "")
        snapshot = self.session.snapshot()
        parent_id = ""
        for item in snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else ():
            if isinstance(item, Mapping) and str(item.get("id") or "") == node_id:
                parent_id = str(item.get("parent_id") or "")
                break
        usage, latest_usage, model, identity, generation_duration_ms, rate = _turn_metrics(
            snapshot,
            str(run_id),
            parent_id,
        )
        return WorkbenchChatResult(
            run_id=str(run_id),
            status="awaiting_user",
            text=pending.text,
            node_id=node_id,
            usage=usage,
            latest_request_usage=latest_usage,
            model=model,
            model_identity=identity,
            generation_duration_ms=generation_duration_ms,
            output_tokens_per_second=rate,
            activity_messages=project_tool_activity_messages(snapshot, str(run_id)),
            snapshot=snapshot,
            pending_question=pending,
            active_plan=pending.plan,
        )

    def current_result(self, run_id: str) -> WorkbenchChatResult:
        snapshot = self.session.snapshot()
        if str(snapshot.get("status") or "") == "awaiting_user":
            return self.pending_result(run_id)
        return self.completed_result(run_id)

    async def _result(
        self,
        run_id: str,
        *,
        publish: WorkbenchPublisher | None,
        replay: bool,
        cancel_on_caller_cancel: bool,
    ) -> WorkbenchChatResult:
        binding = (
            _PublisherBinding(self.session, publish, run_id=run_id, replay=replay)
            if publish is not None
            else None
        )
        try:
            await self.session.drain()
        except asyncio.CancelledError:
            if cancel_on_caller_cancel:
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

        return self.current_result(run_id)

    async def submit_result(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
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
            if cancel_on_caller_cancel:
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

        return self.current_result(normalized_run_id)

    async def submit(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> str:
        result = await self.submit_result(
            text,
            run_id=run_id,
            metadata=metadata,
            publish=publish,
            cancel_on_caller_cancel=cancel_on_caller_cancel,
        )
        return result.text

    async def resume_result(
        self,
        *,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> WorkbenchChatResult:
        run_id = self.session.current_run_id
        if not run_id:
            raise AgentSessionRunError("Agent session has no run to resume")
        return await self._result(
            run_id,
            publish=publish,
            replay=True,
            cancel_on_caller_cancel=cancel_on_caller_cancel,
        )

    async def resume(
        self,
        *,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> str:
        return (
            await self.resume_result(
                publish=publish,
                cancel_on_caller_cancel=cancel_on_caller_cancel,
            )
        ).text

    async def answer_result(
        self,
        question_id: str,
        answer: str,
        *,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> WorkbenchChatResult:
        run_id = self.session.current_run_id
        if not run_id:
            raise AgentSessionRunError("Agent session has no run to answer")
        binding = (
            _PublisherBinding(
                self.session,
                publish,
                run_id=run_id,
                replay=False,
            )
            if publish is not None
            else None
        )
        try:
            self.session.answer(question_id, answer)
            await self.session.drain()
        except asyncio.CancelledError:
            if cancel_on_caller_cancel:
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
        return self.current_result(run_id)

    async def answer(
        self,
        question_id: str,
        answer: str,
        *,
        publish: WorkbenchPublisher | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> str:
        return (
            await self.answer_result(
                question_id,
                answer,
                publish=publish,
                cancel_on_caller_cancel=cancel_on_caller_cancel,
            )
        ).text

    async def cancel(self, reason: str = "user_cancelled") -> bool:
        return await self.session.cancel(reason)

    def close(self) -> None:
        self.session.close()


__all__ = [
    "AgentSessionCancelledError",
    "AgentSessionRunError",
    "WorkbenchChatResult",
    "WorkbenchPendingQuestion",
    "WorkbenchPublisher",
    "WorkbenchSessionBridge",
    "project_tool_activity_messages",
    "workbench_events",
]
