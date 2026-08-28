"""Workbench Task adapter for the durable Plugin-backed Agent kernel.

Task HTTP routes keep their existing project/session projection, but model and
tool execution belongs here.  Every turn reopens the same ContextTree and all
model work (including classification and planning) is routed through the
``kind=model`` Provider Plugins registered behind ``CyreneModelRouter``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.context import ContextStoreRouter, TreeNotFoundError
from agent.context.projection import project_context_message
from agent.plugin import (
    PluginContext,
    default_plugin_impl_directory,
    plugin_child_context_ids,
)
from agent.plugin.model_router import (
    MODEL_ROUTER_PLUGIN,
)
from agent.plugin.model_catalog import (
    set_session_model_preference as persist_session_model_preference,
)
from agent.workbench.bridge import (
    AgentSessionCancelledError,
    AgentSessionRunError,
    WorkbenchSessionBridge,
)
from agent.workbench.chat_runtime import workbench_agent_data_directory
from cyrene.localization import app_language, localized

logger = logging.getLogger(__name__)


_INDEPENDENT_TASK_CONTEXT = (
    "You are an independent read-only Task analyst. Inspect the real "
    "workspace with Plugin tools when useful. Do not modify files or Task "
    "state. Your final response must be exactly one JSON object."
)


def _l(en: str, zh: str, **values: Any) -> str:
    return localized(en, zh, **values)


def _output_language_instruction(language: str | None = None) -> str:
    return localized(
        "Write every user-visible text field in English unless the task explicitly "
        "requests another language.",
        "除非任务明确要求其他语言，否则所有用户可见的文本字段都使用简体中文。",
        language=language,
    )


class TaskAgentRuntimeError(RuntimeError):
    """Stable error contract exposed to the Task application service."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "task_agent_failed",
        status_code: int = 502,
    ) -> None:
        fallback = _l("Task Agent failed", "任务 Agent 执行失败")
        super().__init__(str(message or fallback))
        self.message = str(message or fallback)
        self.code = str(code or "task_agent_failed")
        self.status_code = int(status_code)


