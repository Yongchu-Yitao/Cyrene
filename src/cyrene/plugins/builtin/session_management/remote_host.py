"""Local task ownership and records with execution pinned to a paired device."""
from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from .host import WorkbenchHost, KIND, OWNER


def task_request():
    from cyrene.core.plugin import application_plugin_service
    service = application_plugin_service("remote")
    if service is None:
        raise ConnectionError("工作设备不可用：远程服务未开启。")
    return service.request_task


def select_workspace(device, workspace):
    for project in device.get("projects") or []:
        root = str(project["workspace_path"])
        path_type = PureWindowsPath if PureWindowsPath(root).drive else PurePosixPath
        if ".." not in path_type(workspace).parts and path_type(workspace).is_relative_to(path_type(root)):
            return {**device, "project_id": project["id"], "workspace_path": workspace}
    raise ValueError("请使用所选工作设备上已共享的工作区路径。")


class RemoteAwareHost(WorkbenchHost):
    def reset_assistant(self, owner, store):
        for record in store.sessions(owner):
            if self.remote(record['id']) and self.status(record['id']) in {'running', 'awaiting_user', 'offline', 'dispatching'}:
                raise ValueError("请先停止远程子任务，再清空助理。")
        return super().reset_assistant(owner, store)

    def create(self, owner, session_id, title, workspace_path):
        existing = self.chat(session_id)
        if existing:
            if existing.get("kind") != KIND or existing.get(OWNER) != owner:
                raise ValueError("Session ID conflicts with another conversation")
            return existing
        source = self.chat(owner) or {}
        device = source.get("workDevice")
        if not device or not device.get("device_id"):
            return super().create(owner, session_id, title, workspace_path)
        chosen = select_workspace(device, workspace_path)
        port = self.port()
        chat = port.service.create_chat(source["projectId"], title, source.get("model", ""),
            soul_active=False, workspace_active=True, short_term_memory_active=False,
            project_memory_active=False, reasoning_effort=source.get("reasoningEffort", ""))
        chat.update(id=session_id, kind=KIND, permissionMode="auto", workDevice=chosen,
                    workspaceOverride=workspace_path, **{OWNER: owner})
        return port.service.repository.insert(chat)

    def remote(self, target):
        chat = self.chat(target) or {}
        return chat.get("workDevice") if chat.get("kind") == KIND else None

    def cached(self, target, node=""):
        return self.task_store.remote_snapshot(target, node)

    def checkpoint(self, target):
        return self.cached(target).get("checkpoint", {}) if self.remote(target) else super().checkpoint(target)

    def task_states(self, chats):
        local = {target: chat for target, chat in chats.items() if not chat.get("workDevice")}
        result = super().task_states(local) if local else {}
        for target, chat in chats.items():
            if chat.get("workDevice"):
                snapshot = self.cached(target)
                result[target] = (snapshot.get("checkpoint", {}), snapshot.get("status", "idle"))
        return result

    def status(self, target):
        if not self.remote(target):
            return super().status(target)
        return self.cached(target).get("status", "idle")

    def blocks(self, target):
        return self.cached(target).get("blocks", {"revision": "", "blocks": []}) if self.remote(target) else super().blocks(target)

    def latest_result(self, target, node_id=None):
        if not self.remote(target):
            return super().latest_result(target, node_id)
        snapshot = self.cached(target, node_id or "")
        return snapshot.get("result", {"status": snapshot.get("status", "not_found"), "revision": "", "content": ""})

    def notification(self, target, node_id):
        return self.cached(target, node_id).get("notification") if self.remote(target) else super().notification(target, node_id)

    async def remote_call(self, target, command, payload=None, key=""):
        device = self.remote(target)
        if not device:
            raise ValueError("Task no longer has a remote execution device")
        from cyrene.plugins.builtin.cyrene_remote.task_execution import retry_task_request
        return await retry_task_request(lambda: task_request()(device["device_id"], command,
            {"task_id": target, **(payload or {})}, project_id=device["project_id"], key=key))

    def recovered(self, target):
        notice = self.cached(target, "connection_notice")
        if notice.get("active"):
            self.task_store.save_remote_snapshot(target, {**notice, "active": False}, "connection_notice")
        self.port().service.repository.mutate_one(target, lambda c: c.update(workDeviceError=""))

    async def deliver(self, owner, target, message_id, payload):
        device = self.remote(target)
        if not device:
            return await super().deliver(owner, target, message_id, payload)
        chat = self.chat(target)
        if chat.get(OWNER) != owner or self.version(chat) != payload["version"]:
            raise ValueError("Target was cleared, replaced or changed ownership")
        # Persist the authoritative input before handing execution to the peer.
        def append(current):
            messages = current.setdefault("messages", [])
            if not any(m.get("clientRequestId") == message_id for m in messages):
                messages.append({"id": message_id, "role": "user", "content": payload["content"],
                    "attachments": payload["attachments"], "clientRequestId": message_id,
                    "createdAt": self.port().service.utc_now_iso()})
        self.port().service.repository.mutate_one(target, append)
        # Persist the exact transfer so retries/restarts keep the same idempotency fingerprint.
        transfer = self.cached(target, "delivery:" + message_id)
        if not transfer:
            attachments = []
            for item in payload["attachments"]:
                content = await asyncio.to_thread(Path(item["path"]).read_bytes)
                attachments.append({"name": item["name"], "content_type": item.get("content_type"),
                                    "content_base64": base64.b64encode(content).decode()})
            from cyrene.plugins.model_catalog import configured_context_limit
            history = [{"role": m.get("role"), "content": m.get("content", "")}
                       for m in chat.get("messages", []) if m.get("role") in {"user", "assistant"}
                       and m.get("clientRequestId") != message_id]
            transfer = {"model_context_limit": configured_context_limit(owner),
                "title": chat["title"], "workspace_path": device["workspace_path"],
                "message_id": message_id, "content": payload["content"], "attachments": attachments,
                "continuation": json.dumps({"messages": history, "blocks": self.cached(target).get("blocks", {})}, ensure_ascii=False)}
            self.task_store.save_remote_snapshot(target, transfer, "delivery:" + message_id)
        self.port().service.repository.mutate_one(target, lambda c: c.update(workDeviceStopped=False))
        # A missing receipt is an unknown execution, including the very first dispatch.
        self.task_store.save_remote_snapshot(target, {**self.cached(target), "status": "dispatching",
            "checkpoint": {}, "notification": None, "remote_released": False,
            "model_connection_error": "", "connection_error": "",
            "result": {"status": "running", "revision": "", "content": ""}})
        result = await self.remote_call(target, "tasks.deliver", transfer, key=message_id)
        if result["accepted"]:
            self.task_store.save_remote_snapshot(target, {**self.cached(target), "status": "running"})
            self.recovered(target)
        return result["accepted"]

    async def answer(self, owner, target, question_id, answer, request_id):
        if not self.remote(target):
            return await super().answer(owner, target, question_id, answer, request_id)
        if (self.chat(target) or {}).get(OWNER) != owner:
            raise ValueError("Task ownership changed")
        result = await self.remote_call(target, "tasks.answer", {
            "question_id": question_id, "answer": answer, "request_id": request_id}, key=request_id)
        self.task_store.save_remote_snapshot(target, {**self.cached(target), "status": "running",
            "checkpoint": {}, "notification": None, "model_connection_error": ""})
        self.recovered(target)
        return {**result, "session_id": target}

    async def stop(self, target):
        if not self.remote(target):
            return await super().stop(target)
        if self.cached(target).get("remote_released"):
            self.port().service.repository.mutate_one(target, lambda c: c.update(workDeviceStopped=True))
            return False
        result = await self.remote_call(target, "tasks.stop", key="task-stop-" + uuid4().hex)
        self.port().service.repository.mutate_one(target, lambda c: c.update(workDeviceStopped=True))
        # Acknowledged stop is distinct from pausing an undelivered local input.
        self.task_store.save_remote_snapshot(target, {**self.cached(target), "status": "stopped"})
        self.recovered(target)
        await self.refresh_task(target)
        return result["stopped"]

    async def refresh_task(self, target):
        previous = self.cached(target)
        snapshot = await self.remote_call(target, "tasks.snapshot", {"message_ids": self.task_store.queued_ids(target)})
        if snapshot.get("exists") is False:
            if self.cached(target) != previous:
                return  # A newer dispatch owns the execution state.
            self.task_store.save_remote_snapshot(target, {**self.cached(target), "remote_released": True,
                "status": self.cached(target).get("status") if self.cached(target).get("status") in {"completed", "failed", "cancelled", "stopped"} else "idle"})
            return
        chat = self.chat(target)
        if not chat:
            return
        device = chat["workDevice"]
        snapshot["work_device"] = device
        if snapshot.get("notification"):
            snapshot["notification"]["work_device"] = device
        # All user-visible history and final replies are committed locally.
        remote_chat = snapshot.pop("chat")
        snapshot["last_input_id"] = next((m.get("clientRequestId") for m in reversed(remote_chat.get("messages", []))
                                          if m.get("role") == "user"), None)
        messages = deepcopy(remote_chat.get("messages") or [])
        returned_attachments = []
        local_inputs = {m.get("clientRequestId"): m for m in chat.get("messages", [])
                        if m.get("role") == "user" and m.get("clientRequestId")}
        for message in messages:
            original = local_inputs.get(message.get("clientRequestId"))
            if message.get("role") == "user" and original:
                message.update(deepcopy(original))
                continue
            for attachment in message.get("attachments") or []:
                if attachment.get("id"):
                    local = await self.download_attachment(target, snapshot["chat_id"], attachment)
                    attachment.clear()
                    attachment.update(local)
                    if message.get("role") == "assistant":
                        returned_attachments.append(local)
        if snapshot.get("notification"):
            snapshot["notification"]["attachments"] = returned_attachments
        snapshot["result"]["attachments"] = returned_attachments
        if self.cached(target) != previous:
            return  # A reply/attachment transfer must not overwrite a concurrent update or stop.
        def merge(current):
            from cyrene.workbench.chat.chat_application import merge_chat_messages_chronologically
            merge_chat_messages_chronologically(current, messages)
            for field in ("pendingQuestion", "lastRun", "lastModel", "updatedAt", "usage", "completedTurnCount"):
                if field in remote_chat:
                    current[field] = remote_chat[field]
            current["status"] = snapshot["status"]
            current["workDeviceError"] = ""
        self.port().service.repository.mutate_one(target, merge)
        self.task_store.save_remote_snapshot(target, snapshot)
        for message_id in snapshot.get("admitted_message_ids", []):
            row = self.task_store.message(message_id)
            if row and row["target"] == target and row["status"] == "queued":
                self.task_store.update(message_id, "delivered")
        if not snapshot.get("model_connection_error"):
            self.recovered(target)
        from cyrene.workbench.chat.chat_events import publish_chat_changed
        await publish_chat_changed(target, chat["projectId"], "updated")

    async def download_attachment(self, target, remote_chat_id, attachment):
        key = "attachment:" + attachment["id"]
        cached = self.task_store.remote_snapshot(target, key)
        if cached:
            return cached
        from cyrene.plugins.builtin.cyrene_remote.status import _download_remote_file
        async def request(args, _context):
            return await self.remote_call(target, "attachments.read", {**args["payload"], "chat_id": remote_chat_id})
        result = await _download_remote_file({"payload": {"attachment_id": attachment["id"]}}, None, request_command=request)
        local = result["attachment"]
        self.task_store.save_remote_snapshot(target, local, key)
        return local

    async def refresh_remote(self, service):
        async def refresh(record):
            target = record["id"]
            snapshot = self.cached(target)
            if not self.remote(target) or not snapshot:
                return False
            active = not snapshot.get("remote_released") and self.status(target) in {"running", "awaiting_user", "offline", "dispatching", "stopped"}
            try:
                # Replay durable notifications even after a terminal snapshot was saved.
                await self.capture_cached_notification(service, record)
                if active:
                    await self.refresh_task(target)
                    await self.capture_cached_notification(service, record)
                snapshot = self.cached(target)
                if (snapshot.get("status") in {"completed", "failed", "cancelled"}
                        and not snapshot.get("remote_released") and not self.task_store.queued_ids(target)):
                    active = True
                    result = await self.remote_call(target, "tasks.release", {
                        "node_id": snapshot.get("checkpoint", {}).get("node_id"),
                        "last_input_id": snapshot.get("last_input_id")},
                        key="release:" + target + ":" + str(snapshot.get("checkpoint", {}).get("node_id")))
                    if result.get("released") and self.cached(target) == snapshot:
                        self.task_store.save_remote_snapshot(target, {**snapshot, "remote_released": True})
                        if not snapshot.get("model_connection_error"):
                            self.recovered(target)
                        active = False
            except (ConnectionError, TimeoutError) as exc:
                service.work_device_unavailable(record["owner"], target, str(exc))
                await service.notify_tasks()
            except Exception as exc:
                active = True  # Retry local persistence/publication failures, including after completion.
                self.port().service.repository.mutate_one(target, lambda c, error=str(exc): c.update(workDeviceError=error))
                if self.cached(target).get("connection_error") != str(exc):
                    self.task_store.save_remote_snapshot(target, {**self.cached(target), "connection_error": str(exc)})
                    service.delivery_failed({"id": "offline:" + target, "owner": record["owner"], "target": target}, str(exc))
            return active

        # One offline device must not delay another device's notification.
        results = await asyncio.gather(*(refresh(record) for record in self.task_store.all_sessions()))
        return any(results)

    async def capture_cached_notification(self, service, record):
        target = record["id"]
        snapshot = self.cached(target)
        if (self.chat(target) or {}).get("workDeviceStopped"):
            return
        if snapshot.get("model_connection_error"):
            service.work_device_unavailable(record["owner"], target, snapshot["model_connection_error"])
        elif snapshot.get("checkpoint", {}).get("node_id"):
            await service.capture_notification(target, snapshot["checkpoint"]["node_id"])
