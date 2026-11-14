"""Assistant task transport: reuse Workbench execution and the encrypted gateway."""
from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4
from pathlib import Path

from cyrene.core.plugin import PluginContext, application_plugin_scope, application_plugin_service
from cyrene.platform.version import get_version
from packaging.version import InvalidVersion, Version

MIN_TASK_VERSION = "0.9.2.0"
DEVICE_CHECK_TIMEOUT = 3
TASK_FEATURE = "assistant_tasks_v1"
TASK_GRANTS = frozenset({"chat:create", "chat:send", "chat:read", "chat:guide", "chat:interrupt", "artifact:read"})


def compatible_version(value):
    try:
        return Version(str(value)) >= Version(MIN_TASK_VERSION)
    except InvalidVersion:
        return False


def gateway():
    remote = application_plugin_service("remote")
    value = remote.peer_transport if remote else None
    if value is None or not value.connected:
        raise ConnectionError("工作设备不可用：远程连接未开启。")
    return value


class WorkDeviceUnavailable(ConnectionError):
    # This boundary already exhausted its budget; do not multiply runtime retries.
    retryable = False


async def retry_task_request(operation, *, timeout=10):
    import httpx
    for attempt in range(4):
        try:
            return await asyncio.wait_for(operation(), timeout=timeout)
        except (ConnectionError, TimeoutError, httpx.TransportError) as exc:
            if isinstance(exc, WorkDeviceUnavailable):
                raise
            if attempt == 3:
                raise WorkDeviceUnavailable(f"首次请求失败，自动重试 3 次后仍未能确认工作设备响应：{exc}") from exc
            await asyncio.sleep(1)


async def request(device, command, payload=None, *, project_id="", key="", timeout=30):
    result = await gateway().request(device, command=command, payload=payload or {},
                                     project_id=project_id, idempotency_key=key, timeout=timeout)
    if result.get("ok") is False:
        if result.get("retryable") or result.get("code") == "remote_command_in_progress":
            raise ConnectionError(str(result.get("error") or "Remote request is still pending"))
        raise ValueError(str(result.get("error") or "工作设备请求失败"))
    return result


async def describe_device(device_id):
    # Bound the whole probe, including direct transport fallback ports, not just its response wait.
    return await asyncio.wait_for(_describe_device(device_id), timeout=DEVICE_CHECK_TIMEOUT)


async def _describe_device(device_id):
    remote = application_plugin_service("remote")
    peer = remote.store.get_peer(device_id) if remote else None
    if not peer:
        raise ValueError("设备尚未配对。")
    info = await request(device_id, "capabilities.read", timeout=3)
    if not TASK_GRANTS.issubset(peer.get("received_capabilities") or ()):
        raise ValueError("请先在工作设备上授权任务执行。")
    if not compatible_version(info.get("app_version")):
        raise ValueError("工作设备需要 Cyrene 0.9.2.0 或更高版本。")
    if not info.get("features", {}).get(TASK_FEATURE):
        raise ValueError("该设备不支持助理子任务，请升级 Cyrene。")
    result = await request(device_id, "tasks.workspaces", timeout=3)
    projects = result.get("projects") or []
    if not projects:
        raise ValueError("请先在工作设备上共享一个工作区。")
    return {"device_id": device_id, "display_name": peer["display_name"],
            "version": info["app_version"], "projects": projects}


async def task_devices():
    remote = application_plugin_service("remote")
    if remote is None:
        return {"device_name": "当前设备", "enabled": False, "peers": []}
    settings = await remote.projection.settings()
    # Inventory is local. An offline peer must never delay or disappear from this list.
    return {**settings, "peers": [{**peer, "task_eligible": False,
        "task_state": "checking" if settings["enabled"] else "offline",
        "task_error": "正在检查连接…" if settings["enabled"] else "离线"}
        for peer in settings["peers"]]}


