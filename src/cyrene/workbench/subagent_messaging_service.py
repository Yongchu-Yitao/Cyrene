"""Application service for user-to-subagent collaboration messages."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cyrene import agent as agent_service
from cyrene import subagent
from cyrene.observability import debug
from cyrene.runtime.inbox import clear_inbox, send_message


_TERMINAL_STATUSES = frozenset({"done", "timeout", "incomplete"})


@dataclass(frozen=True)
class AgentBroadcastCommand:
    round_id: str
    text: str
    mentions: list[str] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentMentionCommand:
    message: str
    mentions: list[str]
    public_attachments: list[dict[str, Any]] = field(default_factory=list)
    client_request_id: str = ""


class SubagentMessagingService:
    """Own subagent target selection, inbox delivery, and reactivation."""

    def __init__(self, bot: Any, db_path: str, *, chat_id: int = -1):
        self.bot = bot
        self.db_path = str(db_path)
        self.chat_id = chat_id

    async def group_chat_messages(self, round_id: str) -> dict[str, Any]:
        if not round_id:
            return {"messages": [], "agents": []}
        return await subagent.build_group_chat_messages(round_id)

    async def broadcast(self, command: AgentBroadcastCommand) -> dict[str, Any]:
        if not command.round_id or not command.text:
            return {"ok": False, "error": "round_id and text are required"}
        targets = await self._broadcast_targets(command)
        if not targets:
            return {"ok": False, "error": "No target agents found"}
        full_text = _message_with_attachments(command.text, command.attachments)
        sent_to, first_message_id = await self._deliver_broadcast(
            targets, command.round_id, full_text
        )
        await self._publish_user_message(command, first_message_id)
        return {"ok": True, "sent_to": sent_to}

    async def send_mentions(self, command: AgentMentionCommand) -> dict[str, Any]:
        valid_mentions: list[str] = []
        for raw_agent_id in command.mentions:
            agent_id = str(raw_agent_id).strip()
            info = subagent._registry.get(agent_id) if agent_id else None
            if info is None:
                continue
            valid_mentions.append(agent_id)
            await self._send_mention(agent_id, info, command.message)
        if not valid_mentions:
            raise ValueError("none of the mentioned agents exist")
        await self._persist_mention(command, valid_mentions)
        names = ", ".join(f"@{agent_id}" for agent_id in valid_mentions)
        return {"response": f"Message sent to {names}."}

    def list_subagents(self, session_id: str = "") -> list[dict[str, Any]]:
        return [
            {
                "id": agent_id,
                "name": agent_id,
                "task": info.get("task", ""),
                "status": info.get("status", "running"),
                "result": info.get("result", ""),
            }
            for agent_id, info in subagent._registry.items()
            if not session_id or str(info.get("session_id", "")) == session_id
        ]

    async def _broadcast_targets(
        self, command: AgentBroadcastCommand
    ) -> list[str]:
        if isinstance(command.mentions, list) and command.mentions:
            return [
                str(agent_id).strip()
                for agent_id in command.mentions
                if str(agent_id).strip()
            ]
        async with subagent._lock:
            return [
                agent_id
                for agent_id, info in subagent._registry.items()
                if str(info.get("round_id", "") or "").strip()
                == command.round_id
                and agent_id != "main"
            ]

    async def _deliver_broadcast(
        self, targets: list[str], round_id: str, full_text: str
    ) -> tuple[list[str], str]:
        sent_to: list[str] = []
        first_message_id = ""
        for target in targets:
            info = subagent._registry.get(target)
            terminal = bool(info) and _status(info) in _TERMINAL_STATUSES
            await clear_inbox(target)
            message_id = await send_message(
                "user", target, "guidance", _guidance_text(full_text, terminal),
                round_id=round_id,
            )
            if not message_id:
                continue
            sent_to.append(target)
            first_message_id = first_message_id or message_id
            if terminal and info is not None:
                await self._reactivate(target, info)
        return sent_to, first_message_id

    async def _send_mention(
        self, agent_id: str, info: dict[str, Any], message: str
    ) -> None:
        terminal = _status(info) in _TERMINAL_STATUSES
        await send_message(
            "user", agent_id, "guidance", _guidance_text(message, terminal)
        )
        if terminal:
            await self._reactivate(agent_id, info)

    async def _reactivate(self, agent_id: str, info: dict[str, Any]) -> None:
        if not await subagent.reactivate(agent_id):
            return
        raw_messages = await subagent.get_raw_messages(agent_id)
        task = subagent._run_subagent(
            agent_id,
            str(info.get("task") or ""),
            self.bot,
            self.chat_id,
            self.db_path,
            resume_messages=raw_messages,
        )
        subagent._spawn_subagent_task(task, agent_id)

    async def _persist_mention(
        self, command: AgentMentionCommand, mentions: list[str]
    ) -> None:
        prefix = " ".join(f"@{agent_id}" for agent_id in mentions) + " "
        entry: dict[str, Any] = {
            "role": "user",
            "content": prefix + command.message,
            "mentions": mentions,
        }
        if command.public_attachments:
            entry["attachments"] = command.public_attachments
        if command.client_request_id:
            entry["client_request_id"] = command.client_request_id
        await agent_service._append_session_message(entry)

    async def _publish_user_message(
        self, command: AgentBroadcastCommand, message_id: str
    ) -> None:
        mentions = command.mentions or []
        await debug.publish_event(
            {
                "type": "agent_chat_user_message",
                "round_id": command.round_id,
                "message": {
                    "id": message_id or f"user_msg_{int(time.time() * 1000)}",
                    "type": "user_message",
                    "from": "user",
                    "to": "all" if not mentions else ",".join(mentions),
                    "content": command.text,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "round_id": command.round_id,
                },
            }
        )


def _status(info: dict[str, Any]) -> str:
    return str(info.get("status", "")).strip()


def _guidance_text(text: str, terminal: bool) -> str:
    if terminal:
        return (
            "User sent you a new task. This is a round — complete it and "
            f"report your result via quit.\n\n{text}"
        )
    return (
        "[DIRECT_MESSAGE]\nThe user has sent you guidance. This takes "
        "priority over your current approach — adjust your work accordingly. "
        "Use send_message_to_user ONCE to acknowledge and briefly say what you "
        "will change. Then continue working with the adjusted approach.\n\n"
        f"User guidance:\n{text}"
    )


def _message_with_attachments(
    text: str, attachments: list[dict[str, Any]]
) -> str:
    result = text
    for attachment in attachments:
        path = str(attachment.get("path", "") or "").strip()
        name = str(attachment.get("name", "") or "").strip()
        if path:
            result += f"\n\n[{name}]({path})" if name else f"\n\n{path}"
    return result


__all__ = [
    "AgentBroadcastCommand",
    "AgentMentionCommand",
    "SubagentMessagingService",
]
