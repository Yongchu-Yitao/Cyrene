"""Production adapter for the built-in Workbench Chat and the new Agent kernel."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, TypeAlias

from ..plugin import default_plugin_impl_directory, resolve_agent_plugin_registry
from ..plugin.model_router import MODEL_ROUTER_PLUGIN
from ..permission import runtime_permission_mode
from .bridge import WorkbenchChatResult, WorkbenchSessionBridge
from cyrene.localization import app_language

_FINAL_REPLY_EVENTS = frozenset({"reply_start", "reply_delta", "reply_done"})

PublishCallable: TypeAlias = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class ThreadsafeWorkbenchPublisher:
    """Marshal Agent events back to the owning Workbench loop."""

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


def workbench_agent_data_directory(
    db_path: str,
    override: str | Path | None = None,
) -> Path:
    """Return the durable data root used by the Workbench Agent kernel."""

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
    host_chat_id: Any,
    db_path: str,
    session_id: str,
    workspace_dir: str,
    client_request_id: str = "",
    permission_mode: str = "default",
    command: str = "",
    public_user_message: str | None = None,
    public_attachments: Sequence[Mapping[str, Any]] | None = None,
    attachment_paths: Mapping[str, str] | None = None,
    remote_device_ids: Sequence[str] = (),
    soul_enabled: bool | None = None,
    workspace_enabled: bool | None = None,
    system_extra: str = "",
    project_id: str = "",
    project_memory_snapshot: Mapping[str, Any] | None = None,
    session_title: str = "",
    memory_write_enabled: bool = True,
    memory_trigger_enabled: bool = True,
    memory_archive_enabled: bool = True,
    retry: bool = False,
    completed_turn_count: int = 0,
    response_capabilities: Sequence[str] = (),
    ui_instance_id: str = "",
    conversation_source: str = "",
    plugin_directory: str | Path | None = None,
    data_directory: str | Path | None = None,
    max_model_calls: int = 12,
) -> WorkbenchChatResult:
    """Run one ordinary built-in Workbench Chat turn on the Plugin kernel."""

    owner_loop = asyncio.get_running_loop()
    worker_publisher = ThreadsafeWorkbenchPublisher(owner_loop, run.publish)
    plugin_root = Path(
        plugin_directory or default_plugin_impl_directory()
    ).expanduser().resolve()
    state_root = workbench_agent_data_directory(db_path, data_directory)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id cannot be empty")
    requested_permission_mode = str(permission_mode or "default").strip().lower()
    normalized_permission_mode = runtime_permission_mode(requested_permission_mode)

    run_context = {
        "agent_id": "main",
        "caller": "main_agent",
        "client_request_id": str(client_request_id or ""),
        "command": str(command or ""),
        "user_request_text": str(public_user_message or user_message or ""),
        "conversation_source": str(conversation_source or ""),
        "round_id": str(run.run_id),
        "language": app_language(),
        "session_id": normalized_session_id,
        "ui_instance_id": str(ui_instance_id or ""),
        "workspace_dir": str(workspace_dir or ""),
        "soul_enabled": soul_enabled,
        "workspace_enabled": workspace_enabled,
        "permission_mode": normalized_permission_mode,
        "temporary_full_access": normalized_permission_mode == "full_access",
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

    def submit_background(awaitable: Awaitable[Any]):
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is owner_loop:
            return owner_loop.create_task(awaitable)
        return asyncio.run_coroutine_threadsafe(awaitable, owner_loop)

    def call_on_owner(callback: Callable[[], Any]) -> Any:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is owner_loop:
            return callback()
        result: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def invoke() -> None:
            try:
                result.set_result(callback())
            except BaseException as exc:  # propagate to the invoking Plugin
                result.set_exception(exc)

        owner_loop.call_soon_threadsafe(invoke)
        return result.result(timeout=30)

    def open_bridge() -> WorkbenchSessionBridge:
        registry, load_plugins = resolve_agent_plugin_registry(plugin_root)
        from agent.plugin import active_plugin_application_host

        application_host = active_plugin_application_host()
        plugin_services = (
            application_host.active_services
            if application_host is not None
            else {}
        )
        return WorkbenchSessionBridge.open(
            state_root,
            workspace_dir,
            plugin_root,
            registry=registry,
            load_plugins=load_plugins,
            model_plugin=MODEL_ROUTER_PLUGIN,
            chat_id=normalized_session_id,
            host_context={
                "bot": bot,
                "chat_id": host_chat_id,
                "db_path": str(db_path or ""),
                "notify_state": None,
            },
            plugin_context_data={
                "session_id": normalized_session_id,
                "project_id": str(project_id or ""),
                "project_memory_snapshot": (
                    deepcopy(dict(project_memory_snapshot))
                    if isinstance(project_memory_snapshot, Mapping)
                    else None
                ),
                "session_title": str(session_title or ""),
                "remote_device_ids": tuple(
                    str(item or "").strip()
                    for item in remote_device_ids
                    if str(item or "").strip()
                ),
                "soul_enabled": soul_enabled,
                "memory_write_enabled": bool(memory_write_enabled),
                "memory_trigger_enabled": bool(memory_trigger_enabled),
                "memory_archive_enabled": bool(memory_archive_enabled),
                "retry": bool(retry),
                "completed_turn_count": max(0, int(completed_turn_count or 0)),
                "background_submitter": submit_background,
                "owner_call": call_on_owner,
                "run_context": run_context,
            },
            plugin_services=plugin_services,
            max_model_calls=max_model_calls,
        )

    bridge = await asyncio.to_thread(open_bridge)

    async def publish(event: dict[str, Any]) -> None:
        await _publish_without_final_reply(run.publish, event)

    try:
        snapshot = bridge.snapshot()
        restored_run_id = str(snapshot.get("run_id") or "")
        if (
            str(snapshot.get("status") or "") == "idle"
            and restored_run_id == str(run.run_id)
        ):
            return bridge.completed_result(restored_run_id)
        if str(snapshot.get("status") or "") != "idle":
            if restored_run_id == str(run.run_id):
                return await bridge.resume_result(publish=publish)
            await bridge.cancel("superseded_by_new_workbench_run")
        turn_text = str(user_message or "").strip()
        if not turn_text:
            turn_text = str(public_user_message or "").strip()
        if not turn_text and str(command or "").strip():
            turn_text = f"/{str(command).strip()}"
        return await bridge.submit_result(
            turn_text,
            run_id=str(run.run_id),
            metadata={
                "client_request_id": str(client_request_id or ""),
                "public_user_message": str(public_user_message or ""),
                "public_attachments": [
                    dict(item) for item in (public_attachments or ())
                ],
                "command": str(command or ""),
                "retry": bool(retry),
                # This text is appended to the system prompt only for the
                # current turn. Persist it with that input so the conversation
                # panel can account for the same context the model received.
                "ephemeral_context": str(system_extra or ""),
            },
            publish=publish,
        )
    finally:
        await asyncio.to_thread(bridge.close)


__all__ = [
    "MODEL_ROUTER_PLUGIN",
    "ThreadsafeWorkbenchPublisher",
    "run_workbench_chat",
    "workbench_agent_data_directory",
]
