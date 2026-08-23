"""Authoritative realtime notifications for durable Workbench chat changes."""

from __future__ import annotations

from typing import Any

from cyrene.observability.debug import publish_event


async def publish_chat_changed(
    chat_id: str,
    project_id: str,
    change: str,
    **details: Any,
) -> None:
    """Publish one chat mutation after its durable write has completed."""
    normalized_chat_id = str(chat_id or "").strip()
    await publish_event(
        {
            "type": "workbench_chat_changed",
            "change": str(change or "updated"),
            "chat_id": normalized_chat_id,
            "project_id": str(project_id or "").strip(),
            **details,
        },
        session_id=normalized_chat_id,
    )
