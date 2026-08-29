"""Generate and persist a chat title after the first user turn."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from cyrene.plugins.model_catalog import resolve_session_model_candidate
from cyrene.workbench.sessions import session_naming
from cyrene.workbench.chat.chat_events import publish_chat_changed

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatSessionNamingDependencies:
    mutate_chat: Callable[[str, Callable[[dict[str, Any]], Any]], Any]
    utc_now_iso: Callable[[], str]


class ChatSessionNamingApplicationService:
    def __init__(self, dependencies: ChatSessionNamingDependencies) -> None:
        self.dependencies = dependencies

    async def generate_and_persist(
        self,
        *,
        chat_id: str,
        project_id: str,
        message: str,
    ) -> None:
        candidate = resolve_session_model_candidate(chat_id)
        candidate_id = str((candidate or {}).get("id") or "")
        candidate_model = str((candidate or {}).get("model") or "")
        logger.info(
            "Workbench session naming started [chat=%s project=%s candidate=%s model=%s input_chars=%d]",
            chat_id,
            project_id,
            candidate_id or "unresolved",
            candidate_model or "unresolved",
            len(message),
        )
        try:
            if candidate is None:
                raise RuntimeError("no configured model candidate for conversation")
            title = await session_naming.generate_session_title(
                message,
                limit=60,
                candidate=candidate,
            )
        except Exception as exc:
            logger.exception(
                "Workbench session naming failed [chat=%s project=%s candidate=%s model=%s error_type=%s]",
                chat_id,
                project_id,
                candidate_id or "unresolved",
                candidate_model or "unresolved",
                type(exc).__name__,
            )
            title = ""
        changed = await asyncio.to_thread(self._persist, chat_id, title)
        logger.info(
            "Workbench session naming finished [chat=%s project=%s candidate=%s model=%s status=%s output_chars=%d]",
            chat_id,
            project_id,
            candidate_id or "unresolved",
            candidate_model or "unresolved",
            "generated" if changed else "failed_or_locked",
            len(title),
        )
        if changed:
            await publish_chat_changed(chat_id, project_id, "renamed")

    def _persist(self, chat_id: str, title: str) -> bool:
        changed = False

        def update(chat: dict[str, Any]) -> bool:
            nonlocal changed
            if chat.get("titleNamingStatus") != "pending":
                return False
            if title and not bool(chat.get("titleLocked")):
                chat["title"] = title
                chat["titleNamingStatus"] = "generated"
                chat["titleGeneratedAt"] = self.dependencies.utc_now_iso()
                changed = True
            else:
                chat["titleNamingStatus"] = "locked" if bool(chat.get("titleLocked")) else "failed"
            return True

        self.dependencies.mutate_chat(chat_id, update)
        return changed


__all__ = ["ChatSessionNamingApplicationService", "ChatSessionNamingDependencies"]