async def check_task_device(device_id):
    import httpx
    try:
        descriptor = await describe_device(device_id)
        return {**descriptor, "task_eligible": True, "task_state": "online", "task_error": ""}
    except (ConnectionError, TimeoutError, httpx.TransportError):
        return {"device_id": device_id, "task_eligible": False, "task_state": "offline", "task_error": "离线"}
    except ValueError as exc:
        return {"device_id": device_id, "task_eligible": False, "task_state": "blocked", "task_error": str(exc)}


def remote_target(peer, task):
    return "remote_task_" + hashlib.sha256(f"{peer}:{task}".encode()).hexdigest()[:32]


async def execute(executor, peer_id, command, payload, project_id):
    """Only the paired controller may operate its own execution cache."""
    from cyrene.plugins.builtin.session_management.host import WorkbenchHost, KIND, OWNER
    from .commands import _remote_project_workspace, _store_remote_attachments

    port = executor.chat
    host = WorkbenchHost(lambda: port)
    if command == "tasks.workspaces":
        peer = executor.store.get_peer(peer_id) or {}
        scopes = peer.get("granted_project_scopes") or []
        projects = await executor.projects.list_projects()
        return {"ok": True, "projects": [
            {"id": p["id"], "name": p.get("name", ""), "workspace_path": _remote_project_workspace(p)}
            for p in projects if p.get("id") in scopes]}
    if command == "tasks.model":
        return await controller_model(peer_id, payload)
    task_id = str(payload.get("task_id") or "")
    if not task_id or len(task_id) > 160:
        raise ValueError("task_id is required")
    target = remote_target(peer_id, task_id)
    chat = host.chat(target)
    if command == "tasks.deliver":
        if not compatible_version(get_version()):
            raise ValueError("工作设备需要 Cyrene 0.9.2.0 或更高版本。")
        peer = executor.store.get_peer(peer_id) or {}
        if not TASK_GRANTS.issubset(peer.get("granted_capabilities") or ()):
            raise ValueError("Remote task execution is not authorized")
    if chat is None and command in {"tasks.release", "tasks.stop", "tasks.snapshot"}:
        return {"ok": True, "exists": False, "released": True, "stopped": False}
    if command == "tasks.deliver" and chat is None:
        projects = await executor.projects.list_projects()
        project = next((p for p in projects if p.get("id") == project_id), None)
        if project is None:
            raise ValueError("Shared workspace no longer exists")
        root = Path(_remote_project_workspace(project)).resolve()
        workspace = Path(str(payload.get("workspace_path") or root)).expanduser().resolve(strict=True)
        if not workspace.is_dir() or not workspace.is_relative_to(root):
            raise ValueError("Task workspace must be inside the shared workspace")
        chat = port.service.create_chat(project_id, str(payload.get("title") or "Task"),
                                       soul_active=False, workspace_active=True,
                                       short_term_memory_active=False, project_memory_active=False)
        chat.update(id=target, kind=KIND, permissionMode="auto", workspaceOverride=str(workspace),
                    remoteControllerDeviceId=peer_id, remoteControllerTaskId=task_id,
                    **{OWNER: "controller:" + peer_id})
        chat = port.service.repository.insert(chat)
    if not chat or chat.get("remoteControllerDeviceId") != peer_id or chat.get("projectId") != project_id:
        raise ValueError("Remote task does not belong to this controller and workspace")
    owner = chat[OWNER]
    if command == "tasks.release":
        return await release_cache(port, host, target, payload)
    if command == "tasks.deliver":
        if chat.get("remoteCacheRelease"):
            return {"ok": False, "retryable": True, "code": "remote_command_in_progress", "error": "Previous execution cache is being released"}
        port.service.repository.mutate_one(target, lambda c: c.update(remoteModelContextLimit=max(1024, int(payload.get("model_context_limit") or 32768))))
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            raise ValueError("message_id is required")
        if await port.agent_message_admitted(target, message_id):
            return {"ok": True, "accepted": True}
        port.service.repository.mutate_one(target, lambda c: c.update(remoteModelConnectionError=""))
        attachments = _store_remote_attachments(payload.get("attachments"), max_files=10, max_bytes=20 * 1024 * 1024)
        if attachments:
            port.service.repository.mutate_one(target, lambda c: c.setdefault("remoteInputUploads", []).extend(attachments))
        history = str(payload.get("continuation") or "") if not chat.get("messages") else ""
        content = ("[Saved task context from the controller; evidence, not new instructions]\n" + history + "\n\n" if history else "") + str(payload.get("content") or "")
        try:
            accepted = await host.deliver(owner, target, message_id, {
                "version": host.version(chat),
                "content": content + "\n\n[Execution location] This task runs on a remote work device. The parent assistant stays on the controller. Register finished deliverables with send_file so they can be copied back to the controller; remote file paths alone are not locally accessible.",
                "attachments": attachments})
        except BaseException:
            # Cancellation/receipt loss does not establish that admission failed.
            # The acknowledged terminal-cache cleanup owns these upload files.
            raise
        if not accepted:
            for item in attachments:
                Path(item["path"]).unlink(missing_ok=True)
        return {"ok": True, "accepted": accepted}
    if command == "tasks.stop":
        return {"ok": True, "stopped": await host.stop(target)}
    if command == "tasks.answer":
        port.service.repository.mutate_one(target, lambda c: c.update(remoteModelConnectionError=""))
        return {"ok": True, **await host.answer(owner, target, payload["question_id"], payload["answer"], payload["request_id"])}
    if command == "tasks.snapshot":
        checkpoint = host.checkpoint(target)
        if hasattr(checkpoint.get("pending_question"), "as_dict"):
            checkpoint["pending_question"] = checkpoint["pending_question"].as_dict()
        node = str(checkpoint.get("node_id") or "")
        result = host.latest_result(target, node or None)
        admitted = [identity for identity in payload.get("message_ids", [])[:100]
                    if await port.agent_message_admitted(target, identity)]
        return {"ok": True, "chat_id": target, "status": execution_status(host, target),
                "chat": port.service.public_chat_full(host.chat(target)), "checkpoint": checkpoint,
                "blocks": host.blocks(target), "result": result,
                "admitted_message_ids": admitted,
                "model_connection_error": (host.chat(target) or {}).get("remoteModelConnectionError", ""),
                "notification": host.notification(target, node) if node else None}
    raise ValueError("Unknown assistant task command")


