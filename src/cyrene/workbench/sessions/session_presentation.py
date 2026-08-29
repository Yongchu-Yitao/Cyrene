"""Workbench conversation projections for presentation and data-management APIs.

The SQLite chat repository owns public conversation records.  The Agent
``ContextTree`` only contributes durable execution details (tool activity,
Plugin usage, and subagent state); neither source is reconstructed from the
retired singleton session file.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
from cyrene.localization import localized
from cyrene.workbench.persistence import store
from cyrene.workbench.chat.conversation_context_service import AgentContextRepository

logger = logging.getLogger(__name__)

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


class WorkbenchSessionError(RuntimeError):
    """A public session operation failed with a stable HTTP-style status."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str = "conversation_operation_failed",
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code or "conversation_operation_failed")


@dataclass(frozen=True, slots=True)
class WorkbenchSessionExport:
    content: bytes
    media_type: str
    filename: str


def _empty_chat_store() -> dict[str, list[Any]]:
    return {"chats": []}


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _usage(value: Any) -> dict[str, int]:
    source = value if isinstance(value, Mapping) else {}
    result = {key: _integer(source.get(key)) for key in _USAGE_KEYS}
    if not result["total_tokens"]:
        result["total_tokens"] = (
            result["prompt_tokens"] + result["completion_tokens"]
        )
    return result


def _format_tokens(value: int) -> str:
    tokens = max(0, int(value or 0))
    if tokens < 1_000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{tokens / 1_000:.1f}K".replace(".0K", "K")
    return f"{tokens / 1_000_000:.1f}M".replace(".0M", "M")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _started(value: Any) -> str:
    parsed = _parse_time(value)
    return parsed.astimezone().strftime("%H:%M") if parsed is not None else "—"


