"""Process-level routes and search owned by the editable memory Plugin pack."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.plugin import PluginApplicationContext
from cyrene.localization import app_language, localized


logger = logging.getLogger(__name__)


def _soul_application() -> Any | None:
    from agent.plugin import active_plugin_service

    return active_plugin_service("soul")


def _normalize_search_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _matches(query: str, value: Any) -> bool:
    needle = _normalize_search_text(query)
    haystack = _normalize_search_text(value)
    if not needle or not haystack:
        return False
    return needle in haystack or needle.replace(" ", "") in haystack.replace(" ", "")


def _snippet(value: Any, query: str, *, length: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    needle = _normalize_search_text(query)
    normalized = text.casefold()
    index = normalized.find(needle) if needle else -1
    if index < 0:
        return text[:length] + ("…" if len(text) > length else "")
    start = max(0, index - length // 2)
    end = min(len(text), start + length)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _soul_overview(
    content: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], int, int]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    temporary_count = 0
    temporary_expired = 0
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("## ") and not trimmed.startswith("### "):
            if current is not None:
                sections.append(current)
            current = {
                "name": trimmed[3:].strip(),
                "entries": [],
                "entry_count": 0,
            }
            continue
        if current is None or not trimmed or trimmed.startswith("<!--"):
            continue
        current["entries"].append(trimmed)
        current["entry_count"] += 1
        if current["name"] != "TEMPORARY":
            continue
        temporary_count += 1
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", trimmed)
        if not date_match:
            continue
        try:
            item_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (now - item_date).days >= 1:
                temporary_expired += 1
        except ValueError:
            pass
    if current is not None:
        sections.append(current)
    return sections, temporary_count, temporary_expired


@dataclass(slots=True)
class MemoryApplication:
    """Application-owned memory capabilities shared by HTTP and global search."""

    db_path: str
    memory: Any
    project_memory: Any
    data_directory: Path
    model_gateway: Any = None

    @classmethod
    def create(
        cls,
        db_path: str,
        data_directory: str | Path,
        *,
        model_gateway: Any,
    ) -> "MemoryApplication":
        if model_gateway is None or not callable(
            getattr(model_gateway, "complete", None)
        ):
            raise RuntimeError("memory Plugin application requires the model service")
        from cyrene.workbench.chat_repository import ChatRepository
        from cyrene.workbench.context import configure_store, read_projects
        from .structured import MemoryApplicationService
        from .project_memory import (
            ProjectMemoryApplicationService,
            ProjectQueryPort,
        )

        normalized_db_path = str(db_path or "")
        configure_store(normalized_db_path)
        from .archive import configure_archive

        configure_archive(normalized_db_path)
        memory = MemoryApplicationService(normalized_db_path)
        chats = ChatRepository()
        chats.configure(normalized_db_path)

        def find_project(project_id: str) -> dict[str, Any] | None:
            target = str(project_id or "").strip()
            return next(
                (dict(project) for project in read_projects() if isinstance(project, dict) and str(project.get("id") or "") == target),
                None,
            )

        project_memory = ProjectMemoryApplicationService(
            normalized_db_path,
            ProjectQueryPort(find_project),
            chats,
            memory,
            model_gateway=model_gateway,
        )
        return cls(
            normalized_db_path,
            memory,
            project_memory,
            Path(data_directory).expanduser().resolve(),
            model_gateway,
        )

    async def overview(self) -> dict[str, Any]:
        """Build the Memory-page projection without a host-side memory backend."""

        from agent.context.compaction import message_token_estimate
        from agent.plugin.model_catalog import configured_context_limit
        from agent.workbench.chat_runtime import workbench_agent_data_directory
        from cyrene.workbench.conversation_context_service import AgentContextRepository
        from .archive import CONVERSATIONS_DIR
        from .short_term import load_entries
        soul = _soul_application()
        soul_content = str(soul.read() or "") if soul is not None else ""
        now = datetime.now(timezone.utc)
        sections, temporary_count, temporary_expired = _soul_overview(
            soul_content,
            now,
        )

        entries = load_entries()
        session_messages: list[dict[str, Any]] = []
        compaction_blocks = 0
        summaries = self.project_memory.chats.read_summaries().get("chats") or []
        latest = max(
            (item for item in summaries if isinstance(item, dict)),
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            default=None,
        )
        session_id = str((latest or {}).get("id") or "")
        if session_id:
            state = AgentContextRepository(
                workbench_agent_data_directory(self.db_path) / "context"
            ).read(session_id)
            raw_messages = state.get("messages") if isinstance(state, dict) else None
            if isinstance(raw_messages, list):
                session_messages = [item for item in raw_messages if isinstance(item, dict)]
                compaction = state.get("compaction")
                if isinstance(compaction, dict):
                    compaction_blocks = int(compaction.get("blocks") or 0)
            if not session_messages:
                chat = self.project_memory.chats.get(session_id) or {}
                session_messages = [
                    item for item in chat.get("messages") or [] if isinstance(item, dict)
                ]
        context_limit = configured_context_limit(session_id)
        today_file = CONVERSATIONS_DIR / f"{now.strftime('%Y-%m-%d')}.md"
        today_exchanges = 0
        if today_file.exists():
            try:
                today_exchanges = max(
                    0,
                    today_file.read_text(encoding="utf-8").count("## ") - 1,
                )
            except (OSError, UnicodeError):
                pass
        return {
            "soul": {
                "exists": bool(soul_content),
                "path": str(soul.path()) if soul is not None else "",
                "sections": sections,
                "temporary_count": temporary_count,
                "temporary_expired": temporary_expired,
            },
            "short_term": {
                "entries": sorted(
                    entries,
                    key=lambda entry: str(entry.get("last_mentioned") or ""),
                    reverse=True,
                ),
                "total": len(entries),
            },
            "context_window": {
                "messages": len(session_messages),
                "max": 0,
                "tokens": sum(message_token_estimate(item) for item in session_messages),
                "ctx_limit": context_limit,
                "trigger_tokens": int(context_limit * 0.6) if context_limit else 0,
                "compacted_blocks": compaction_blocks or sum(bool(item.get("compacted_block")) for item in session_messages),
            },
            "archive": {
                "days": len(list(CONVERSATIONS_DIR.glob("*.md"))) if CONVERSATIONS_DIR.exists() else 0,
                "today_exchanges": today_exchanges,
            },
        }

    def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        from cyrene.workbench.context import read_projects
        from .structured import (
            _entry_id,
            _is_user_visible_entry,
            _safe_workspace_id,
        )
        from cyrene.workbench.store import list_document_keys, read_document

        projects = [project for project in read_projects() if isinstance(project, dict) and str(project.get("id") or "").strip()]
        project_names = {
            str(project.get("id")): str(project.get("name") or "").strip()
            for project in projects
        }
        storage_to_project: dict[str, str] = {}
        for project in projects:
            project_id = str(project.get("id") or "").strip()
            storage_to_project.setdefault(_safe_workspace_id(project_id), project_id)

        db_path = self.db_path
        if not db_path:
            return []
        memory_keys = {key[len("memory:") :] for key in list_document_keys(db_path, prefix="memory:")}

        results: list[dict[str, Any]] = []
        bounded = max(1, min(int(limit or 10), 100))
        for storage_key in sorted(memory_keys):
            project_id = storage_to_project.get(storage_key, "")
            entries = read_document(
                db_path,
                f"memory:{storage_key}",
                list,
            )
            for entry in entries if isinstance(entries, list) else ():
                if not isinstance(entry, dict) or not _is_user_visible_entry(entry):
                    continue
                content = str(entry.get("content") or "")
                tags = [str(tag) for tag in entry.get("tags") or ()]
                if not (_matches(query, content) or _matches(query, " ".join(tags))):
                    continue
                memory_id = _entry_id(entry)
                results.append(
                    {
                        "id": memory_id,
                        "type": "memory",
                        "title": content[:80],
                        "titleKey": "" if content else "search.default.memory",
                        "snippet": _snippet(content, query),
                        "projectId": project_id,
                        "projectName": project_names.get(project_id, ""),
                        "projectNameDefault": not bool(project_names.get(project_id, "")),
                        "memId": memory_id,
                        "category": entry.get("category") or entry.get("type") or "fact",
                        "tags": tags,
                        "updatedAt": entry.get("last_mentioned") or entry.get("first_seen") or "",
                    }
                )
                if len(results) >= bounded:
                    return results
        return results

    async def search_workbench(self, query: str, limit: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._search, str(query or ""), limit)

    async def search_conversations(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        from .archive import search_conversations_structured

        return await search_conversations_structured(
            str(query or "").strip(),
            limit=max(1, min(int(limit or 30), 100)),
        )

    @property
    def conversations_directory(self) -> Path:
        from .archive import CONVERSATIONS_DIR

        return CONVERSATIONS_DIR

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        """Return Plugin-owned filesystem roots for the Settings data panel.

        Structured and project memory documents live in the shared Workbench
        SQLite database and therefore remain part of the database category.
        This reports only files exclusively owned by this pack, while keeping
        the core storage scanner independent of their concrete locations.
        """

        return {
            "memory": (
                self.data_directory / "short_term.json",
                self.data_directory / "memory_steward.json",
            ),
            "conversations": (self.conversations_directory,),
        }

    def backup_sources(self) -> dict[str, tuple[tuple[Path, str], ...]]:
        """Describe Plugin-owned files for the host's generic backup service."""

        return {
            "directories": ((self.conversations_directory, "workspace/conversations"),),
        }

    def memory_context(
        self,
        *,
        include_short_term: bool = True,
        soul_enabled: bool = True,
    ) -> str:
        from .short_term import get_context
        soul = _soul_application() if soul_enabled else None
        parts = [str(soul.persona_context() or "").strip()] if soul is not None else []
        if include_short_term:
            language = app_language()
            parts.append(get_context(
                max_chars=5000,
                header=localized(
                    "[Short-term cross-session memory:]",
                    "[跨会话短期记忆：]",
                    language=language,
                ),
            ).strip())
        return "\n\n".join(part for part in parts if part)

    def short_term_context(
        self,
        *,
        max_chars: int = 5000,
        header: str | None = None,
    ) -> str:
        from .short_term import get_context

        return get_context(max_chars=max_chars, header=header)

    def short_term_entries(self) -> list[dict[str, Any]]:
        from .short_term import load_entries

        return load_entries()

    def save_short_term_entries(self, entries: list[dict[str, Any]]) -> None:
        from .short_term import save_entries

        save_entries(entries)

    def touch_short_term(
        self,
        content_keyword: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from .short_term import touch_entry

        touch_entry(content_keyword, metadata)

    def clear_old_short_term(self, days: int = 7) -> None:
        from .short_term import clear_old_entries

        clear_old_entries(days)

    def compression_due(self, messages: list[dict[str, Any]]) -> bool:
        from .short_term import compression_due

        return compression_due(messages)

    async def compress_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str = "",
    ) -> None:
        from .short_term import compress_messages

        await compress_messages(
            messages,
            session_id=session_id,
            model_gateway=self.model_gateway,
        )

    async def archive_exchange(self, *args: Any, **kwargs: Any) -> Any:
        from .archive import archive_exchange

        return await archive_exchange(*args, **kwargs)

    def archive_session_exchange(self, *args: Any, **kwargs: Any) -> Any:
        from .archive import archive_session_exchange

        return archive_session_exchange(*args, **kwargs)

    def archived_round(self, archive_session_id: str, round_id: str) -> dict[str, Any] | None:
        from .archive import get_archived_round

        return get_archived_round(archive_session_id, round_id)

    def parse_daily_archive(
        self,
        content: str,
        date: str,
    ) -> list[dict[str, str]]:
        from .archive_format import parse_archive_sections

        return parse_archive_sections(content, date)

    def archive_meta(self, section: str, key: str) -> str:
        from .archive_format import parse_archive_meta

        return parse_archive_meta(section, key)

    def split_archive_entries(self, content: str) -> list[str]:
        from .archive_format import split_archive_entry_blocks

        return split_archive_entry_blocks(content)

    def update_archive_session_title(
        self,
        content: str,
        date: str,
        session_title: str,
    ) -> str:
        from .archive import upsert_archive_session_title

        return upsert_archive_session_title(content, date, session_title)

    def write_daily_archive(
        self,
        filepath: Path,
        date: str,
        sections: list[dict[str, Any]],
    ) -> None:
        if not sections:
            try:
                filepath.unlink()
            except FileNotFoundError:
                pass
            return
        session_title = next(
            (str(section.get("session_title") or "").strip() for section in sections if section.get("session_title")),
            "",
        )
        content = self.update_archive_session_title(
            f"# Conversations - {date}\n\n",
            date,
            session_title,
        )
        content += "\n---\n\n".join(str(section.get("raw_entry") or "") for section in sections if section.get("raw_entry"))
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content + "\n\n---\n", encoding="utf-8")

    @staticmethod
    def _archive_identifier(value: str) -> str:
        identifier = str(value or "").strip()
        if not identifier or identifier in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
            raise ValueError("invalid conversation archive identifier")
        return identifier

    def has_archived_conversations(self) -> bool:
        directory = self.conversations_directory
        return directory.is_dir() and any(directory.glob("*.md"))

    def has_existing_data(self) -> bool:
        """Return whether this Plugin owns any durable user memory."""

        if self.has_archived_conversations():
            return True
        if not self.db_path or not Path(self.db_path).exists():
            return False
        from cyrene.workbench.store import list_document_keys

        return bool(list_document_keys(self.db_path, prefix="memory:"))

    def list_archive_documents(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return parsed archive documents without exposing storage paths."""

        directory = self.conversations_directory
        if not directory.is_dir():
            return []
        files = sorted(directory.glob("*.md"), reverse=True)
        if limit is not None:
            files = files[: max(0, int(limit))]
        documents: list[dict[str, Any]] = []
        for filepath in files:
            try:
                content = filepath.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            date = filepath.stem
            try:
                sections = self.parse_daily_archive(content, date)
                session_title = self.archive_meta(content, "session_title")
            except Exception:
                continue
            documents.append(
                {
                    "date": date,
                    "content": content,
                    "sections": sections,
                    "session_title": session_title,
                }
            )
        return documents

    def read_archive_sections(self, date: str) -> list[dict[str, Any]] | None:
        identifier = self._archive_identifier(date)
        filepath = self.conversations_directory / f"{identifier}.md"
        if not filepath.is_file():
            return None
        content = filepath.read_text(encoding="utf-8")
        return self.parse_daily_archive(content, identifier)

    def replace_archive_sections(
        self,
        date: str,
        sections: list[dict[str, Any]],
    ) -> None:
        identifier = self._archive_identifier(date)
        self.write_daily_archive(
            self.conversations_directory / f"{identifier}.md",
            identifier,
            sections,
        )

    def delete_archive_session(self, date: str, archive_session_id: str) -> bool:
        sections = self.read_archive_sections(date)
        if sections is None:
            return False
        target = str(archive_session_id or "").strip()
        if not target:
            return False
        kept = [section for section in sections if str(section.get("archive_session_id") or "").strip() != target]
        if len(kept) == len(sections):
            return False
        self.replace_archive_sections(date, kept)
        return True

    def latest_archived_user_message_time(self) -> datetime | None:
        """Return the newest user-exchange timestamp from global archives."""

        timestamp_pattern = re.compile(
            r"## (\d{2}:\d{2}:\d{2} UTC)\n.*?\*\*User\*\*:",
            re.DOTALL,
        )
        for document in self.list_archive_documents():
            matches = timestamp_pattern.findall(str(document.get("content") or ""))
            if not matches:
                continue
            try:
                return datetime.strptime(
                    f"{document['date']} {matches[-1].replace(' UTC', '')}",
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @staticmethod
    def project_key(project: dict[str, Any] | None) -> str:
        """Return the Plugin-owned storage identity for a Workbench project."""

        from .structured import _safe_workspace_id

        project_id = project.get("id") if isinstance(project, dict) else None
        return _safe_workspace_id(project_id)

    def render_past_task_reports(
        self,
        project: dict[str, Any] | None,
        *,
        limit: int = 3,
        max_chars: int = 2500,
        language: str = "",
    ) -> str:
        """Render project completion reports for the planning prompt."""

        if not isinstance(project, dict):
            return ""
        from .structured import render_task_reports_for_planning

        return render_task_reports_for_planning(
            self.project_key(project),
            limit=limit,
            max_chars=max_chars,
            language=language,
        )

    def store_reflection_insights(
        self,
        project: dict[str, Any] | None,
        packet: dict[str, Any],
    ) -> int:
        """Persist durable reflection findings as hidden project memories."""

        if not isinstance(project, dict) or not isinstance(packet, dict):
            return 0
        from .structured import add_agent_memory

        memory_key = self.project_key(project)
        stored = 0
        for field, tags in (
            (
                "excluded_paths",
                ["reflection", "dead_end"],
            ),
            (
                "promising_directions",
                ["reflection", "promising_direction"],
            ),
        ):
            values = packet.get(field)
            for value in values[:5] if isinstance(values, list) else ():
                text = str(value or "").strip()
                if not text:
                    continue
                add_agent_memory(
                    memory_key,
                    text,
                    category="reflection",
                    source="agent",
                    tags=tags,
                )
                stored += 1
        return stored

    def store_task_report(
        self,
        project: dict[str, Any] | None,
        report: str,
    ) -> bool:
        """Persist a Task completion report under the Plugin's hidden category."""

        content = str(report or "").strip()
        if not isinstance(project, dict) or not content:
            return False
        from .structured import add_agent_memory

        add_agent_memory(
            self.project_key(project),
            content,
            category="task_report",
            tags=["task_report", "auto_generated"],
            source="agent",
        )
        return True

    async def recent_conversations(self, days: int = 1) -> str:
        from .archive import get_recent_conversations

        return await get_recent_conversations(days)

    async def run_steward_if_needed(
        self,
        *,
        interval: int,
        now: float | None = None,
    ) -> bool:
        from .steward import run_steward_if_needed

        return await run_steward_if_needed(
            self,
            interval=interval,
            now=now,
            soul_application=_soul_application(),
        )

    def session_conversations_directory(
        self,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        from .archive import session_conversations_dir

        return session_conversations_dir(workspace_dir)

    def delete_session_archive(
        self,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> bool:
        """Delete one Workbench conversation archive through the Plugin domain."""

        from .archive import session_conversation_file

        archive_path = session_conversation_file(session_id, workspace_dir)
        existed = archive_path.is_file()
        archive_path.unlink(missing_ok=True)
        return existed

    def current_snapshot(self, project_id: str) -> dict[str, str]:
        from .project_memory import current_snapshot

        return current_snapshot(project_id)

    def delete_workspace(self, workspace_id: str) -> None:
        from .structured import delete_workspace_memory

        delete_workspace_memory(workspace_id)

    async def cancel_project_jobs(self, project_id: str) -> None:
        from .project_memory import cancel_project_jobs

        await cancel_project_jobs(project_id)

    def delete_project(self, project_id: str, chat_ids: list[str]) -> None:
        from .project_memory import delete_project_memory

        delete_project_memory(project_id, chat_ids)

    async def delete_chat(self, chat_id: str) -> None:
        from .project_memory import (
            cancel_chat_jobs,
            delete_chat_context,
        )

        await cancel_chat_jobs(chat_id)
        await asyncio.to_thread(delete_chat_context, chat_id)

    async def shutdown(self) -> None:
        from .project_memory import cancel_pending_jobs

        await cancel_pending_jobs()

    def startup(self) -> None:
        from .archive import ensure_conversations_dir
        from .short_term import init_short_term

        init_short_term(self.data_directory, self.db_path)
        ensure_conversations_dir()

    async def reset_data(self) -> None:
        """Remove all Plugin-owned files and recreate an empty memory store."""

        from .archive import CONVERSATIONS_DIR
        from .short_term import init_short_term, save_entries
        from cyrene.workbench.store import delete_document, list_document_keys

        await self.shutdown()
        if self.db_path:
            for prefix in (
                "memory:",
                "project_memory_prompt:",
                "project_memory_context:",
            ):
                for key in list_document_keys(self.db_path, prefix=prefix):
                    delete_document(self.db_path, key)
        shutil.rmtree(CONVERSATIONS_DIR, ignore_errors=True)
        for path in (self.data_directory / "memory_steward.json",):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        init_short_term(self.data_directory, self.db_path)
        save_entries([])
        self.startup()


def setup_application(context: PluginApplicationContext) -> None:
    from .routes_overview import (
        register_conversation_search_routes,
        register_memory_routes,
    )
    from .routes_structured import register_workbench_memory_routes
    from .routes_project import register_project_memory_routes

    application = MemoryApplication.create(
        context.db_path,
        context.data_directory,
        model_gateway=context.services.get("model"),
    )
    register_workbench_memory_routes(context.router, application.memory)
    register_project_memory_routes(context.router, application.project_memory)
    register_memory_routes(context.router, application)
    register_conversation_search_routes(context.router, application)
    context.provide("memory", application)
    context.provide_search("memory", application.search_workbench)
    context.expose_frontend("memory")
    context.on_startup(application.startup)
    context.on_shutdown(application.shutdown)


__all__ = ["MemoryApplication", "setup_application"]
