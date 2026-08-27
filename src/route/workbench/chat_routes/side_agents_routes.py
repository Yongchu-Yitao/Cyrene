from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext


def register_side_agents_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _chat_soul_active = service.chat_soul_active
    _chat_workspace_active = service.chat_workspace_active
    _find_chat = service.repository.find
    _new_chat = service.create_chat
    _public_chat_full = service.public_chat_full
    _read_chats_store = service.repository.read
    _write_chats_store = service.repository.write

    @router.get("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_list_side_agents(chat_id: str):
        payload = await asyncio.to_thread(_read_chats_store)
        parent = _find_chat(payload, chat_id)
        if not parent:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        agents = [_public_chat_full(item) for item in payload.get("chats", []) if str(item.get("kind") or "") == "side-agent" and str(item.get("parentChatId") or "") == chat_id]
        agents.sort(key=lambda item: str(item.get("createdAt") or ""))
        return {"agents": agents}

    @router.post("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_create_side_agent(chat_id: str, body_model: api_models.SideAgentCreateBody):
        body = api_models.body_dict(body_model)
        quote = str(body.get("quote") or "").strip()
        if not quote:
            return JSONResponse({"error": "quote is required"}, status_code=400)

        def create_and_persist() -> dict[str, Any] | None:
            payload = _read_chats_store()
            parent = _find_chat(payload, chat_id)
            if not parent:
                return None
            compact_quote = re.sub(r"\s+", " ", quote)
            title = str(body.get("title") or "").strip() or compact_quote[:28]
            agent = _new_chat(
                str(parent.get("projectId") or ""),
                title or "侧边提问",
                str(parent.get("model") or ""),
                project_memory_snapshot=(dict(parent.get("projectMemorySnapshot") or {}) if isinstance(parent.get("projectMemorySnapshot"), dict) else None),
            )
            agent["kind"] = "side-agent"
            agent["parentChatId"] = chat_id
            agent["sourceQuote"] = quote[:12_000]
            if parent.get("workspaceOverride"):
                agent["workspaceOverride"] = str(parent["workspaceOverride"])
            agent["soulActive"] = _chat_soul_active(parent)
            agent["workspaceActive"] = _chat_workspace_active(parent)
            if parent.get("reasoningEffort"):
                agent["reasoningEffort"] = str(parent["reasoningEffort"])
            payload.setdefault("chats", []).insert(0, agent)
            _write_chats_store(payload)
            return agent

        agent = await asyncio.to_thread(create_and_persist)
        if not agent:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        await publish_chat_changed(
            chat_id,
            str(agent.get("projectId") or ""),
            "side_agent_created",
            side_agent_id=str(agent.get("id") or ""),
        )
        return {"ok": True, "agent": _public_chat_full(agent)}