@dataclass(frozen=True, slots=True)
class TaskAgentResult:
    text: str
    awaiting_user: bool
    pending_question: dict[str, Any] | None
    tool_events: tuple[dict[str, Any], ...]
    usage: Mapping[str, int]
    model: str
    model_identity: Mapping[str, Any]
    snapshot: Mapping[str, Any]
    generation_duration_ms: float | None = None
    output_tokens_per_second: float | None = None


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(text[start : end + 1])
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _option_label(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("label", "text", "value", "title", "name"):
            label = str(value.get(key) or "").strip()
            if label:
                return label
        return ""
    return str(value or "").strip()


def _decoded_tool_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _run_nodes(snapshot: Mapping[str, Any], run_id: str) -> list[Mapping[str, Any]]:
    raw_nodes = snapshot.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    return [
        item
        for item in nodes
        if isinstance(item, Mapping)
        and isinstance(item.get("value"), Mapping)
        and str(item["value"].get("run_id") or "") == str(run_id or "")
    ]


def _tool_call(raw: Mapping[str, Any]) -> dict[str, Any]:
    function = raw.get("function")
    source = function if isinstance(function, Mapping) else raw
    arguments = source.get("arguments")
    return {
        "name": str(source.get("name") or ""),
        "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
    }


def _projected_tool_invocation(
    call: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[str, dict[str, Any], Any, str | None]:
    """Unwrap the model-facing toolbox protocol for Workbench projections."""

    name = str(result.get("name") or call.get("name") or "")
    arguments = dict(call.get("arguments") or {})
    decoded = _decoded_tool_value(result.get("value"))
    pack: str | None = None
    if name != "toolbox":
        return name, arguments, decoded, pack

    operation = str(arguments.get("operation") or "").strip()
    if operation != "invoke":
        display_name = ".".join(
            part for part in ("toolbox", operation) if part
        ) or "toolbox"
        return display_name, arguments, decoded, pack

    nested_arguments = arguments.get("arguments")
    arguments = (
        dict(nested_arguments) if isinstance(nested_arguments, Mapping) else {}
    )
    name = str(
        (decoded.get("name") if isinstance(decoded, Mapping) else "")
        or call.get("arguments", {}).get("name")
        or result.get("name")
        or "toolbox"
    )
    if isinstance(decoded, Mapping) and str(decoded.get("operation") or "") == "invoke":
        pack_value = decoded.get("pack")
        pack = str(pack_value) if pack_value is not None else None
        decoded = _decoded_tool_value(decoded.get("result"))
    return name, arguments, decoded, pack


def _project_tool_events(
    snapshot: Mapping[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], ...]:
    calls: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for node in _run_nodes(snapshot, run_id):
        value = node["value"]
        if value.get("role") == "assistant":
            for raw in value.get("tool_calls") or ():
                if not isinstance(raw, Mapping):
                    continue
                call_id = str(raw.get("id") or "")
                if not call_id:
                    continue
                calls[call_id] = _tool_call(raw)
            continue
        if value.get("role") != "tool_results":
            continue
        for result in value.get("results") or ():
            if not isinstance(result, Mapping):
                continue
            call_id = str(result.get("call_id") or "")
            call = calls.get(call_id, {})
            tool_name, arguments, decoded, pack = _projected_tool_invocation(
                call, result
            )
            preview = json.dumps(
                arguments, ensure_ascii=False, sort_keys=True, default=str
            )[:160]
            event = {
                "id": f"tool_{call_id or uuid4().hex[:12]}",
                "type": "ToolCallEvent",
                "runId": str(run_id or ""),
                "createdAt": str(node.get("created_at") or ""),
                "tool": tool_name,
                "argsPreview": preview,
                "args": arguments,
                "arguments": arguments,
                "success": bool(result.get("success")),
                "result": decoded,
                "error": str(result.get("error") or ""),
                "fileChanges": [],
            }
            if pack is not None:
                event["pack"] = pack
            events.append(event)
    return tuple(events)


def _pending_question(
    snapshot: Mapping[str, Any], run_id: str
) -> dict[str, Any] | None:
    calls: dict[str, dict[str, Any]] = {}
    pending: dict[str, Any] | None = None
    for node in _run_nodes(snapshot, run_id):
        value = node["value"]
        if value.get("role") == "assistant":
            for raw in value.get("tool_calls") or ():
                if not isinstance(raw, Mapping):
                    continue
                call_id = str(raw.get("id") or "")
                if call_id:
                    calls[call_id] = _tool_call(raw)
            continue
        if value.get("role") != "tool_results":
            continue
        for result in value.get("results") or ():
            if not isinstance(result, Mapping) or not bool(result.get("success")):
                continue
            call = calls.get(str(result.get("call_id") or ""), {})
            tool_name, arguments, decoded, _pack = _projected_tool_invocation(
                call, result
            )
            if not isinstance(decoded, Mapping):
                continue
            if str(decoded.get("status") or "") != "awaiting_user":
                continue
            raw_options = decoded.get("options")
            if not isinstance(raw_options, list):
                raw_options = arguments.get("options")
            options = [
                label
                for item in raw_options if (label := _option_label(item))
            ] if isinstance(raw_options, list) else []
            meta = arguments.get("meta")
            meta = dict(meta) if isinstance(meta, Mapping) else {}
            kind = str(
                decoded.get("kind")
                or arguments.get("kind")
                or meta.get("kind")
                or ""
            )
            text = str(
                decoded.get("text")
                or arguments.get("text")
                or arguments.get("reason")
                or ""
            ).strip()
            allow_custom = (
                bool(decoded.get("allow_custom"))
                if isinstance(decoded.get("allow_custom"), bool)
                else bool(arguments.get("allow_custom", True))
            )
            kind = kind or "clarification"
            text = text or "需要你确认后才能继续。"
            question_id = str(decoded.get("question_id") or "").strip()
            if not question_id:
                question_id = f"question_{str(result.get('call_id') or uuid4().hex)[:24]}"
            pending = {
                "id": question_id,
                "text": text,
                "options": options,
                "roundId": str(run_id or ""),
                "clientRequestId": str(
                    decoded.get("client_request_id")
                    or arguments.get("client_request_id")
                    or ""
                ),
                "ownerLane": "execution",
                "allowCustom": allow_custom,
                "kind": kind,
            }
            question_meta = {
                key: str(meta.get(key) or arguments.get(key) or "")
                for key in (
                    "kind", "tool_name", "operation", "path_hint", "reason"
                )
                if str(meta.get(key) or arguments.get(key) or "")
            }
            if question_meta:
                pending["meta"] = question_meta
    return pending


def _task_system_extra(
    project: Mapping[str, Any],
    session: Mapping[str, Any],
    *,
    purpose: str,
    instruction: str,
    attachments: Sequence[Mapping[str, Any]],
) -> str:
    plan = [
        {
            key: step.get(key)
            for key in ("id", "title", "description", "status", "dependsOn")
        }
        for step in (session.get("plan") or [])[:12]
        if isinstance(step, Mapping)
    ]
    context = {
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "workspacePath": project.get("workspacePath"),
        },
        "task": {
            "id": session.get("id"),
            "title": session.get("title"),
            "goal": session.get("goal"),
            "status": session.get("status"),
            "constraints": session.get("constraints") or [],
            "acceptanceCriteria": session.get("acceptanceCriteria") or [],
            "plan": plan,
        },
        "purpose": str(purpose or "task"),
        "attachments": [
            {
                key: item.get(key)
                for key in ("id", "name", "path", "kind", "content_type")
                if item.get(key) is not None
            }
            for item in attachments
        ],
    }
    parts = [
        "You are operating inside a Cyrene Workbench Task. Treat the following "
        "JSON as host-owned task context. Use the editable Plugin tools for all "
        "actions and keep the final answer concise and task-focused.",
        json.dumps(context, ensure_ascii=False, sort_keys=True, default=str),
        _output_language_instruction(),
    ]
    if str(instruction or "").strip():
        parts.append(str(instruction).strip())
    return "\n\n".join(parts)


class TaskAgentRuntime:
    """Open and drive one Task's persistent AgentSession on demand."""

    def __init__(
        self,
        *,
        bot: Any,
        db_path: str,
        plugin_directory: str | Path | None = None,
        data_directory: str | Path | None = None,
        max_model_calls: int = 12,
    ) -> None:
        self.bot = bot
        self.db_path = str(db_path or "")
        self.plugin_directory = Path(
            plugin_directory or default_plugin_impl_directory()
        ).expanduser().resolve()
        self.data_directory = workbench_agent_data_directory(
            self.db_path, data_directory
        )
        self.max_model_calls = max(1, int(max_model_calls))

    @staticmethod
    def _workspace(project: Mapping[str, Any]) -> Path:
        raw = str(project.get("workspacePath") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        from cyrene.config import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).expanduser().resolve()

    @staticmethod
    def _plugin_services() -> dict[str, Any]:
        from agent.plugin import active_plugin_application_host

        host = active_plugin_application_host()
        return host.active_services if host is not None else {}

    def _open_bridge(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        run_id: str,
        permission_mode: str,
        command: str,
        client_request_id: str,
        ui_instance_id: str,
        system_extra: str,
        attachments: Sequence[Mapping[str, Any]],
        owner_loop: asyncio.AbstractEventLoop,
        tree_id: str = "",
    ) -> WorkbenchSessionBridge:
        from agent.plugin import resolve_agent_plugin_registry

        registry, load_plugins = resolve_agent_plugin_registry(
            self.plugin_directory
        )
        normalized_mode = str(permission_mode or "default").strip().lower()
        if normalized_mode == "workspace_only":
            normalized_mode = "default"
        if normalized_mode not in {"default", "auto", "plan", "full_access"}:
            normalized_mode = "default"

        async def event_writer(event: Mapping[str, Any]) -> None:
            from cyrene.workbench.usage_events import publish_usage_event

            awaitable = publish_usage_event(event, session_id=session_id)
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                await awaitable
                return
            future = asyncio.run_coroutine_threadsafe(awaitable, owner_loop)
            await asyncio.wrap_future(future)

        def submit_background(awaitable: Any):
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                return owner_loop.create_task(awaitable)
            return asyncio.run_coroutine_threadsafe(awaitable, owner_loop)

        def call_on_owner(callback: Any) -> Any:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                return callback()
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()

            def invoke() -> None:
                try:
                    future.set_result(callback())
                except BaseException as exc:
                    future.set_exception(exc)

            owner_loop.call_soon_threadsafe(invoke)
            return future.result(timeout=30)

        attachment_paths = {
            str(item.get("id") or item.get("name") or index): str(item.get("path") or "")
            for index, item in enumerate(attachments)
            if str(item.get("path") or "").strip()
        }
        session_id = str(session.get("id") or "").strip()
        context_tree_id = str(tree_id or session_id).strip()
        run_context = {
            "agent_id": "main",
            "caller": "main_agent",
            "client_request_id": str(client_request_id or ""),
            "command": str(command or ""),
            "user_request_text": "",
            "conversation_source": "webui",
            "round_id": str(run_id or ""),
            "language": app_language(),
            "session_id": session_id,
            "ui_instance_id": str(ui_instance_id or ""),
            "workspace_dir": str(self._workspace(project)),
            "permission_mode": normalized_mode,
            "temporary_full_access": normalized_mode == "full_access",
            "response_capabilities": frozenset(),
            "deep_research": str(command or "").strip() == "deep-research",
            "attachment_paths": attachment_paths,
            "reply_stream_writer": event_writer,
            "runtime_event_writer": event_writer,
        }
        plugin_services = self._plugin_services()
        project_memory_snapshot = None
        memory_service = plugin_services.get("memory")
        snapshot_loader = getattr(memory_service, "current_snapshot", None)
        if callable(snapshot_loader):
            loaded = snapshot_loader(str(project.get("id") or ""))
            if isinstance(loaded, Mapping):
                project_memory_snapshot = dict(loaded)
        return WorkbenchSessionBridge.open(
            self.data_directory,
            self._workspace(project),
            self.plugin_directory,
            registry=registry,
            load_plugins=load_plugins,
            model_plugin=MODEL_ROUTER_PLUGIN,
            chat_id=context_tree_id,
            host_context={
                "bot": self.bot,
                "chat_id": session_id,
                "db_path": self.db_path,
                "notify_state": None,
            },
            plugin_context_data={
                "session_id": session_id,
                "project_id": str(project.get("id") or ""),
                "project_memory_snapshot": project_memory_snapshot,
                "session_title": str(session.get("title") or ""),
                "memory_write_enabled": True,
                "memory_trigger_enabled": True,
                "memory_archive_enabled": True,
                "background_submitter": submit_background,
                "owner_call": call_on_owner,
                "run_context": run_context,
            },
            plugin_services=plugin_services,
            max_model_calls=self.max_model_calls,
        )

    async def run_turn(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        text: str,
        run_id: str,
        permission_mode: str = "default",
        command: str = "",
        client_request_id: str = "",
        ui_instance_id: str = "",
        attachments: Sequence[Mapping[str, Any]] = (),
        purpose: str = "task",
        instruction: str = "",
        metadata: Mapping[str, Any] | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> TaskAgentResult:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            normalized_text = _l(
                "Please inspect the attached files and complete the task.",
                "请检查附加文件并完成任务。",
            )
        normalized_run_id = str(run_id or f"run_{uuid4().hex}")
        system_extra = _task_system_extra(
            project,
            session,
            purpose=purpose,
            instruction=instruction,
            attachments=attachments,
        )
        owner_loop = asyncio.get_running_loop()
        bridge: WorkbenchSessionBridge | None = None
        try:
            bridge = await asyncio.to_thread(
                self._open_bridge,
                project=project,
                session=session,
                run_id=normalized_run_id,
                permission_mode=permission_mode,
                command=command,
                client_request_id=client_request_id,
                ui_instance_id=ui_instance_id,
                system_extra=system_extra,
                attachments=attachments,
                owner_loop=owner_loop,
            )
            snapshot = bridge.snapshot()
            restored_run_id = str(snapshot.get("run_id") or "")
            status = str(snapshot.get("status") or "")
            if status == "idle" and restored_run_id == normalized_run_id:
                completed = bridge.completed_result(normalized_run_id)
            elif status != "idle" and restored_run_id == normalized_run_id:
                completed = await bridge.resume_result(
                    cancel_on_caller_cancel=cancel_on_caller_cancel
                )
            else:
                if status != "idle":
                    await bridge.cancel("superseded_by_new_task_run")
                completed = await bridge.submit_result(
                    normalized_text,
                    run_id=normalized_run_id,
                    metadata={
                        "task": True,
                        "purpose": str(purpose or "task"),
                        "command": str(command or ""),
                        "client_request_id": str(client_request_id or ""),
                        **dict(metadata or {}),
                        # Persist the exact host context that TurnStart must
                        # mount.  Reopened runs must not rebuild it from mutable
                        # Task state such as plan/status fields.
                        "ephemeral_context": system_extra,
                    },
                    cancel_on_caller_cancel=cancel_on_caller_cancel,
                )
        except AgentSessionCancelledError as exc:
            logger.info("Task Agent run was cancelled: %s", exc)
            raise TaskAgentRuntimeError(
                _l("The task run was cancelled.", "任务运行已取消。"),
                code="task_agent_cancelled",
                status_code=409,
            ) from exc
        except AgentSessionRunError as exc:
            logger.warning("Task Agent run failed", exc_info=True)
            raise TaskAgentRuntimeError(
                _l(
                    "The Task Agent could not complete this run.",
                    "任务 Agent 未能完成本次运行。",
                )
            ) from exc
        except TaskAgentRuntimeError:
            raise
        except Exception as exc:
            logger.exception("Unexpected Task Agent runtime failure")
            raise TaskAgentRuntimeError(
                _l(
                    "The Task Agent could not be started.",
                    "无法启动任务 Agent。",
                )
            ) from exc
        finally:
            if bridge is not None:
                bridge.close()

        pending = _pending_question(completed.snapshot, normalized_run_id)
        return TaskAgentResult(
            text=(
                str(pending.get("text") or "")
                if pending is not None
                else completed.text
            ),
            awaiting_user=pending is not None,
            pending_question=pending,
            tool_events=_project_tool_events(completed.snapshot, normalized_run_id),
            usage=dict(completed.usage),
            model=str(completed.model or ""),
            model_identity=dict(completed.model_identity),
            snapshot=completed.snapshot,
            generation_duration_ms=completed.generation_duration_ms,
            output_tokens_per_second=completed.output_tokens_per_second,
        )

    async def answer_turn(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        question_id: str,
        answer: str,
        run_id: str,
        permission_mode: str = "default",
        command: str = "",
        client_request_id: str = "",
        ui_instance_id: str = "",
        purpose: str = "task_answer",
        instruction: str = "",
        metadata: Mapping[str, Any] | None = None,
        cancel_on_caller_cancel: bool = True,
    ) -> TaskAgentResult:
        """Answer a durable Plugin question without creating a new Agent turn."""

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise TaskAgentRuntimeError(
                _l("Pending Agent run id is required", "缺少待处理的 Agent 运行 ID"),
                code="task_answer_run_missing",
                status_code=409,
            )
        owner_loop = asyncio.get_running_loop()
        bridge: WorkbenchSessionBridge | None = None
        try:
            bridge = await asyncio.to_thread(
                self._open_bridge,
                project=project,
                session=session,
                run_id=normalized_run_id,
                permission_mode=permission_mode,
                command=command,
                client_request_id=client_request_id,
                ui_instance_id=ui_instance_id,
                system_extra=_task_system_extra(
                    project,
                    session,
                    purpose=purpose,
                    instruction=instruction,
                    attachments=(),
                ),
                attachments=(),
                owner_loop=owner_loop,
            )
            snapshot = bridge.snapshot()
            restored_run_id = str(snapshot.get("run_id") or "")
            status = str(snapshot.get("status") or "")
            if restored_run_id != normalized_run_id:
                raise TaskAgentRuntimeError(
                    _l(
                        "The pending Agent run no longer owns this context.",
                        "待处理的 Agent 运行已不再拥有此上下文。",
                    ),
                    code="task_answer_run_mismatch",
                    status_code=409,
                )
            if status == "awaiting_user":
                completed = await bridge.answer_result(
                    str(question_id or ""),
                    str(answer or ""),
                    cancel_on_caller_cancel=cancel_on_caller_cancel,
                )
            elif status == "idle":
                # Idempotent recovery: the answer completed before the host wrote
                # its projection, so reuse the terminal node from the same run.
                completed = bridge.completed_result(normalized_run_id)
            else:
                # The answer was durably mounted but the process stopped before
                # the model settled. Resume that exact run instead of re-answering.
                completed = await bridge.resume_result(
                    cancel_on_caller_cancel=cancel_on_caller_cancel
                )
        except AgentSessionCancelledError as exc:
            logger.info("Task Agent answer run was cancelled: %s", exc)
            raise TaskAgentRuntimeError(
                _l("The task run was cancelled.", "任务运行已取消。"),
                code="task_agent_cancelled",
                status_code=409,
            ) from exc
        except AgentSessionRunError as exc:
            logger.warning("Task Agent answer run failed", exc_info=True)
            raise TaskAgentRuntimeError(
                _l(
                    "The Task Agent could not complete this answer.",
                    "任务 Agent 未能完成本次答复。",
                )
            ) from exc
        except TaskAgentRuntimeError:
            raise
        except Exception as exc:
            logger.exception("Unexpected Task Agent answer failure")
            raise TaskAgentRuntimeError(
                _l(
                    "The Task Agent could not process this answer.",
                    "任务 Agent 无法处理本次答复。",
                )
            ) from exc
        finally:
            if bridge is not None:
                bridge.close()

        pending = _pending_question(completed.snapshot, normalized_run_id)
        return TaskAgentResult(
            text=(
                str(pending.get("text") or "")
                if pending is not None
                else completed.text
            ),
            awaiting_user=pending is not None,
            pending_question=pending,
            tool_events=_project_tool_events(completed.snapshot, normalized_run_id),
            usage=dict(completed.usage),
            model=str(completed.model or ""),
            model_identity=dict(completed.model_identity),
            snapshot=completed.snapshot,
            generation_duration_ms=completed.generation_duration_ms,
            output_tokens_per_second=completed.output_tokens_per_second,
        )

    async def cancel_turn(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        run_id: str,
        reason: str = "user_cancelled",
    ) -> bool:
        """Persist cancellation for one exact Task run in its ContextTree."""

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        owner_loop = asyncio.get_running_loop()
        bridge: WorkbenchSessionBridge | None = None
        try:
            bridge = await asyncio.to_thread(
                self._open_bridge,
                project=project,
                session=session,
                run_id=normalized_run_id,
                permission_mode="default",
                command="workbench-task-cancel",
                client_request_id="",
                ui_instance_id="",
                system_extra="",
                attachments=(),
                owner_loop=owner_loop,
            )
            snapshot = bridge.snapshot()
            if str(snapshot.get("run_id") or "") != normalized_run_id:
                return False
            if str(snapshot.get("status") or "") == "idle":
                return False
            return await bridge.cancel(str(reason or "user_cancelled"))
        finally:
            if bridge is not None:
                bridge.close()

    async def _model_response(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        messages: list[dict[str, Any]],
        purpose: str,
        max_tokens: int,
        response_format: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        owner_loop = asyncio.get_running_loop()
        run_id = f"task_aux_{uuid4().hex}"
        bridge = await asyncio.to_thread(
            self._open_bridge,
            project=project,
            session=session,
            run_id=run_id,
            permission_mode="default",
            command="",
            client_request_id="",
            ui_instance_id="",
            system_extra="",
            attachments=(),
            owner_loop=owner_loop,
        )
        try:
            data = dict(bridge.session.plugin_context_data)
            run_context = data.get("run_context")
            run_context = dict(run_context) if isinstance(run_context, Mapping) else {}
            run_context["round_id"] = run_id
            data.update({
                "run_id": run_id,
                "model_call_kind": str(purpose or "task_auxiliary"),
                "run_context": run_context,
            })
            arguments: dict[str, Any] = {
                "messages": messages,
                "max_tokens": max(1, int(max_tokens)),
            }
            context_mounts = await bridge.session.build_model_mounts(
                {
                    "run_id": run_id,
                    "agent_id": bridge.session.agent_id,
                    "parent_agent_id": bridge.session.parent_agent_id,
                    "user_request": "",
                    "metadata": {
                        "ephemeral_context": _task_system_extra(
                            project,
                            session,
                            purpose=purpose,
                            instruction="",
                            attachments=(),
                        )
                    },
                }
            )
            if context_mounts:
                contextualized = [dict(message) for message in messages]
                for mount in context_mounts:
                    project_context_message(
                        contextualized,
                        {
                            "content": mount.get("content"),
                            "context_lifecycle": mount.get("lifecycle"),
                            "context_kind": mount.get("kind"),
                            "context_source": mount.get("source"),
                        },
                    )
                arguments["messages"] = contextualized
            if response_format is not None:
                arguments["response_format"] = dict(response_format)
            result = await bridge.session.runtime.call(
                MODEL_ROUTER_PLUGIN,
                arguments,
                PluginContext(
                    workspace=bridge.session.workspace,
                    tree=bridge.session.store,
                    tree_id=bridge.session.tree.id,
                    node_id=str(bridge.snapshot().get("leaf_id") or ""),
                    data=data,
                    services=bridge.session.active_plugin_services(),
                ),
                call_id=run_id,
            )
            if not result.success or not isinstance(result.value, Mapping):
                raise TaskAgentRuntimeError(
                    result.error or "Task model Plugin returned no result"
                )
            return dict(result.value)
        finally:
            bridge.close()

    def _context_history(self, session_id: str, *, max_chars: int = 32000) -> list[dict[str, Any]]:
        """Read user-visible history from the Task's authoritative ContextTree."""

        tree_id = str(session_id or "").strip()
        if not tree_id:
            return []
        router = ContextStoreRouter(self.data_directory / "context")
        try:
            tree = router.get_tree(tree_id)
            nodes = router.get_subtree(tree.id, tree.root_id)
        except TreeNotFoundError:
            return []
        finally:
            router.close()
        history: list[dict[str, Any]] = []
        used = 0
        for node in nodes:
            value = node.value
            if not isinstance(value, Mapping):
                continue
            role = str(value.get("role") or "")
            if role not in {"user", "assistant", "tool_results"}:
                continue
            if role == "tool_results":
                content = json.dumps(
                    value.get("results") or [], ensure_ascii=False, default=str
                )
            else:
                content = str(value.get("content") or "")
            content = content.strip()
            if not content:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                break
            content = content[:remaining]
            history.append({"role": role, "content": content})
            used += len(content)
        return history[-80:]

    def _delete_context_tree(self, tree_id: str) -> bool:
        normalized = str(tree_id or "").strip()
        if not normalized:
            return False
        router = ContextStoreRouter(self.data_directory / "context")
        children: list[str] = []
        try:
            try:
                tree = router.get_tree(normalized)
                root = router.get_node(tree.id, tree.root_id)
            except TreeNotFoundError:
                return False
            value = root.value if isinstance(root.value, Mapping) else {}
            children = list(plugin_child_context_ids(value))
            for child_id in children:
                try:
                    router.delete_tree(child_id)
                except TreeNotFoundError:
                    pass
            router.delete_tree(normalized)
            return True
        finally:
            router.close()

    async def clear_session(self, session_id: str) -> bool:
        """Delete a Task ContextTree and its recorded subagent trees."""

        return await asyncio.to_thread(self._delete_context_tree, session_id)

    async def _independent_json_agent(
        self,
        *,
        project: Mapping[str, Any],
        session: Mapping[str, Any],
        prompt: str,
        purpose: str,
    ) -> dict[str, Any]:
        """Run a clean, tool-capable auxiliary Agent and return its JSON result.

        The verifier/planner receives the same Plugin toolbox and workspace as the
        main Task, but a separate ContextTree, so it cannot mistake the execution
        Agent's self-report for independent evidence.
        """

        run_id = f"task_aux_{uuid4().hex}"
        tree_id = f"{str(session.get('id') or 'task')}.aux.{purpose}.{uuid4().hex}"
        owner_loop = asyncio.get_running_loop()
        bridge: WorkbenchSessionBridge | None = None
        try:
            bridge = await asyncio.to_thread(
                self._open_bridge,
                project=project,
                session=session,
                run_id=run_id,
                permission_mode="plan",
                command="",
                client_request_id="",
                ui_instance_id="",
                system_extra=_INDEPENDENT_TASK_CONTEXT,
                attachments=(),
                owner_loop=owner_loop,
                tree_id=tree_id,
            )
            completed = await bridge.submit_result(
                str(prompt or ""),
                run_id=run_id,
                metadata={
                    "task": True,
                    "purpose": purpose,
                    "auxiliary": True,
                    "ephemeral_context": _INDEPENDENT_TASK_CONTEXT,
                },
            )
            parsed = _json_object(completed.text)
            if not parsed:
                raise TaskAgentRuntimeError(
                    _l(
                        "The {purpose} Agent returned no JSON object.",
                        "{purpose} Agent 未返回 JSON 对象。",
                        purpose=purpose,
                    ),
                    code="task_auxiliary_response_format",
                )
            return parsed
        except (AgentSessionCancelledError, AgentSessionRunError) as exc:
            logger.warning("Auxiliary Task Agent failed for %s", purpose, exc_info=True)
            raise TaskAgentRuntimeError(
                _l(
                    "The {purpose} Agent could not complete its run.",
                    "{purpose} Agent 未能完成运行。",
                    purpose=purpose,
                ),
                code="task_auxiliary_failed",
            ) from exc
        finally:
            if bridge is not None:
                bridge.close()
            await asyncio.to_thread(self._delete_context_tree, tree_id)

    async def should_reflect(
        self,
        goal: str,
        acceptance: Sequence[Any],
        feedback: str,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> bool:
        if not str(feedback or "").strip():
            return False
        try:
            response = await self._model_response(
                project=project,
                session=session,
                messages=[{
                    "role": "user",
                    "content": (
                        "Decide whether this feedback means the overall goal/direction "
                        "failed and needs reflection, rather than a local edit. Return JSON "
                        "only as {\"reflect\":true|false}.\n\n"
                        + json.dumps(
                            {
                                "goal": goal,
                                "acceptance": list(acceptance),
                                "feedback": feedback,
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ),
                }],
                purpose="task_reflection_classification",
                max_tokens=120,
                response_format={"type": "json_object"},
            )
            return bool(_json_object(response.get("content")).get("reflect"))
        except Exception:
            return False

    async def reflect_task(
        self,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
        *,
        focus: str = "",
        goal_gap: str = "",
    ) -> dict[str, Any] | None:
        history = self._context_history(str(session.get("id") or ""))
        if not history:
            return None
        response = await self._model_response(
            project=project,
            session=session,
            messages=[{
                "role": "user",
                "content": (
                    "Reflect on this Task history and return a reusable JSON packet with "
                    "objective, attempt_summary, root_causes, excluded_paths, "
                    "promising_directions, and next_step. Arrays must contain concise "
                    "strings; do not include hidden reasoning.\n\n"
                    + json.dumps(
                        {
                            "task": {
                                "goal": session.get("goal"),
                                "constraints": session.get("constraints") or [],
                                "acceptance": session.get("acceptanceCriteria") or [],
                            },
                            "focus": focus,
                            "goalGap": goal_gap,
                            "history": history,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\n"
                    + _output_language_instruction()
                ),
            }],
            purpose="task_reflection",
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        parsed = _json_object(response.get("content"))
        if not parsed:
            return None
        packet: dict[str, Any] = {
            "objective": str(parsed.get("objective") or session.get("goal") or "").strip(),
            "attempt_summary": str(parsed.get("attempt_summary") or "").strip(),
            "next_step": str(parsed.get("next_step") or "").strip(),
        }
        for key in ("root_causes", "excluded_paths", "promising_directions"):
            raw = parsed.get(key)
            packet[key] = [
                str(item).strip()[:1000]
                for item in raw if str(item).strip()
            ][:12] if isinstance(raw, list) else []
        return packet

    async def reflection_hints(
        self,
        packet: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> dict[str, str]:
        if not packet or not candidates:
            return {}
        try:
            response = await self._model_response(
                project=project,
                session=session,
                messages=[{
                    "role": "user",
                    "content": (
                        "Match a reflection packet to other open Tasks. Return JSON only "
                        "as {\"matches\":[{\"sessionId\":\"...\",\"relevant\":true," 
                        "\"hint\":\"one concrete short suggestion\"}]}. Include only "
                        "genuinely reusable findings.\n\n"
                        + json.dumps(
                            {"reflection": dict(packet), "candidates": list(candidates)},
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n\n"
                        + _output_language_instruction()
                    ),
                }],
                purpose="task_reflection_hints",
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
        except Exception:
            return {}
        parsed = _json_object(response.get("content"))
        matches: dict[str, str] = {}
        raw_matches = parsed.get("matches")
        for item in raw_matches if isinstance(raw_matches, list) else ():
            if not isinstance(item, Mapping) or not bool(item.get("relevant")):
                continue
            candidate_id = str(item.get("sessionId") or "").strip()
            hint = str(item.get("hint") or "").strip()[:200]
            if candidate_id and hint:
                matches[candidate_id] = hint
        return matches

    async def generate_acceptance_criteria(
        self,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        from cyrene.workbench import planning_runtime

        plan = [item for item in session.get("plan") or [] if isinstance(item, Mapping)]
        fallback = planning_runtime._workbench_fallback_acceptance(dict(session), plan)
        try:
            parsed = await self._independent_json_agent(
                project=project,
                session=session,
                purpose="acceptance_design",
                prompt=(
                    "Inspect the Task and workspace, then produce 3-8 independently "
                    "verifiable acceptance criteria. Return JSON only as "
                    "{\"acceptanceCriteria\":[\"...\"]}.\n\n"
                    + json.dumps(
                        {
                            "goal": session.get("goal") or session.get("title"),
                            "constraints": session.get("constraints") or [],
                            "plan": plan,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\n"
                    + _output_language_instruction()
                ),
            )
        except Exception:
            return fallback, False
        criteria = planning_runtime._workbench_coerce_acceptance_criteria(parsed, fallback)
        raw = parsed.get("acceptanceCriteria")
        generated = isinstance(raw, list) and any(str(item).strip() for item in raw)
        return criteria, generated

    async def verify_acceptance(
        self,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        criteria = [
            item for item in session.get("acceptanceCriteria") or []
            if isinstance(item, Mapping)
        ]
        if not criteria:
            return None
        parsed = await self._independent_json_agent(
            project=project,
            session=session,
            purpose="acceptance_verification",
            prompt=(
                "Independently verify every criterion against real workspace evidence. "
                "Do not trust the execution Agent's claims. Return JSON only as "
                "{\"results\":[{\"id\":\"criterion id\",\"passed\":true," 
                "\"evidence\":\"concise evidence\"}],\"recommend_reflection\":"
                "true,\"reason\":\"...\"}.\n\n"
                + json.dumps(
                    {
                        "goal": session.get("goal") or session.get("title"),
                        "criteria": criteria,
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n\n"
                + _output_language_instruction()
            ),
        )
        expected = {str(item.get("id") or "") for item in criteria}
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        raw_results = parsed.get("results")
        for item in raw_results if isinstance(raw_results, list) else ():
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("id") or "")
            if item_id not in expected or item_id in seen or not isinstance(item.get("passed"), bool):
                continue
            seen.add(item_id)
            results.append({
                "id": item_id,
                "passed": bool(item.get("passed")),
                "evidence": str(item.get("evidence") or "").strip(),
            })
        if seen != expected:
            raise TaskAgentRuntimeError(
                _l(
                    "The acceptance verifier omitted one or more criteria.",
                    "验收验证器遗漏了一个或多个验收条件。",
                ),
                code="task_acceptance_response_format",
            )
        return {
            "results": results,
            "recommend_reflection": bool(parsed.get("recommend_reflection")),
            "reason": str(parsed.get("reason") or "").strip(),
        }

    async def generate_init_form(
        self,
        project: Mapping[str, Any],
        *,
        lang: str = "",
    ) -> dict[str, Any] | None:
        from cyrene.workbench import project_runtime, task_initialization_runtime

        session = next(
            (
                item for item in project.get("sessions") or []
                if isinstance(item, Mapping) and str(item.get("kind") or "") == "init"
            ),
            {"id": f"project_{str(project.get('id') or 'new')}_init", "kind": "init"},
        )
        base = project_runtime._workbench_default_init_form(dict(project))
        language_code = app_language(lang)
        language = "English" if language_code == "en" else "简体中文"
        try:
            parsed = await self._independent_json_agent(
                project=project,
                session=session,
                purpose="initialization_form",
                prompt=(
                    "Inspect the workspace when it is non-empty, without assuming existing "
                    "files belong to the new project. Design a tailored project onboarding "
                    f"form in {language}. Return JSON only with greeting and sections; each "
                    "section has id, title, and 2-4 questions. Each question has id, type "
                    "(text|textarea|single|multi), label, optional placeholder, and options "
                    "for choice questions. Ask 3-5 groups covering goal, workspace "
                    "relationship, scope, constraints, audience, priorities, and acceptance.\n\n"
                    + json.dumps(
                        {
                            "name": project.get("name"),
                            "description": project.get("description"),
                            "template": project.get("template"),
                            "workspacePath": project.get("workspacePath"),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n\n"
                    + _output_language_instruction(language_code)
                ),
            )
        except Exception:
            return None
        return task_initialization_runtime._workbench_coerce_init_form(parsed, base)

    async def generate_init_plan(
        self,
        project: Mapping[str, Any],
        form: Mapping[str, Any],
        *,
        session: Mapping[str, Any] | None = None,
        feedback: str = "",
        current_plan: Sequence[Mapping[str, Any]] | None = None,
        max_attempts: int = 3,
    ) -> tuple[list[dict[str, Any]] | None, bool, dict[str, Any] | None]:
        from cyrene.workbench import task_initialization_runtime

        effective_session: Mapping[str, Any] = session or next(
            (
                item for item in project.get("sessions") or []
                if isinstance(item, Mapping) and str(item.get("kind") or "") == "init"
            ),
            {"id": f"project_{str(project.get('id') or 'new')}_init", "kind": "init"},
        )
        brief = task_initialization_runtime._workbench_init_brief(
            dict(project), dict(form)
        )
        prompt = (
            "Inspect the workspace and split this initialized project into 3-6 major, "
            "independently executable Task sessions. Preserve explicit scope, technology, "
            "time, and delivery constraints. Return JSON only as {\"tasks\":[{" 
            "\"title\":\"...\",\"goal\":\"...\",\"priority\":"
            "\"high|medium|low\",\"constraints\":[\"...\"],"
            "\"acceptanceCriteria\":[\"...\"]}]}.\n\n"
            + json.dumps(
                {
                    "project": {
                        "name": project.get("name"),
                        "description": project.get("description"),
                        "template": project.get("template"),
                    },
                    "initializationBrief": brief,
                    "feedback": str(feedback or ""),
                    "currentPlan": list(current_plan or ()),
                },
                ensure_ascii=False,
                default=str,
            )
            + "\n\n"
            + _output_language_instruction()
        )
        attempts: list[dict[str, Any]] = []
        attempt_limit = max(1, int(max_attempts or 1))
        for attempt in range(1, attempt_limit + 1):
            try:
                parsed = await self._independent_json_agent(
                    project=project,
                    session=effective_session,
                    purpose=f"initialization_plan_{attempt}",
                    prompt=prompt,
                )
                plan = task_initialization_runtime._workbench_coerce_init_task_plan(
                    parsed, []
                )
                if not plan:
                    raise TaskAgentRuntimeError(
                        _l(
                            "The initialization model returned no usable tasks.",
                            "初始化模型未返回可用任务。",
                        ),
                        code="task_initialization_response_format",
                    )
                return plan, True, None
            except Exception as exc:
                attempts.append({
                    "attempt": attempt,
                    "category": getattr(exc, "code", "model"),
                    "message": str(exc),
                })
        last = attempts[-1] if attempts else {
            "category": "model",
            "message": _l(
                "Initialization plan generation failed.",
                "初始化计划生成失败。",
            ),
        }
        return None, False, {
            "code": "init_plan_generation_failed",
            "attemptCount": attempt_limit,
            "category": last["category"],
            "summary": last["message"],
            "attempts": attempts,
        }

    async def classify_intent(
        self,
        text: str,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> str:
        prompt = (
            "Classify the user's Task composer message. Return JSON only as "
            '{"kind":"answer|direct|plan|finalize"}. answer means a question or '
            "discussion, direct means execute one bounded instruction now, plan means "
            "a complex goal that needs editable multi-step planning, and finalize means "
            "the user asks to hand off, review, or declare the existing work done."
        )
        try:
            response = await self._model_response(
                project=project,
                session=session,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(text or "")},
                ],
                purpose="task_classification",
                max_tokens=120,
                response_format={"type": "json_object"},
            )
            kind = str(_json_object(response.get("content")).get("kind") or "")
            if kind in {"answer", "direct", "plan", "finalize"}:
                return kind
        except Exception:
            pass
        normalized = str(text or "").strip().lower()
        if any(word in normalized for word in ("验收", "交付", "完成了", "done")):
            return "finalize"
        if normalized.endswith(("?", "？")):
            return "answer"
        if any(word in normalized for word in ("规划", "计划", "拆解", "步骤")):
            return "plan"
        return "direct"

    async def extract_constraints(
        self,
        text: str,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> list[str]:
        if not str(text or "").strip():
            return []
        try:
            response = await self._model_response(
                project=project,
                session=session,
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract only explicit execution constraints from the following "
                        "request. Do not invent constraints. Return JSON only as "
                        '{"constraints":["..."]}, at most 8 items.\n\n'
                        + str(text)
                        + "\n\n"
                        + _output_language_instruction()
                    ),
                }],
                purpose="task_constraint_extraction",
                max_tokens=700,
                response_format={"type": "json_object"},
            )
        except Exception:
            return []
        raw = _json_object(response.get("content")).get("constraints")
        constraints: list[str] = []
        for value in raw if isinstance(raw, list) else ():
            item = re.sub(r"\s+", " ", str(value or "").strip())[:300]
            if item and item not in constraints:
                constraints.append(item)
            if len(constraints) >= 8:
                break
        return constraints

    async def generate_title(
        self,
        text: str,
        session: Mapping[str, Any],
        project: Mapping[str, Any],
    ) -> str:
        try:
            response = await self._model_response(
                project=project,
                session=session,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Generate a concise Task title. "
                            "Return only one plain-text line, no quotes or punctuation, "
                            "at most 24 Chinese characters or 12 words. "
                            + _output_language_instruction()
                        ),
                    },
                    {"role": "user", "content": str(text or "")},
                ],
                purpose="task_title",
                max_tokens=80,
            )
        except Exception:
            return ""
        title = re.sub(r"\s+", " ", str(response.get("content") or "")).strip()
        return title.strip("\"'`#*_ ").rstrip("。！？!?；;，,")[:80]

    async def generate_plan(
        self,
        session: dict[str, Any],
        project: Mapping[str, Any],
        *,
        feedback: str = "",
        auto_start: bool = False,
        requested_operation: str = "auto",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
        from cyrene.workbench import planning_runtime

        existing = [
            dict(step) for step in session.get("plan") or [] if isinstance(step, Mapping)
        ]
        prompt = (
            "Create or revise an executable Task plan. Return JSON only with "
            "operation ('create', 'revise', or 'replace'), optional goal/title, "
            "steps (3-12), and "
            "acceptanceCriteria (3-8). Each step needs title, description, "
            "dependsOnStepIndexes (one-based indexes of earlier steps), and optional "
            "sourceStepId when preserving an existing step.\n\n"
            + json.dumps(
                {
                    "project": {
                        "name": project.get("name"),
                        "workspacePath": project.get("workspacePath"),
                    },
                    "task": {
                        "goal": session.get("goal"),
                        "title": session.get("title"),
                        "constraints": session.get("constraints") or [],
                        "existingPlan": existing,
                    },
                    "feedback": str(feedback or ""),
                    "autoStart": bool(auto_start),
                    "requestedOperation": str(requested_operation or "auto"),
                },
                ensure_ascii=False,
                default=str,
            )
            + "\n\n"
            + _output_language_instruction()
        )
        try:
            parsed = await self._independent_json_agent(
                project=project,
                session=session,
                purpose="task_planning",
                prompt=prompt,
            )
            generated = planning_runtime._workbench_coerce_plan_steps(parsed, session)
            if not generated:
                raise ValueError(
                    _l(
                        "The model returned no plan steps.",
                        "模型未返回计划步骤。",
                    )
                )
            operation = str(
                parsed.get("operation") or parsed.get("revisionMode") or ""
            ).strip().lower()
            requested = str(requested_operation or "auto").strip().lower()
            if requested in {"revise", "replace"}:
                operation = requested
            if operation not in {"create", "revise", "replace"}:
                operation = "revise" if existing else "create"
            steps = (
                planning_runtime._workbench_reconcile_revised_plan(
                    existing, generated, str(feedback or ""), operation
                )
                if existing
                else planning_runtime._workbench_normalize_plan(
                    generated, task_id=str(session.get("id") or "")
                )
            )
            fallback_acceptance = planning_runtime._workbench_fallback_acceptance(
                {**session, "plan": steps}, steps
            )
            acceptance = planning_runtime._workbench_coerce_acceptance_criteria(
                parsed, fallback_acceptance
            )
            if str(parsed.get("goal") or "").strip():
                session["goal"] = str(parsed["goal"]).strip()
            if str(parsed.get("title") or "").strip() and not session.get("titleLocked"):
                session["title"] = str(parsed["title"]).strip()[:80]
            return steps, acceptance, True, operation
        except Exception:
            steps = (
                existing
                or planning_runtime._workbench_plan_from_input(
                    str(session.get("goal") or feedback or ""), session
                )
            )
            acceptance = planning_runtime._workbench_fallback_acceptance(
                {**session, "plan": steps}, steps
            )
            fallback_operation = (
                "replace" if str(requested_operation or "").lower() == "replace"
                else "revise" if existing else "create"
            )
            return steps, acceptance, False, fallback_operation


__all__ = [
    "TaskAgentResult",
    "TaskAgentRuntime",
    "TaskAgentRuntimeError",
    "persist_session_model_preference",
]
