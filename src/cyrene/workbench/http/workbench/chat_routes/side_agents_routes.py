from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter

from cyrene.localization import app_language, localized
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext


def register_side_agents_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _chat_soul_active = service.chat_soul_active
    _chat_workspace_active = service.chat_workspace_active
    _chat_short_term_memory_active = service.chat_short_term_memory_active
    _chat_project_memory_active = service.chat_project_memory_active
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
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        agents = [_public_chat_full(item) for item in payload.get("chats", []) if str(item.get("kind") or "") == "side-agent" and str(item.get("parentChatId") or "") == chat_id]
        agents.sort(key=lambda item: str(item.get("createdAt") or ""))
        return {"agents": agents}

    @router.post("/api/workbench/chats/{chat_id}/side-agents")
    async def api_workbench_create_side_agent(chat_id: str, body_model: api_models.SideAgentCreateBody):
        body = api_models.body_dict(body_model)
        quote = str(body.get("quote") or "").strip()
        if not quote:
            return localized_error_response(
                "A quoted passage is required.",
                "请选择要引用的内容。",
                400,
                "quote_required",
            )
        language = app_language()

        def create_and_persist() -> dict[str, Any] | None:
            payload = _read_chats_store()
            parent = _find_chat(payload, chat_id)
            if not parent:
                return None
            compact_quote = re.sub(r"\s+", " ", quote)
            title = str(body.get("title") or "").strip() or compact_quote[:28]
            agent = _new_chat(
                str(parent.get("projectId") or ""),
                title or localized(
                    "Side question", "侧边提问", language=language
                ),
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
            agent["shortTermMemoryActive"] = _chat_short_term_memory_active(parent)
            agent["projectMemoryActive"] = _chat_project_memory_active(parent)
            agent["contextActivations"] = dict(
                parent.get("contextActivations") or {}
            )
            agent["remoteDeviceIds"] = list(
                parent.get("remoteDeviceIds") or ()
            )
            if parent.get("reasoningEffort"):
                agent["reasoningEffort"] = str(parent["reasoningEffort"])
            payload.setdefault("chats", []).insert(0, agent)
            _write_chats_store(payload)
            return agent

        agent = await asyncio.to_thread(create_and_persist)
        if not agent:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        await publish_chat_changed(
            chat_id,
            str(agent.get("projectId") or ""),
            "side_agent_created",
            side_agent_id=str(agent.get("id") or ""),
        )
        return {"ok": True, "agent": _public_chat_full(agent)}
