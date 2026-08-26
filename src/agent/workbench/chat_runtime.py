"""Production adapter for the built-in Workbench Chat and the new Agent kernel."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from ..plugin import Plugin, PluginContext, PluginRegistry, default_plugin_impl_directory
from ..session import DEFAULT_SYSTEM_PROMPT
from .bridge import WorkbenchSessionBridge

WORKBENCH_CHAT_MODEL_PLUGIN = "WorkbenchChatModel"
WORKBENCH_CHAT_KERNEL_ENV = "CYRENE_WORKBENCH_CHAT_KERNEL"

_LEGACY_VALUES = frozenset({"0", "false", "no", "off", "legacy", "old"})
_FINAL_REPLY_EVENTS = frozenset({"reply_start", "reply_delta", "reply_done"})

PublishCallable: TypeAlias = Callable[[dict[str, Any]], Any | Awaitable[Any]]


def workbench_chat_kernel_enabled(value: str | None = None) -> bool:
    """Use the new kernel by default; accept only an explicit legacy rollback."""

    raw = os.environ.get(WORKBENCH_CHAT_KERNEL_ENV, "") if value is None else value
    return str(raw or "").strip().casefold() not in _LEGACY_VALUES


def _normalize_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    from cyrene.model_runtime.messages import parse_tool_arguments

    calls: list[dict[str, Any]] = []
    iterable = (
        raw_calls
        if isinstance(raw_calls, Sequence)
        and not isinstance(raw_calls, (str, bytes, bytearray))
        else ()
    )
    for raw in iterable:
        if not isinstance(raw, Mapping):
            raise ValueError("Model returned an invalid tool call")
        function = raw.get("function")
        source = function if isinstance(function, Mapping) else raw
        name = str(source.get("name") or "").strip()
        if not name:
            raise ValueError("Model tool call is missing function.name")
        calls.append(
            {
                "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                "name": name,
                "arguments": parse_tool_arguments(source.get("arguments")),
            }
        )
    return calls


def _model_messages(
    messages: list[dict[str, Any]],
    *,
    phase: str,
    system_extra: str,
) -> list[dict[str, Any]]:
    """Project turn-only Workbench context without changing durable nodes."""

    projected = deepcopy(messages)
    extra = str(system_extra or "").strip()
    if phase != "agent" or not extra:
        return projected
    for message in projected:
        if str(message.get("role") or "") != "system":
            continue
        content = str(message.get("content") or "").strip()
        message["content"] = "\n\n".join(part for part in (content, extra) if part)
        return projected
    projected.insert(0, {"role": "system", "content": extra})
    return projected


async def workbench_chat_model(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    """Call the model selected for this Workbench conversation."""

    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    tools = arguments.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")

    from cyrene.model_runtime import client as model_client
    from cyrene.model_runtime.messages import assistant_text

    session_id = str(context.data.get("session_id") or context.tree_id or "")
    run_id = str(context.data.get("run_id") or "")
    phase = str(context.data.get("model_call_kind") or "agent")
    model_messages = _model_messages(
        messages,
        phase=phase,
        system_extra=str(context.data.get("system_extra") or ""),
    )
    raw_run_context = context.data.get("run_context")
    binding = None
    if isinstance(raw_run_context, Mapping):
        from cyrene.agent.context import bind_run_context

        binding = bind_run_context(**dict(raw_run_context))
    try:
        response = await model_client.call_llm(
            model_messages,
            tools=tools,
            tool_choice=arguments.get("tool_choice"),
            candidates=None,
            max_tokens=(
                int(arguments["max_tokens"])
                if arguments.get("max_tokens") is not None
                else None
            ),
            stream=False,
            caller="main_agent",
            phase=phase,
            round_id=run_id,
            session_id=session_id,
        )
    finally:
        if binding is not None:
            binding.reset()
    if not isinstance(response, Mapping):
        raise RuntimeError("The selected Workbench model returned no assistant message")

    message = dict(response)
    reasoning = message.get("reasoning_content")
    reasoning_details = message.get("reasoning_details")
    usage = message.get("usage")
    return {
        "content": assistant_text(message),
        "reasoning": reasoning if isinstance(reasoning, str) else "",
        "reasoning_details": (
            [dict(item) for item in reasoning_details if isinstance(item, Mapping)]
            if isinstance(reasoning_details, list)
            else []
        ),
        "tool_calls": _normalize_tool_calls(message.get("tool_calls")),
        "finish_reason": str(message.get("finish_reason") or ""),
        "usage": dict(usage) if isinstance(usage, Mapping) else {},
        "model": str(message.get("model") or ""),
        "response_id": str(message.get("id") or message.get("response_id") or ""),
    }


def create_workbench_chat_model_plugin() -> Plugin:
    """Create the model Plugin backed by Cyrene's configured model runtime."""

    return Plugin(
        name=WORKBENCH_CHAT_MODEL_PLUGIN,
        description="Call the model selected for the current Workbench conversation.",
        input_schema={
            "type": "object",
            "properties": {
                "messages": {"type": "array"},
                "tools": {"type": "array"},
                "tool_choice": {
                    "oneOf": [
                        {"type": "string", "enum": ["auto", "none", "required"]},
                        {"type": "object"},
                    ]
                },
                "max_tokens": {"type": "integer"},
            },
            "required": ["messages"],
            "additionalProperties": False,
        },
        handler=workbench_chat_model,
        kind="model",
        timeout_seconds=180.0,
    )