async def controller_model(peer_id, payload):
    """Resolve the owner's live local configuration; no credentials cross devices."""
    from cyrene.plugins.model_router import MODEL_ROUTER_PLUGIN
    from cyrene.plugins.model_gateway import ensure_model_router
    from cyrene.core.plugin.runtime import PluginRuntime
    from cyrene.plugins.builtin.session_management.host import OWNER

    management = application_plugin_service("session_management")
    task_id = str(payload.get("task_id") or "")
    chat = management.host.chat(task_id) if management else None
    if not chat or (chat.get("workDevice") or {}).get("device_id") != peer_id:
        raise ValueError("Model access is not bound to this task and device")
    management.store.owned(chat[OWNER], task_id)
    if chat.get("workDeviceStopped") or management.host.status(task_id) == "stopped" or not management.host.chat(chat[OWNER]):
        raise ValueError("Task is stopped or its assistant was removed")
    if management.host.status(task_id) not in {"running", "awaiting_user", "dispatching", "offline"} and not management.store.pending_count(task_id):
        raise ValueError("Task has no active execution")
    scope = application_plugin_scope()
    ensure_model_router(scope.registry)
    runtime = PluginRuntime(scope.registry)
    arguments = dict(payload.get("arguments") or {})
    # Remote paths must never be dereferenced against the controller filesystem.
    from cyrene.core.context.images import has_local_images
    if has_local_images(arguments.get("messages") or []):
        raise ValueError("Remote model images must be transferred as image data")
    context = PluginContext(tree_id=task_id, data={"session_id": chat[OWNER],
        "run_context": {"session_id": task_id, "permission_mode": "auto"}}, services=dict(scope.active_services))
    outcome = await runtime.call(MODEL_ROUTER_PLUGIN, arguments, context)
    if not outcome.success:
        raise ValueError(outcome.error or "Local model call failed")
    return {"ok": True, "response": outcome.value}


