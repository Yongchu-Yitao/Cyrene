"""Application services for legacy/live session queries and exports."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from cyrene.config import STATE_FILE
from cyrene.runtime.memory.conversations import CONVERSATIONS_DIR
from cyrene.workbench import presentation_runtime
from cyrene.workbench.chat import get_workbench_chat


async def _clear_session(*, deleting: bool = False) -> None:
    from cyrene.agent.session import clear_session_id
    await clear_session_id(deleting=deleting)


def _interrupt_session() -> bool:
    from cyrene.agent.coordinator import interrupt_active_run
    return interrupt_active_run()


class SessionPresentationPort(Protocol):
    def sessions(self) -> list[dict[str, Any]]: ...
    def archives(self, skip_ids: set[str]) -> list[dict[str, Any]]: ...
    def parse_archive(self, content: str) -> list[dict[str, Any]]: ...
    def write_archive(self, path: Path, date: str, sections: list[dict[str, Any]]) -> None: ...


class WorkbenchSessionPresentation:
    def sessions(self) -> list[dict[str, Any]]:
        return presentation_runtime.build_sessions()

    def archives(self, skip_ids: set[str]) -> list[dict[str, Any]]:
        return presentation_runtime.build_archive_sessions(skip_archive_ids=skip_ids)

    def parse_archive(self, content: str) -> list[dict[str, Any]]:
        return presentation_runtime.parse_archive_sections(content)

    def write_archive(self, path: Path, date: str, sections: list[dict[str, Any]]) -> None:
        presentation_runtime.write_archive_sections(path, date, sections)


@dataclass(slots=True)
class SessionServiceError(RuntimeError):
    message: str
    status_code: int = 500

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SessionExport:
    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class SessionDocument:
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, str]]


class SessionRepository:
    """Typed access to live state, archives, and Workbench chats."""

    def __init__(
        self,
        *,
        state_file: Path = STATE_FILE,
        conversations_dir: Path = CONVERSATIONS_DIR,
        presentation: SessionPresentationPort | None = None,
        chat_reader: Callable[[str], dict[str, Any] | None] = get_workbench_chat,
    ) -> None:
        self.state_file = state_file
        self.conversations_dir = conversations_dir
        self.presentation = presentation or WorkbenchSessionPresentation()
        self.chat_reader = chat_reader

    def state(self) -> dict[str, Any] | None:
        if not self.state_file.exists():
            return None
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionServiceError(f"failed to read current session: {exc}") from exc
        if not isinstance(value, dict):
            raise SessionServiceError("current session state must be a JSON object")
        return value

    def archive(self, date: str) -> list[dict[str, Any]] | None:
        path = self.conversations_dir / f"{date}.md"
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SessionServiceError(f"failed to read archived session: {exc}") from exc
        return self.presentation.parse_archive(content)

    def replace_archive(self, date: str, sections: list[dict[str, Any]]) -> None:
        path = self.conversations_dir / f"{date}.md"
        try:
            self.presentation.write_archive(path, date, sections)
        except OSError as exc:
            raise SessionServiceError(f"failed to update archived session: {exc}") from exc

    async def workbench_chat(self, session_id: str) -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(self.chat_reader, session_id)
        except Exception as exc:
            raise SessionServiceError(f"failed to read Workbench session: {exc}") from exc


class SessionApplicationService:
    def __init__(
        self,
        db_path: str,
        *,
        repository: SessionRepository | None = None,
        presentation: SessionPresentationPort | None = None,
        model_stats_reader: Callable[[str, str, str], Awaitable[list[Any]]] | None = None,
        clear_session: Callable[..., Awaitable[None]] = _clear_session,
        interrupt_session: Callable[..., Any] = _interrupt_session,
    ) -> None:
        self.db_path = db_path
        self.presentation = presentation or WorkbenchSessionPresentation()
        self.repository = repository or SessionRepository(presentation=self.presentation)
        self.model_stats_reader = model_stats_reader
        self.clear_session = clear_session
        self.interrupt_session = interrupt_session

    async def list_sessions(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).astimezone()
        day_from = (now - timedelta(days=27)).strftime("%Y-%m-%d")
        day_to = now.strftime("%Y-%m-%d")
        reader = self.model_stats_reader
        if reader is None:
            from cyrene.runtime.database import get_model_stats_range
            reader = get_model_stats_range
        try:
            model_stats = await reader(self.db_path, day_from, day_to)
        except Exception as exc:
            raise SessionServiceError(f"failed to read session model statistics: {exc}") from exc
        return {"sessions": self.presentation.sessions(), "model_stats": model_stats}

    async def create_session(self) -> dict[str, Any]:
        await self.clear_session()
        return {"ok": True, "sessions": self.presentation.sessions()}

    def archive_context(self, cursor: str = "") -> dict[str, Any]:
        skip_ids = self._current_archive_ids()
        archives = self.presentation.archives(skip_ids)
        start = self._archive_start(archives, cursor.strip())
        if start is None or start >= len(archives):
            return {"messages": [], "hasMore": False}
        target = archives[start]
        raw_messages = target.get("chat", {}).get("messages", [])
        messages = [dict(message, isArchivedContext=True) for message in raw_messages]
        return {
            "messages": messages,
            "id": target["id"],
            "archiveSessionId": target.get("archiveSessionId", ""),
            "archiveDate": target.get("archiveDate", ""),
            "title": target.get("title", ""),
            "hasMore": (start + 1) < len(archives),
        }

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id == "run_live":
            self.interrupt_session()
            await self.clear_session(deleting=True)
        elif session_id.startswith("archive_"):
            self._delete_archive(session_id)
        else:
            raise SessionServiceError("unknown session id", 400)
        return {"ok": True, "sessions": self.presentation.sessions()}

    async def export_session(self, session_id: str, fmt: str) -> SessionExport:
        normalized = fmt.strip().lower()
        if normalized not in {"markdown", "json"}:
            raise SessionServiceError("format must be 'markdown' or 'json'", 400)
        document = await self._document(session_id)
        safe_title = re.sub(r"[^\w\-. ]+", "_", document.title or session_id, flags=re.ASCII)
        safe_title = safe_title[:60].strip("_. ") or "session"
        if normalized == "json":
            payload = {
                "id": session_id,
                "title": document.title,
                "created_at": document.created_at,
                "updated_at": document.updated_at,
                "message_count": len(document.messages),
                "messages": document.messages,
            }
            return SessionExport(
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
                f"{safe_title}.json",
            )
        return SessionExport(
            self._markdown(session_id, document).encode("utf-8"),
            "text/markdown; charset=utf-8",
            f"{safe_title}.md",
        )

    def _current_archive_ids(self) -> set[str]:
        state = self.repository.state()
        archive_id = str((state or {}).get("archive_session_id", "")).strip()
        if not archive_id:
            return set()
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        return {f"{today}:{archive_id}"}

    @staticmethod
    def _archive_start(archives: list[dict[str, Any]], cursor: str) -> int | None:
        if not cursor:
            return 0
        return next((index + 1 for index, item in enumerate(archives) if item.get("id") == cursor), None)

    def _delete_archive(self, session_id: str) -> None:
        date, _, archive_id = session_id.removeprefix("archive_").partition("_")
        sections = self.repository.archive(date)
        if sections is None:
            raise SessionServiceError("session not found", 404)
        kept = [item for item in sections if str(item.get("archive_session_id", "")).strip() != archive_id]
        if len(kept) == len(sections):
            raise SessionServiceError("session not found", 404)
        self.repository.replace_archive(date, kept)

    async def _document(self, session_id: str) -> SessionDocument:
        if session_id == "run_live":
            return self._live_document()
        if session_id.startswith("archive_"):
            return self._archive_document(session_id)
        chat = await self.repository.workbench_chat(session_id)
        if not chat:
            raise SessionServiceError("unknown session id", 400)
        messages = self._messages(chat.get("messages") or [], workbench=True)
        created_at = str(chat.get("createdAt") or "")
        return SessionDocument(
            str(chat.get("title") or "conversation"), created_at,
            str(chat.get("updatedAt") or created_at), messages,
        )

    def _live_document(self) -> SessionDocument:
        stored_state = self.repository.state()
        state = stored_state or {}
        messages = self._messages(state.get("messages", []) or [])
        created_at = datetime.now().astimezone().strftime("%Y-%m-%d") if stored_state is not None else ""
        title = str(state.get("session_title", "")).strip() or "current session"
        return SessionDocument(title, created_at, created_at, messages)

    def _archive_document(self, session_id: str) -> SessionDocument:
        date, _, archive_id = session_id.removeprefix("archive_").partition("_")
        sections = self.repository.archive(date)
        if sections is None:
            raise SessionServiceError("session not found", 404)
        matching = [item for item in sections if str(item.get("archive_session_id", "")).strip() == archive_id]
        if not matching and archive_id.startswith("legacy_"):
            matching = [item for item in sections if not str(item.get("archive_session_id", "")).strip()]
        if not matching:
            raise SessionServiceError("session not found", 404)
        title = next((str(item.get("session_title", "")).strip() for item in matching if item.get("session_title")), "") or date
        timestamps = [str(item.get("timestamp", "")).strip() for item in matching if item.get("timestamp")]
        messages: list[dict[str, str]] = []
        for section in matching:
            timestamp = str(section.get("timestamp", "")).strip()
            for role, field in (("user", "user_body"), ("assistant", "assistant_body")):
                content = str(section.get(field, "")).strip()
                if content:
                    messages.append({"role": role, "content": content, "time": timestamp})
        return SessionDocument(title, date, timestamps[-1] if timestamps else date, messages)

    @staticmethod
    def _messages(raw_messages: list[Any], *, workbench: bool = False) -> list[dict[str, str]]:
        messages = []
        for item in raw_messages:
            if not isinstance(item, dict) or (workbench and item.get("hidden_from_ui")):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            time = (item.get("createdAt") or item.get("created_at")) if workbench else item.get("created_at")
            messages.append({"role": role, "content": content, "time": str(time or "")})
        return messages

    @staticmethod
    def _markdown(session_id: str, document: SessionDocument) -> str:
        lines = [
            f"# {document.title}", "", f"**Session ID**: `{session_id}`",
            f"**Date**: {document.created_at}", f"**Messages**: {len(document.messages)}",
            "", "---", "",
        ]
        index = 0
        while index < len(document.messages):
            message = document.messages[index]
            if message["role"] == "user":
                if message["time"]:
                    lines.extend((f"## {message['time']}", ""))
                lines.extend((f"**User**: {message['content']}", ""))
                if index + 1 < len(document.messages) and document.messages[index + 1]["role"] == "assistant":
                    lines.extend((f"**Cyrene**: {document.messages[index + 1]['content']}", "", "---", ""))
                    index += 2
                    continue
            else:
                lines.extend((f"**Cyrene**: {message['content']}", "", "---", ""))
            index += 1
        return "\n".join(lines)


__all__ = [
    "SessionApplicationService", "SessionExport", "SessionRepository",
    "SessionServiceError", "WorkbenchSessionPresentation",
]
