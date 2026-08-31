"""Public conversation commit event shared by Workbench and Core Plugins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cyrene.core.hook import CONVERSATION_TURN_COMMITTED


@dataclass(frozen=True, slots=True)
class ConversationTurnCommit:
    """One public turn projection accepted by the Workbench database."""

    chat_id: str
    turn_id: str
    run_id: str
    node_id: str
    status: str
    retry: bool
    user_text: str
    assistant_text: str = ""
    completed_turn_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_event(self) -> dict[str, Any]:
        chat_id = str(self.chat_id or "").strip()
        turn_id = str(self.turn_id or "").strip()
        run_id = str(self.run_id or "").strip()
        node_id = str(self.node_id or "").strip()
        if not chat_id or not turn_id or not run_id or not node_id:
            raise ValueError(
                "conversation turn commit requires chat_id, turn_id, run_id, and node_id"
            )
        status = str(self.status or "").strip()
        if status not in {"awaiting_user", "completed"}:
            raise ValueError(f"invalid conversation turn commit status: {status}")
        details = dict(self.metadata)
        details.update(
            {
                "retry": bool(self.retry),
                "turn_id": turn_id,
                "public_user_message": str(self.user_text or ""),
            }
        )
        return {
            "event_id": f"conversation_commit:{chat_id}:{turn_id}:{run_id}:{node_id}",
            "type": CONVERSATION_TURN_COMMITTED,
            "chat_id": chat_id,
            "turn_id": turn_id,
            "run_id": run_id,
            "node_id": node_id,
            "assistant_node_id": node_id,
            "status": status,
            "retry": bool(self.retry),
            "user_text": str(self.user_text or ""),
            "assistant_text": str(self.assistant_text or ""),
            "completed_turn_count": max(0, int(self.completed_turn_count)),
            "metadata": details,
        }


__all__ = ["ConversationTurnCommit"]