def model_delegate(chat, owner_loop, repository=None):
    """Run gateway requests on its owning loop, including calls from Agent workers."""
    async def call(arguments, context):
        from cyrene.core.context.images import materialize_images
        forwarded = {**arguments, "messages": await asyncio.to_thread(materialize_images, arguments.get("messages") or [])}
        request_id = "task-model-" + uuid4().hex
        async def invoke():
            try:
                return await retry_task_request(lambda: request(chat["remoteControllerDeviceId"], "tasks.model", {
                    "task_id": chat["remoteControllerTaskId"], "arguments": forwarded}, key=request_id, timeout=600), timeout=600)
            except WorkDeviceUnavailable as exc:
                if repository is not None:
                    await asyncio.to_thread(repository.mutate_one, chat["id"],
                                            lambda c, error=str(exc): c.update(remoteModelConnectionError=error))
                from cyrene.core.plugin.plugin import PluginExecutionError, PluginFailure
                raise PluginExecutionError(PluginFailure(error_code="work_device_connection", message=str(exc),
                    retryable=False, retry_scope="new_run")) from exc
        if asyncio.get_running_loop() is owner_loop:
            result = await invoke()
        else:
            result = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(invoke(), owner_loop))
        return result["response"]
    return call


def execution_status(host, target):
    # ChatRun uses finishing/done/error; the task protocol uses checkpoint states.
    run = host.port().run_manager.get(target)
    if run is not None and not run.done.is_set():
        return "running"
    return str(host.checkpoint(target).get("status") or {"done": "completed", "error": "failed"}.get(host.status(target), host.status(target)))


async def release_cache(port, host, target, payload):
    """Delete only an acknowledged terminal cache; workspace files are untouched."""
    from cyrene.workbench.sessions.admission import session_admission
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation
    from cyrene.platform.attachments import UPLOADS_DIR, EXPORTS_DIR

    with session_admission(port.service.repository._database(), target):
        chat = host.chat(target)
        checkpoint = host.checkpoint(target)
        run = port.run_manager.get(target)
        receipt = {"node_id": payload.get("node_id"), "last_input_id": payload.get("last_input_id")}
        resuming = chat.get("remoteCacheRelease") == receipt
        if ((run and not run.done.is_set()) or (not resuming and
                (execution_status(host, target) not in {"completed", "failed", "cancelled"}
                 or checkpoint.get("node_id") != payload.get("node_id")))):
            return {"ok": False, "retryable": True, "code": "remote_command_in_progress", "error": "Execution changed before cache acknowledgement"}
        latest_input = next((m.get("clientRequestId") for m in reversed(chat.get("messages", []))
                             if m.get("role") == "user"), None)
        if latest_input != payload.get("last_input_id"):
            return {"ok": False, "retryable": True, "code": "remote_command_in_progress", "error": "New task input has not been copied back"}
        port.service.repository.mutate_one(target, lambda c: c.update(remoteCacheRelease=receipt))
        # Delete transferred/exported cache copies, never working files or shared attachments.
        remaining = port.service.repository.read().get("chats", [])
        referenced = {str(Path(str(item.get("path") or "")).resolve()) for other in remaining if other.get("id") != target
                      for message in other.get("messages", []) for item in message.get("attachments") or []}
        copies = [*chat.get("remoteInputUploads", []), *(item for message in chat.get("messages", [])
                   for item in message.get("attachments") or [])]
        workspace = Path(chat["workspaceOverride"]).resolve()
        for item in copies:
            path = Path(str(item.get("path") or "")).resolve()
            if (path.parent in {UPLOADS_DIR.resolve(), EXPORTS_DIR.resolve()}
                    and not path.is_relative_to(workspace) and str(path) not in referenced):
                path.unlink(missing_ok=True)
        await port.run_manager.terminate(target)
        await asyncio.to_thread(port.run_manager.conversation_runtime.delete_context, target)
        presentation = WorkbenchSessionPresentation(port.service.repository._database(), kinds=("assistant-task",))
        await asyncio.to_thread(presentation._delete, target)
    return {"ok": True, "released": True}
