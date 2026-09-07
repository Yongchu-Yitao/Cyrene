"""Background turn presentation policy owned by this Plugin pack."""
from __future__ import annotations

from cyrene.workbench.chat.background_projection import create_completed_background_chat
from collections.abc import Mapping
from typing import Any
from cyrene.workbench.chat.chat_usage import runtime_usage_message_fields


async def create_proactive_chat(
    db_path: str,
    project_id: str,
    text: str,
    *,
    chat_id: str,
    model: str = "",
    usage: Mapping[str, Any] | None = None,
    latest_request_usage: Mapping[str, Any] | None = None,
    source_chat_id: str = "",
    lang: str = "",
) -> dict[str, str] | None:
    return await create_completed_background_chat(
        db_path, project_id, text, chat_id=chat_id, model=model,
        source_chat_id=source_chat_id,
        title="Proactive work" if str(lang or "").lower() == "en" else "主动工作",
        message_fields=lambda: runtime_usage_message_fields(usage, latest_request_usage),
    )


__all__ = ["create_proactive_chat"]
