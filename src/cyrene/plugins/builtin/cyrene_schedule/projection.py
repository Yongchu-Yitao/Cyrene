"""Background turn presentation policy owned by this Plugin pack."""
from __future__ import annotations

from cyrene.workbench.chat.background_projection import (
    append_completed_background_message,
    create_completed_background_chat,
)
from cyrene.localization import app_language, localized


async def create_scheduled_chat(
    db_path: str,
    project_id: str,
    text: str,
    *,
    chat_id: str,
    model: str = "",
    source_chat_id: str = "",
    lang: str = "",
) -> dict[str, str] | None:
    if source_chat_id:
        projected = await append_completed_background_message(
            db_path,
            source_chat_id,
            text,
            delivery_id=chat_id,
            model=model,
        )
        if projected is not None:
            return projected
    return await create_completed_background_chat(
        db_path, project_id, text, chat_id=chat_id, model=model,
        source_chat_id=source_chat_id,
        title=localized("Scheduled task", "定时任务", language=app_language(lang)),
    )


__all__ = ["create_scheduled_chat"]
