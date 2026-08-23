"""Thin HTTP adapters for agent collaboration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from cyrene.workbench.subagent_messaging_service import (
    AgentBroadcastCommand,
    SubagentMessagingService,
)


def register_collaboration_routes(
    router: APIRouter,
    service: SubagentMessagingService,
) -> None:
    @router.get("/api/chat/agent-chat-messages")
    async def api_agent_chat_messages(round_id: str = ""):
        return await service.group_chat_messages(round_id)

    @router.post("/api/chat/send-to-agents")
    async def api_send_to_agents(body: dict[str, Any]):
        mentions = body.get("mentions")
        attachments = body.get("attachments") or []
        return await service.broadcast(
            AgentBroadcastCommand(
                round_id=str(body.get("round_id", "") or "").strip(),
                text=str(body.get("text", "") or "").strip(),
                mentions=(
                    [str(item) for item in mentions]
                    if isinstance(mentions, list)
                    else None
                ),
                attachments=(list(attachments) if isinstance(attachments, list) else []),
            )
        )


__all__ = ["register_collaboration_routes"]