class ThreadsafeWorkbenchPublisher:
    """Marshal legacy ContextVar events back to the owning Workbench loop."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        publish: PublishCallable,
    ) -> None:
        self._loop = loop
        self._publish = publish

    async def _send(self, event: dict[str, Any]) -> Any:
        result = self._publish(dict(event))
        if inspect.isawaitable(result):
            return await result
        return result

    async def __call__(self, event: dict[str, Any]) -> Any:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            return await self._send(event)
        future = asyncio.run_coroutine_threadsafe(self._send(event), self._loop)
        return await asyncio.wrap_future(future)


def _agent_data_directory(db_path: str, override: str | Path | None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    if str(db_path or "").strip():
        return Path(db_path).expanduser().resolve().parent / "agent-state"
    from cyrene.runtime.paths import USER_DATA_DIR

    return Path(USER_DATA_DIR).expanduser().resolve() / "agent-state"


async def _publish_without_final_reply(
    publish: PublishCallable,
    event: dict[str, Any],
) -> None:
    """Leave the final reply protocol to ChatRunLifecycleApplicationService."""

    if str(event.get("type") or "") in _FINAL_REPLY_EVENTS:
        return
    result = publish(dict(event))
    if inspect.isawaitable(result):
        await result


async def run_workbench_chat(
    *,
    run: Any,
    user_message: str,
    bot: Any,
    legacy_chat_id: Any,
    db_path: str,
    session_id: str,
    workspace_dir: str,
    client_request_id: str = "",
    permission_mode: str = "default",
    command: str = "",
    public_user_message: str | None = None,
    public_attachments: Sequence[Mapping[str, Any]] | None = None,
    attachment_paths: Mapping[str, str] | None = None,
    soul_enabled: bool | None = None,
    workspace_enabled: bool | None = None,
    system_extra: str = "",
    response_capabilities: Sequence[str] = (),
    ui_instance_id: str = "",
    conversation_source: str = "",
    plugin_directory: str | Path | None = None,
    data_directory: str | Path | None = None,
    max_model_calls: int = 12,
) -> str:
    """Run one ordinary built-in Workbench Chat turn on the Plugin kernel."""

    owner_loop = asyncio.get_running_loop()
    worker_publisher = ThreadsafeWorkbenchPublisher(owner_loop, run.publish)
    plugin_root = Path(
        plugin_directory or default_plugin_impl_directory()
    ).expanduser().resolve()
    state_root = _agent_data_directory(db_path, data_directory)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id cannot be empty")

    run_context = {
        "agent_id": "main",
        "caller": "main_agent",
        "client_request_id": str(client_request_id or ""),
        "command": str(command or ""),
        "user_request_text": str(public_user_message or user_message or ""),
        "conversation_source": str(conversation_source or ""),
        "round_id": str(run.run_id),
        "session_id": normalized_session_id,
        "ui_instance_id": str(ui_instance_id or ""),
        "workspace_dir": str(workspace_dir or ""),
        "soul_enabled": soul_enabled,
        "workspace_enabled": workspace_enabled,
        "permission_mode": str(permission_mode or "default"),
        "response_capabilities": frozenset(
            str(item or "").strip()
            for item in response_capabilities
            if str(item or "").strip()
        ),
        "deep_research": str(command or "").strip() == "deep-research",
        "attachment_paths": dict(attachment_paths or {}),
        "reply_stream_writer": worker_publisher,
        "runtime_event_writer": worker_publisher,
    }

    def open_bridge() -> WorkbenchSessionBridge:
        # The seed operation is idempotent and only fills missing built-ins. It
        # must happen before AgentSession takes its Plugin directory snapshot.
        from ..plugin import native_tools

        native_tools.seed_builtin_plugin_directory(plugin_root)
        registry = PluginRegistry()
        registry.register_plugin(
            create_workbench_chat_model_plugin(),
            source="workbench",
        )
        return WorkbenchSessionBridge.open(
            state_root,
            workspace_dir,
            plugin_root,
            registry=registry,
            model_plugin=WORKBENCH_CHAT_MODEL_PLUGIN,
            chat_id=normalized_session_id,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            host_context={
                "bot": bot,
                "chat_id": legacy_chat_id,
                "db_path": str(db_path or ""),
                "notify_state": None,
            },
            plugin_context_data={
                "session_id": normalized_session_id,
                "system_extra": str(system_extra or ""),
                "run_context": run_context,
            },
            max_model_calls=max_model_calls,
        )

    bridge = await asyncio.to_thread(open_bridge)

    async def publish(event: dict[str, Any]) -> None:
        await _publish_without_final_reply(run.publish, event)

    try:
        snapshot = bridge.snapshot()
        restored_run_id = str(snapshot.get("run_id") or "")
        if str(snapshot.get("status") or "") != "idle":
            if restored_run_id == str(run.run_id):
                return await bridge.resume(publish=publish)
            await bridge.cancel("superseded_by_new_workbench_run")
        turn_text = str(user_message or "").strip()
        if not turn_text:
            turn_text = str(public_user_message or "").strip()
        if not turn_text and str(command or "").strip():
            turn_text = f"/{str(command).strip()}"
        return await bridge.submit(
            turn_text,
            run_id=str(run.run_id),
            metadata={
                "client_request_id": str(client_request_id or ""),
                "public_user_message": str(public_user_message or ""),
                "public_attachments": [
                    dict(item) for item in (public_attachments or ())
                ],
                "command": str(command or ""),
            },
            publish=publish,
        )
    finally:
        await asyncio.to_thread(bridge.close)


__all__ = [
    "ThreadsafeWorkbenchPublisher",
    "WORKBENCH_CHAT_KERNEL_ENV",
    "WORKBENCH_CHAT_MODEL_PLUGIN",
    "create_workbench_chat_model_plugin",
    "run_workbench_chat",
    "workbench_chat_kernel_enabled",
    "workbench_chat_model",
]
