"""Personal assistant entry and sidebar; conversations use the existing chat API."""

import asyncio
from fastapi import Body
from fastapi.responses import JSONResponse
from cyrene.core.plugin import application_plugin_service
from cyrene.workbench.projects.assistant import assistant_scope


def register_routes(context, service):
    router = context.router

    async def ensure():
        from cyrene.workbench.projects.assistant import ASSISTANT_ID
        existing = service.host.chat(ASSISTANT_ID)
        if existing:
            return existing  # Listing devices must not wait behind task execution's lock.
        async def create():
            async with service.lock:
                return await asyncio.to_thread(service.initialize_assistant)

        return await service._on_owner(create)

    @router.get("/api/workbench/assistant")
    async def sidebar():
        chat = await ensure()
        target = chat["id"]
        sessions = (await service.list(target))["sessions"]
        chats = [chat] + [service.host.chat(s["session_id"]) for s in sessions]
        return {"scope": assistant_scope(), "assistant_id": target, "sessions": sessions, "chats": [service.host.port().service.public_chat_light(c) for c in chats if c]}

    @router.post("/api/workbench/assistant/reset")
    async def reset():
        from cyrene.workbench.http.agent.sessions import session_call

        return await session_call(service.reset_assistant)

    @router.get("/api/workbench/assistant/work-device")
    async def work_devices(device_id: str = ""):
        remote = application_plugin_service("remote")
        if device_id:
            peer = await remote.check_task_device(device_id) if remote else {
                "device_id": device_id, "task_eligible": False, "task_state": "offline", "task_error": "离线"}
            return {"peer": peer}
        chat = await ensure()
        devices = await remote.task_devices() if remote else {"device_name": "当前设备", "peers": [], "enabled": False}
        return {**devices, "work_device": chat.get("workDevice")}

    @router.put("/api/workbench/assistant/work-device")
    async def select_work_device(body: dict = Body(...)):
        chat = await ensure()
        device_id = str(body.get("device_id") or "")
        try:
            remote = application_plugin_service("remote")
            if device_id and device_id != "local" and remote is None:
                raise ValueError("远程设备服务不可用。")
            device = await remote.describe_work_device(device_id) if device_id and device_id != "local" else None
            async def save():
                async with service.lock:
                    return await asyncio.to_thread(service.host.port().service.repository.mutate_one,
                        chat["id"], lambda current: current.update(workDevice=device))
            await service._on_owner(save)
        except (ConnectionError, TimeoutError):
            return JSONResponse({"error": "设备离线，无法切换工作设备。"}, status_code=409)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"work_device": device}
