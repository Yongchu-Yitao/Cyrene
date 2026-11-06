"""Personal assistant entry and sidebar; conversations use the existing chat API."""

import asyncio
from fastapi import HTTPException
from cyrene.workbench.assistant import assistant_scope


def register_routes(context, service):
    router = context.router

    resolved_id = None

    async def ensure(refresh=False):
        async def create():
            nonlocal resolved_id
            async with service.lock:
                if resolved_id and not refresh:
                    chat = service.host.chat(resolved_id)
                    if chat:
                        return chat
                chat = await asyncio.to_thread(service.initialize_assistant)
                resolved_id = chat["id"]
                return chat

        return await service._on_owner(create)

    @router.post("/api/workbench/assistant")
    async def open_assistant():
        chat = await ensure(refresh=True)
        return {"chat": service.host.port().service.public_chat_light(chat)}

    @router.get("/api/workbench/assistant")
    async def sidebar():
        chat = await ensure()
        target = chat["id"]
        sessions = (await service.list(target))["sessions"]
        snapshot = await asyncio.to_thread(service.host.blocks, target)
        blocks = [{k: v for k, v in block.items() if k != "content"} for block in snapshot["blocks"] if block["kind"] == "task"]
        chats = [chat] + [service.host.chat(s["session_id"]) for s in sessions]
        return {"scope": assistant_scope(), "assistant_id": target, "sessions": sessions, "blocks": blocks, "chats": [service.host.port().service.public_chat_light(c) for c in chats if c]}

    @router.post("/api/workbench/assistant/load/{context_id}")
    async def load_context(context_id: str):
        target = (await ensure())["id"]
        snapshot = await asyncio.to_thread(service.host.blocks, target)
        block = next((b for b in snapshot["blocks"] if b["block_id"] == "task:" + context_id), None)
        if not block or context_id == "shared":
            raise HTTPException(400, "Select a task context")
        port = service.host.port()
        if service.host.status(target) == "awaiting_user":
            raise HTTPException(409, "Answer the pending question first")
        message = "请切换到我选择的上下文块 " + repr(context_id) + "。如有其他活动上下文，先保存摘要并卸载，再加载所选块。简要告诉我当前进展。"
        if port.run_manager.get(target):
            return await port.guide(target, {"message": message})
        return await port.send(target, {"message": message, "stream": True})