def _duration(created_at: Any, updated_at: Any) -> str:
    created = _parse_time(created_at)
    updated = _parse_time(updated_at)
    if created is None or updated is None:
        return "—"
    seconds = max(0, int((updated - created).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _run_status(
    chat: Mapping[str, Any],
    message_count: int,
    checkpoint: Mapping[str, Any] | None = None,
) -> str:
    if isinstance(chat.get("pendingQuestion"), Mapping) and bool(
        chat.get("pendingQuestion")
    ):
        return "awaiting_user"
    persisted = str(chat.get("status") or "idle").strip().lower()
    if persisted == "running":
        durable = str((checkpoint or {}).get("status") or "").strip().lower()
        if durable in {
            "awaiting_user",
            "cancelled",
            "completed",
            "failed",
            "running",
        }:
            return durable
        return "running"
    if persisted in {"failed", "error"}:
        return "failed"
    if persisted in {"cancelled", "interrupted"}:
        return "cancelled"
    if persisted in {"awaiting", "awaiting_user", "queued"}:
        return "awaiting_user"
    last_run = chat.get("lastRun")
    last_run = last_run if isinstance(last_run, Mapping) else {}
    status = str(last_run.get("status") or "").strip().lower()
    outcome = str(last_run.get("outcome") or "").strip().lower()
    termination = str(last_run.get("terminationReason") or "").strip().lower()
    if status in {"error", "failed"} or outcome == "error":
        return "failed"
    if status in {"cancelled", "interrupted"} or termination in {
        "cancelled",
        "user_interrupted",
        "shutdown_timeout",
    }:
        return "cancelled"
    if outcome == "awaiting" or termination == "awaiting_user":
        return "awaiting_user"
    if status in {"done", "completed", "success"} or outcome == "reply":
        return "completed"
    return "completed" if message_count else "idle"


def _ui_status(run_status: str) -> str:
    return {
        "running": "running",
        "awaiting_user": "queued",
        "failed": "err",
        "cancelled": "done",
        "completed": "done",
        "idle": "idle",
    }.get(str(run_status or ""), "idle")


def _subagent_cards(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = state.get("subagents")
    raw = raw if isinstance(raw, Mapping) else {}
    cards: list[dict[str, Any]] = []
    for agent_id, item in raw.items():
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "running")
        cards.append(
            {
                "id": str(agent_id),
                "name": str(agent_id),
                "task": str(item.get("task") or ""),
                "status": status,
                "result": str(item.get("result") or ""),
                "roundId": str(item.get("round_id") or ""),
                "createdAt": str(item.get("created_at") or ""),
                "updatedAt": str(item.get("updated_at") or ""),
            }
        )
    cards.sort(key=lambda item: (item["createdAt"], item["id"]))
    return cards


def _context_chips(state: Mapping[str, Any]) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    for pack in state.get("usedPluginPacks") or ():
        value = str(pack or "").strip()
        if value:
            chips.append({"icon": "🧰", "label": value, "key": f"pack:{value}"})
    for plugin in state.get("usedStandalonePlugins") or ():
        value = str(plugin or "").strip()
        if value:
            chips.append({"icon": "🔌", "label": value, "key": f"plugin:{value}"})
    return chips


def _merge_activity_messages(
    messages: list[dict[str, Any]],
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    merged = [dict(item) for item in messages if isinstance(item, Mapping)]
    known = {
        str(item.get("id") or item.get("messageId") or "")
        for item in merged
        if str(item.get("id") or item.get("messageId") or "")
    }
    activities = state.get("activityMessages")
    for raw in activities if isinstance(activities, list) else ():
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        item_id = str(item.get("id") or "")
        if item_id and item_id in known:
            continue
        if item_id:
            known.add(item_id)
        merged.append(item)
    merged.sort(
        key=lambda item: (
            not bool(str(item.get("createdAt") or "")),
            str(item.get("createdAt") or ""),
        )
    )
    return merged


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", message.get("body", ""))
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping) and str(item.get("text") or "")
        )
    return str(content or "")


def _markdown(chat: Mapping[str, Any], messages: list[dict[str, Any]]) -> str:
    title = str(chat.get("title") or "Conversation").strip() or "Conversation"
    lines = [f"# {title}", ""]
    for label, key in (
        ("Conversation ID", "id"),
        ("Project ID", "projectId"),
        ("Model", "model"),
        ("Created", "createdAt"),
        ("Updated", "updatedAt"),
    ):
        value = str(chat.get(key) or "").strip()
        if value:
            lines.append(f"- {label}: {value}")
    if len(lines) > 2:
        lines.append("")
    for message in messages:
        if message.get("activityCard"):
            trace = message.get("trace")
            trace_items = trace if isinstance(trace, list) else []
            names = [
                str(item.get("text") or item.get("tool") or "").strip()
                for item in trace_items if isinstance(item, Mapping)
                if str(item.get("text") or item.get("tool") or "").strip()
            ]
            reasoning = str(message.get("reasoning") or "").strip()
            if names or reasoning:
                lines.extend(["## Agent activity", ""])
                if names:
                    lines.append("Tools: " + ", ".join(names))
                if reasoning:
                    lines.extend(["", reasoning])
                lines.append("")
            continue
        role = str(message.get("role") or "message").strip().capitalize()
        text = _message_text(message).strip()
        if not text:
            continue
        lines.extend([f"## {role}", "", text, ""])
    return "\n".join(lines).rstrip() + "\n"


class WorkbenchSessionPresentation:
    """Project Workbench chats and their ContextTrees into UI session records."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.context_directory = (
            workbench_agent_data_directory(self.db_path) / "context"
        )

    def _agent_state(self, chat_id: str) -> dict[str, Any]:
        if not (self.context_directory / "index.sqlite3").is_file():
            return {}
        try:
            return AgentContextRepository(self.context_directory).read(chat_id)
        except Exception:
            logger.debug(
                "Could not project ContextTree for Workbench chat %s",
                chat_id,
                exc_info=True,
            )
            return {}

    def _summary(
        self,
        chat: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        projection = chat.get("_messageProjection")
        projection = projection if isinstance(projection, Mapping) else {}
        message_count = _integer(projection.get("messageCount"))
        projected_usage = _usage(projection.get("usage"))
        tree_usage = _usage(state.get("usage"))
        usage = (
            tree_usage
            if tree_usage["total_tokens"] >= projected_usage["total_tokens"]
            else projected_usage
        )
        checkpoint = state.get("checkpoint")
        checkpoint = checkpoint if isinstance(checkpoint, Mapping) else None
        run_status = _run_status(chat, message_count, checkpoint)
        activity = state.get("activityMessages")
        activity_items = activity if isinstance(activity, list) else []
        tool_calls = sum(
            len(item.get("trace") or [])
            for item in activity_items if isinstance(item, Mapping)
        )
        subagents = _subagent_cards(state)
        return {
            "id": str(chat.get("id") or ""),
            "projectId": str(chat.get("projectId") or ""),
            "kind": str(chat.get("kind") or "chat"),
            "title": str(chat.get("title") or "New chat"),
            "status": _ui_status(run_status),
            "runStatus": run_status,
            "started": _started(chat.get("createdAt")),
            "createdAt": str(chat.get("createdAt") or ""),
            "updatedAt": str(chat.get("updatedAt") or state.get("updatedAt") or ""),
            "dur": _duration(chat.get("createdAt"), chat.get("updatedAt")),
            "preview": str(projection.get("preview") or ""),
            "model": str(state.get("model") or chat.get("lastModel") or chat.get("model") or ""),
            "messageCount": message_count,
            "completedTurnCount": _integer(
                projection.get("completedTurnCount", chat.get("completedTurnCount"))
            ),
            "usage": usage,
            "summary": {
                "tokens": _format_tokens(usage["total_tokens"]),
                "spend": "—",
                "toolCalls": tool_calls,
                "requests": _integer(
                    projection.get("completedTurnCount", chat.get("completedTurnCount"))
                ),
                "total_tokens": usage["total_tokens"],
            },
            "pendingQuestion": chat.get("pendingQuestion") or None,
            "usedPluginPacks": list(state.get("usedPluginPacks") or []),
            "usedStandalonePlugins": list(state.get("usedStandalonePlugins") or []),
            "subagents": subagents,
            "contextChips": _context_chips(state),
            "contextTree": {
                "treeId": str(state.get("treeId") or ""),
                "leafId": str(state.get("leafId") or ""),
                "runId": str((checkpoint or {}).get("run_id") or ""),
                "status": str((checkpoint or {}).get("status") or ""),
                "updatedAt": str(state.get("updatedAt") or ""),
            },
        }

    def list(self) -> list[dict[str, Any]]:
        chats = store.read_chat_summaries(
            self.db_path,
            _empty_chat_store,
        )
        source_chats = [
            chat
            for chat in chats
            if isinstance(chat, Mapping)
            and str(chat.get("kind") or "chat") == "chat"
            and str(chat.get("id") or "").strip()
        ]
        try:
            states = AgentContextRepository(self.context_directory).read_many(
                tuple(str(chat.get("id") or "") for chat in source_chats)
            )
        except Exception:
            logger.debug(
                "Could not batch-project Workbench ContextTrees",
                exc_info=True,
            )
            states = {}
        sessions = [
            self._summary(
                chat,
                states.get(str(chat.get("id") or ""), {}),
            )
            for chat in source_chats
        ]
        sessions.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return sessions

    def get(self, chat_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        target = str(chat_id or "").strip()
        chat = store.read_chat(self.db_path, target, _empty_chat_store)
        if not isinstance(chat, dict) or str(chat.get("kind") or "chat") != "chat":
            raise WorkbenchSessionError(
                localized("Conversation not found.", "未找到对话。"),
                404,
                "conversation_not_found",
            )
        return chat, self._agent_state(target)

    def _ensure_mutable(
        self,
        chat: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> None:
        projection = chat.get("_messageProjection")
        projection = projection if isinstance(projection, Mapping) else {}
        checkpoint = state.get("checkpoint")
        checkpoint = checkpoint if isinstance(checkpoint, Mapping) else None
        if _run_status(
            chat,
            _integer(projection.get("messageCount")),
            checkpoint,
        ) == "running":
            raise WorkbenchSessionError(
                localized(
                    "Cancel the running conversation before clearing it.",
                    "请先取消正在运行的对话，再清空内容。",
                ),
                409,
                "conversation_running",
            )

    def _delete_context_tree(self, chat_id: str) -> None:
        if not (self.context_directory / "index.sqlite3").is_file():
            return
        with ContextStoreRouter(self.context_directory) as router:
            try:
                router.delete_tree(str(chat_id))
            except TreeNotFoundError:
                return

    def _workspace_directory(self, chat: Mapping[str, Any]) -> str | None:
        override = str(chat.get("workspaceOverride") or "").strip()
        if override:
            return override
        project_id = str(chat.get("projectId") or "").strip()
        if not project_id:
            return None
        try:
            from cyrene.workbench.artifacts import artifact_runtime
            from cyrene.workbench.projects import project_runtime

            bundle = store.read_project_bundle(
                self.db_path,
                project_runtime._workbench_default_project,
                store.summarize_task_session,
                lightweight=True,
            )
            project = next(
                (
                    item
                    for item in bundle.get("projects") or ()
                    if isinstance(item, Mapping)
                    and str(item.get("id") or "") == project_id
                ),
                None,
            )
            root = artifact_runtime._workbench_workspace_root(
                dict(project) if isinstance(project, Mapping) else None
            )
            return str(root) if root is not None else None
        except Exception:
            logger.warning(
                "Could not resolve workspace for Workbench chat %s",
                str(chat.get("id") or ""),
                exc_info=True,
            )
            return None

    def _delete_memory_archive(self, chat: Mapping[str, Any]) -> int:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("memory")
        if service is None:
            return 0
        delete_archive = getattr(service, "delete_session_archive", None)
        if not callable(delete_archive):
            raise WorkbenchSessionError(
                localized(
                    "The Memory Plugin does not support deleting Workbench archives.",
                    "Memory 插件不支持删除工作台归档。",
                ),
                503,
                "memory_archive_delete_unavailable",
            )
        return int(
            bool(
                delete_archive(
                    str(chat.get("id") or ""),
                    self._workspace_directory(chat),
                )
            )
        )

    def clear(self, chat_id: str) -> tuple[dict[str, Any], int]:
        chat, state = self.get(chat_id)
        self._ensure_mutable(chat, state)
        deleted_archives = self._delete_memory_archive(chat)
        self._delete_context_tree(str(chat_id))

        def clear_record(record: dict[str, Any]) -> None:
            record["messages"] = []
            record["status"] = "idle"
            record["completedTurnCount"] = 0
            record["updatedAt"] = datetime.now(timezone.utc).isoformat()
            for key in (
                "activePlan",
                "lastModel",
                "lastRun",
                "pendingQuestion",
            ):
                record.pop(key, None)

        result = store.mutate_chat(
            self.db_path,
            str(chat_id),
            clear_record,
            _empty_chat_store,
        )
        if result is None:
            raise WorkbenchSessionError(
                localized("Conversation not found.", "未找到对话。"),
                404,
                "conversation_not_found",
            )
        return (
            self._summary(
                {
                    **result,
                    "_messageProjection": {"messageCount": 0, "usage": {}},
                },
                {},
            ),
            deleted_archives,
        )

    def delete(self, chat_id: str) -> int:
        chat, state = self.get(chat_id)
        self._ensure_mutable(chat, state)
        deleted_archives = self._delete_memory_archive(chat)
        self._delete_context_tree(str(chat_id))
        payload = store.read_chat_bundle(self.db_path, _empty_chat_store)
        before = len(payload.get("chats") or [])
        payload["chats"] = [
            item
            for item in payload.get("chats") or []
            if not (
                isinstance(item, Mapping)
                and str(item.get("id") or "") == str(chat_id)
            )
        ]
        if len(payload["chats"]) == before:
            raise WorkbenchSessionError(
                localized("Conversation not found.", "未找到对话。"),
                404,
                "conversation_not_found",
            )
        store.write_chat_bundle(self.db_path, payload, _empty_chat_store)
        return deleted_archives

    def export(self, chat_id: str, output_format: str) -> WorkbenchSessionExport:
        chat, state = self.get(chat_id)
        messages = _merge_activity_messages(
            [dict(item) for item in chat.get("messages") or [] if isinstance(item, Mapping)],
            state,
        )
        normalized = str(output_format or "markdown").strip().lower()
        if normalized in {"md", "markdown"}:
            content = _markdown(chat, messages).encode("utf-8")
            media_type = "text/markdown; charset=utf-8"
            extension = "md"
        elif normalized == "json":
            content = json.dumps(
                {
                    "session": self._summary(
                        {
                            **chat,
                            "_messageProjection": {
                                "messageCount": len(chat.get("messages") or []),
                                "usage": {},
                            },
                        },
                        state,
                    ),
                    "chat": {
                        **chat,
                        "messages": messages,
                    },
                    "contextTree": {
                        key: state.get(key)
                        for key in (
                            "treeId",
                            "rootId",
                            "leafId",
                            "usage",
                            "model",
                            "modelIdentity",
                            "usedPluginPacks",
                            "usedStandalonePlugins",
                            "compaction",
                            "createdAt",
                            "updatedAt",
                        )
                        if key in state
                    },
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ).encode("utf-8")
            media_type = "application/json; charset=utf-8"
            extension = "json"
        else:
            raise WorkbenchSessionError(
                localized(
                    "Export format must be Markdown or JSON.",
                    "导出格式必须是 Markdown 或 JSON。",
                ),
                400,
                "unsupported_export_format",
            )
        title = re.sub(r"[^A-Za-z0-9._-]+", "-", str(chat.get("title") or ""))
        filename = (title.strip("-.") or str(chat_id) or "conversation")[:80]
        return WorkbenchSessionExport(
            content=content,
            media_type=media_type,
            filename=f"{filename}.{extension}",
        )


__all__ = [
    "WorkbenchSessionError",
    "WorkbenchSessionExport",
    "WorkbenchSessionPresentation",
]
