"""Personal assistant entry and sidebar; conversations use the existing chat API."""

import asyncio
from cyrene.workbench.projects.assistant import assistant_scope


def register_routes(context, service):
    router = context.router

    async def ensure():
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
