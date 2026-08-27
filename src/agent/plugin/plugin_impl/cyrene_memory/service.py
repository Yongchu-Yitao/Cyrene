"""Session-scoped memory service and lifecycle Hooks for the memory pack."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.hook import CONTEXT_USED, SESSION_END, SESSION_START, STOP, ContextUsed, HookEvent
from agent.plugin import PluginContext, PluginSetupContext
from cyrene.localization import app_language, localized
from .definitions import MEMORY_TOOL_NAMES

MEMORY_SERVICE_ID = "memory.v1"

logger = logging.getLogger(__name__)


def _run_data(data: Mapping[str, Any]) -> dict[str, Any]:
    raw = data.get("run_context")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _bool(data: Mapping[str, Any], run_data: Mapping[str, Any], name: str, default: bool) -> bool:
    value = data.get(name, run_data.get(name))
    return default if value is None else bool(value)


@dataclass(slots=True)
class MemoryService:
    workspace: Path | None
    tree: Any
    tree_id: str
    data: Mapping[str, Any]
    model_gateway: Any = None
    agent_id: str = "main"
    parent_agent_id: str = ""
    _project_id: str | None = None
    _context_threshold: int = 0
    _background: set[Any] = field(default_factory=set, repr=False)

    @classmethod
    def from_setup(cls, context: PluginSetupContext) -> "MemoryService":
        return cls(
            workspace=context.workspace,
            tree=context.tree,
            tree_id=context.tree_id,
            data=dict(context.data),
            model_gateway=context.services.get("model"),
            agent_id=context.agent_id,
            parent_agent_id=context.parent_agent_id,
        )

    @classmethod
    def from_plugin_context(cls, context: PluginContext) -> "MemoryService":
        data = dict(context.data)
        run_data = _run_data(data)
        return cls(
            workspace=context.workspace,
            tree=context.tree,
            tree_id=str(context.tree_id or data.get("session_id") or ""),
            data=data,
            model_gateway=context.services.get("model"),
            agent_id=str(data.get("agent_id") or run_data.get("agent_id") or "main"),
            parent_agent_id=str(data.get("parent_agent_id") or ""),
        )

    @property
    def run_data(self) -> dict[str, Any]:
        return _run_data(self.data)

    @property
    def session_id(self) -> str:
        return str(self.data.get("session_id") or self.run_data.get("session_id") or self.tree_id or "").strip()

    @property
    def db_path(self) -> str:
        return str(self.data.get("db_path") or "").strip()

    @property
    def project_id(self) -> str | None:
        if self._project_id is not None:
            return self._project_id or None
        explicit = str(self.data.get("project_id") or self.run_data.get("project_id") or "").strip()
        if explicit:
            self._project_id = explicit
            return explicit
        resolved = ""
        if self.session_id:
            try:
                from cyrene.workbench import context as workbench_context

                if self.db_path:
                    workbench_context.configure_store(self.db_path)
                value = workbench_context.resolve_workbench_project_id_for_session(self.session_id)
                resolved = str(value or "").strip()
            except Exception:
                logger.debug("Could not resolve project memory scope", exc_info=True)
        self._project_id = resolved
        return resolved or None

    @property
    def is_main(self) -> bool:
        return self.agent_id == "main"

    @property
    def language(self) -> str:
        run_data = self.run_data
        return app_language(
            self.data.get("language")
            or run_data.get("language")
            or run_data.get("app_language")
        )

    def configure_stores(self) -> None:
        if not self.db_path:
            return
        from . import project_memory, structured

        structured.configure_store(self.db_path)
        project_memory.configure_store(self.db_path)

    def context_block(self) -> str:
        """Render the frozen memory context used by one Agent run."""

        parts: list[str] = []
        run_data = self.run_data
        language = self.language
        try:
            from .short_term import get_context

            short = get_context(
                max_chars=2500,
                header=localized(
                    "[Short-term cross-session memory:]",
                    "[跨会话短期记忆：]",
                    language=language,
                ),
            ).strip()
            if short:
                parts.append(short)
        except Exception:
            logger.exception("Failed to render short-term memory")

        project_id = self.project_id
        if project_id:
            self.configure_stores()
            try:
                from .structured import render_memory_for_injection

                structured = render_memory_for_injection(
                    project_id,
                    limit=20,
                    max_chars=2400,
                    header=localized(
                        "Project durable memories:",
                        "项目持久记忆：",
                        language=language,
                    ),
                    language=language,
                ).strip()
                if structured:
                    parts.append(structured)
            except Exception:
                logger.exception("Failed to render structured project memory")
            try:
                from .project_memory import (
                    build_main_agent_suffix,
                )

                raw_snapshot = self.data.get("project_memory_snapshot")
                snapshot = copy.deepcopy(dict(raw_snapshot)) if isinstance(raw_snapshot, Mapping) else None
                include_trigger = self.is_main and _bool(
                    self.data,
                    run_data,
                    "memory_trigger_enabled",
                    True,
                )
                prompt = build_main_agent_suffix(
                    snapshot,
                    include_trigger=include_trigger,
                    language=language,
                ).strip()
                if prompt:
                    parts.append(prompt)
            except Exception:
                logger.exception("Failed to render project-memory prompt")
        return "\n\n".join(part for part in parts if part).strip()

    def _path(self, node_id: str) -> tuple[Any, ...]:
        if self.tree is None or not self.tree_id or not node_id:
            return ()
        try:
            return tuple(self.tree.get_path(self.tree_id, node_id))
        except Exception:
            logger.debug("Could not read memory lifecycle path", exc_info=True)
            return ()

    def _node_value(self, node_id: str) -> dict[str, Any]:
        path = self._path(node_id)
        if not path or path[-1].id != node_id:
            return {}
        value = path[-1].value
        return dict(value) if isinstance(value, Mapping) else {}

    def messages(
        self,
        node_id: str,
        *,
        include_anchor: bool = True,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        path = self._path(node_id)
        if not include_anchor and path and path[-1].id == node_id:
            path = path[:-1]
        current_user = next(
            (node for node in reversed(path) if isinstance(node.value, Mapping) and node.value.get("role") == "user"),
            None,
        )
        current_run_id = str(current_user.value.get("run_id") if current_user is not None and isinstance(current_user.value, Mapping) else "")
        current_context_by_kind = {
            str(node.value.get("context_kind") or node.id): node.id
            for node in path
            if isinstance(node.value, Mapping) and node.value.get("role") == "context" and str(node.value.get("run_id") or "") == current_run_id
        }
        current_context_ids = set(current_context_by_kind.values())
        for node in path:
            value = node.value if isinstance(node.value, Mapping) else {}
            role = str(value.get("role") or "")
            if role in {"system", "user"}:
                messages.append({"role": role, "content": str(value.get("content") or "")})
            elif role == "context":
                if node.id not in current_context_ids:
                    continue
                self._append_system(messages, str(value.get("content") or ""))
            elif role == "assistant":
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": str(value.get("content") or ""),
                }
                calls = value.get("tool_calls")
                if isinstance(calls, list) and calls:
                    message["tool_calls"] = [
                        {
                            "id": str(call.get("id") or ""),
                            "type": "function",
                            "function": {
                                "name": str(call.get("name") or ""),
                                "arguments": json.dumps(
                                    call.get("arguments") or {},
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            },
                        }
                        for call in calls
                        if isinstance(call, Mapping)
                    ]
                    reasoning_details = value.get("reasoning_details")
                    if isinstance(reasoning_details, list) and reasoning_details:
                        message["reasoning_details"] = copy.deepcopy(reasoning_details)
                messages.append(message)
            elif role == "tool_results":
                results = value.get("results")
                for result in results if isinstance(results, list) else ():
                    if not isinstance(result, Mapping):
                        continue
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(result.get("call_id") or ""),
                            "name": str(result.get("name") or ""),
                            "content": json.dumps(
                                {
                                    "success": bool(result.get("success")),
                                    "value": result.get("value"),
                                    "error": str(result.get("error") or ""),
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )
        return messages

    @staticmethod
    def _append_system(messages: list[dict[str, Any]], content: str) -> None:
        clean = str(content or "").strip()
        if not clean:
            return
        system = next(
            (message for message in messages if message.get("role") == "system"),
            None,
        )
        if system is None:
            messages.insert(0, {"role": "system", "content": clean})
        else:
            existing = str(system.get("content") or "").strip()
            system["content"] = "\n\n".join(part for part in (existing, clean) if part)

    def verified_evidence(self, node_id: str, *, max_chars: int = 6000) -> str:
        names: dict[str, str] = {}
        blocks: list[str] = []
        used = 0
        for node in self._path(node_id):
            value = node.value if isinstance(node.value, Mapping) else {}
            if value.get("role") == "assistant":
                for call in value.get("tool_calls") or ():
                    if isinstance(call, Mapping):
                        names[str(call.get("id") or "")] = str(call.get("name") or "")
                continue
            if value.get("role") != "tool_results":
                continue
            for result in value.get("results") or ():
                if not isinstance(result, Mapping) or result.get("success") is not True:
                    continue
                call_id = str(result.get("call_id") or "")
                name = str(result.get("name") or names.get(call_id) or "").strip()
                if not name or name in MEMORY_TOOL_NAMES:
                    continue
                body = json.dumps(result.get("value"), ensure_ascii=False, default=str)
                block = f"[tool:{name} verified result]\n{body[:1600]}"
                if blocks and used + len(block) > max_chars:
                    return "\n\n".join(blocks)
                blocks.append(block)
                used += len(block)
        return "\n\n".join(blocks)[:max_chars]

    def _track(self, future: Any) -> None:
        if future is None:
            return
        self._background.add(future)

        def done(completed: Any) -> None:
            self._background.discard(completed)
            try:
                completed.result()
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                return
            except Exception:
                logger.debug("Memory background task failed", exc_info=True)

        future.add_done_callback(done)

    def _submit_background(self, awaitable: Awaitable[Any]) -> bool:
        submitter = self.data.get("background_submitter")
        if not callable(submitter):
            return False
        try:
            self._track(submitter(awaitable))
            return True
        except Exception:
            logger.exception("Failed to submit memory background task")
            return False

    def _call_owner(self, callback: Callable[[], Any]) -> Any:
        caller = self.data.get("owner_call")
        return caller(callback) if callable(caller) else callback()

    async def _proactive_conversation_context(self) -> str:
        """Return recent dialogue needed only by proactive Agent runs."""

        try:
            from .archive import get_recent_conversations

            conversations = str(await get_recent_conversations(days=1) or "")
        except Exception:
            logger.exception("Failed to render proactive conversation context")
            return ""
        if len(conversations) > 3000:
            conversations = conversations[-3000:]
            boundary = conversations.find("\n=== ")
            if boundary > 100:
                conversations = conversations[boundary + 1 :]
        conversations = conversations.strip()
        if not conversations:
            return ""
        header = localized(
            "## Recent conversation",
            "## 近期对话",
            language=self.language,
        )
        return header + "\n" + conversations

    async def on_session_start(self, event: HookEvent) -> dict[str, str]:
        parts = [self.context_block()]
        details = event.payload if isinstance(event.payload, Mapping) else {}
        metadata = details.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if bool(metadata.get("proactive")):
            parts.append(await self._proactive_conversation_context())
        context = "\n\n".join(part for part in parts if part).strip()
        return {"context": context} if context else {}

    async def on_context_used(self, event: HookEvent) -> None:
        usage = event.payload
        if not isinstance(usage, ContextUsed) or usage.usage_ratio <= 0:
            return
        reached = min(70, int(usage.usage_ratio * 100) // 10 * 10)
        if reached >= 20:
            self._context_threshold = max(self._context_threshold, reached)

    def _archive_completed_exchange(
        self,
        details: Mapping[str, Any],
        public_user: str,
        assistant_text: str,
        run_data: Mapping[str, Any],
    ) -> None:
        if not _bool(self.data, run_data, "memory_archive_enabled", True) or not self.is_main or not self.session_id:
            return
        try:
            from .archive import archive_session_exchange

            archive_session_exchange(
                self.session_id,
                public_user,
                assistant_text,
                workspace_dir=self.workspace,
                session_title=str(self.data.get("session_title") or ""),
                round_id=str(details.get("run_id") or ""),
                language=self.language,
            )
        except Exception:
            logger.exception("Failed to archive conversation from memory Plugin")

    def _persist_learning_snapshot(
        self,
        messages: list[dict[str, Any]],
        assistant_node_id: str,
        run_id: str,
        anchor_value: Mapping[str, Any],
        details: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        self.configure_stores()
        try:
            from .project_memory import persist_tree_context_snapshot

            return persist_tree_context_snapshot(
                self.session_id,
                str(self.project_id or ""),
                messages,
                tree_id=self.tree_id,
                tree_node_id=assistant_node_id,
                completed_turn_count=int(self.data.get("completed_turn_count") or 0),
                round_id=run_id,
                model={
                    "id": str(anchor_value.get("model") or details.get("model") or ""),
                    **(dict(anchor_value.get("model_identity") or {}) if isinstance(anchor_value.get("model_identity"), Mapping) else {}),
                },
                language=self.language,
            )
        except Exception:
            logger.exception("Could not persist ContextTree memory snapshot")
            return None

    async def on_session_end(self, event: HookEvent) -> None:
        details = event.payload if isinstance(event.payload, Mapping) else {}
        if str(details.get("status") or "") != "completed":
            return
        metadata = details.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        assistant_node_id = str(details.get("assistant_node_id") or "")
        public_user = str(metadata.get("public_user_message") if "public_user_message" in metadata else details.get("user_request") or "").strip()
        assistant_text = str(details.get("assistant_text") or "").strip()
        run_data = self.run_data
        self._archive_completed_exchange(
            details,
            public_user,
            assistant_text,
            run_data,
        )

        learning_enabled = _bool(
            self.data,
            run_data,
            "memory_write_enabled",
            self.is_main,
        )
        command = str(metadata.get("command") or run_data.get("command") or "").strip()
        retry = bool(self.data.get("retry") or metadata.get("retry"))
        path = self._path(assistant_node_id)
        anchor = path[-1] if path and path[-1].id == assistant_node_id else None
        anchor_value = dict(anchor.value) if anchor is not None and isinstance(anchor.value, Mapping) else {}
        run_id = str(anchor_value.get("run_id") or details.get("run_id") or "")
        tree_user = next(
            (node for node in reversed(path[:-1]) if isinstance(node.value, Mapping) and node.value.get("role") == "user" and str(node.value.get("run_id") or "") == run_id),
            None,
        )
        tree_user_text = str(tree_user.value.get("content") if tree_user is not None and isinstance(tree_user.value, Mapping) else "").strip()
        tree_assistant_text = str(anchor_value.get("content") or "").strip()
        if not learning_enabled or not self.is_main or command or retry or not self.project_id or anchor is None or not tree_user_text:
            return
        messages = self.messages(assistant_node_id)
        if not messages:
            return
        snapshot = self._persist_learning_snapshot(
            messages,
            assistant_node_id,
            run_id,
            anchor_value,
            details,
        )
        if snapshot is None:
            return
        evidence = self.verified_evidence(assistant_node_id)
        coroutine = self._capture_and_learn(
            tree_user_text,
            tree_assistant_text,
            messages,
            snapshot,
            evidence=evidence,
        )
        if not self._submit_background(coroutine):
            await coroutine

    async def _capture_and_learn(
        self,
        user_text: str,
        assistant_text: str,
        messages: list[dict[str, Any]],
        snapshot: dict[str, Any],
        *,
        evidence: str,
    ) -> None:
        project_id = self.project_id
        if not project_id:
            return
        self.configure_stores()
        try:
            from .structured import capture_from_exchange

            await capture_from_exchange(
                project_id,
                user_text,
                assistant_text,
                verified_evidence=evidence,
                model_gateway=self.model_gateway,
                session_id=self.session_id,
            )
        except Exception:
            logger.exception("Project memory capture failed")
        try:
            from .project_memory import (
                context_auto_trigger_threshold,
                schedule_learning,
            )

            if self._context_threshold:
                snapshot["observedContextThresholdPercent"] = self._context_threshold
            threshold = context_auto_trigger_threshold(
                project_id,
                self.session_id,
                messages,
                observed_percent=(self._context_threshold or None),
            )
            if threshold is not None:
                snapshot["contextThresholdPercent"] = threshold
                schedule_learning(
                    project_id,
                    snapshot,
                    source="conversation_auto",
                    reason=f"context_{threshold}_percent",
                    model_gateway=self.model_gateway,
                )
        except Exception:
            logger.exception("Project-memory prompt learning failed")

    def trigger_project_learning(self, reason: str, *, node_id: str) -> dict[str, Any]:
        language = self.language
        if not self.is_main:
            return {
                "status": "error",
                "type": "permission_denied",
                "message": localized(
                    "Only the main Agent can trigger project-memory learning.",
                    "只有主 Agent 可以触发项目记忆学习。",
                    language=language,
                ),
            }
        project_id = self.project_id
        if not project_id:
            return {
                "status": "error",
                "type": "not_found",
                "message": localized(
                    "Project-memory learning is only available in a Workbench project chat.",
                    "项目记忆学习仅可在 Workbench 项目对话中使用。",
                    language=language,
                ),
            }
        try:
            from cyrene.workbench.chat_repository import ChatRepository

            record = ChatRepository(self.db_path).get(self.session_id)
            if not record or str(record.get("kind") or "chat") != "chat":
                return {
                    "status": "error",
                    "type": "unsupported_chat_kind",
                    "message": localized(
                        "Only a root Workbench conversation can learn project memory.",
                        "只有 Workbench 根对话可以学习项目记忆。",
                        language=language,
                    ),
                }
        except Exception:
            logger.exception("Could not verify project-memory chat kind")
            return {
                "status": "error",
                "type": "context_unavailable",
                "message": localized(
                    "The Workbench conversation could not be verified.",
                    "无法验证当前 Workbench 对话。",
                    language=language,
                ),
            }
        anchor_value = self._node_value(node_id)
        if anchor_value.get("role") != "assistant":
            return {
                "status": "error",
                "type": "no_completed_context",
                "message": localized(
                    "Project-memory learning requires the current assistant tree node.",
                    "项目记忆学习需要当前助手树节点。",
                    language=language,
                ),
            }
        messages = self.messages(node_id, include_anchor=False)
        if not messages:
            return {
                "status": "error",
                "type": "no_completed_context",
                "message": localized(
                    "No current Agent context is available.",
                    "当前没有可用的 Agent 上下文。",
                    language=language,
                ),
            }
        self.configure_stores()
        from .project_memory import (
            persist_tree_context_snapshot,
            schedule_learning,
        )

        raw_identity = anchor_value.get("model_identity")
        model_identity = dict(raw_identity) if isinstance(raw_identity, Mapping) else {}
        snapshot = persist_tree_context_snapshot(
            self.session_id,
            project_id,
            messages,
            tree_id=self.tree_id,
            tree_node_id=node_id,
            completed_turn_count=int(self.data.get("completed_turn_count") or 0),
            round_id=str(anchor_value.get("run_id") or self.data.get("run_id") or self.run_data.get("round_id") or ""),
            model={
                "id": str(anchor_value.get("model") or ""),
                **model_identity,
            },
            language=language,
        )
        return self._call_owner(
            lambda: schedule_learning(
                project_id,
                snapshot,
                source="agent_tool",
                reason=str(reason or "high_value_evidence"),
                model_gateway=self.model_gateway,
            )
        )

    async def on_stop(self, _event: HookEvent) -> None:
        for pending in tuple(self._background):
            try:
                pending.cancel()
            except Exception:
                logger.debug("Failed to cancel memory task", exc_info=True)


def _bind_hook(
    context: PluginSetupContext,
    event: str,
    suffix: str,
    handler: Callable[[HookEvent], Any],
    *,
    root_only: bool = False,
) -> None:
    hook_id = f"cyrene-memory-{suffix}"
    plugin_id = f"cyrene_memory.{suffix}"
    existing = {hook.id for hook in context.hooks.list()}
    if hook_id in existing:
        context.hooks.bind_plugin(plugin_id, handler, replace=True)
        return
    context.hooks.register(
        event,
        handler,
        plugin_id=plugin_id,
        hook_id=hook_id,
        root_only=root_only,
    )


def setup_memory(context: PluginSetupContext) -> None:
    service = MemoryService.from_setup(context)
    from .archive import configure_archive
    from .short_term import init_short_term

    application_memory = context.services.get("memory")
    memory_data_directory = Path(
        str(
            getattr(application_memory, "data_directory", "")
            or context.data.get("memory_data_directory")
            or context.data_directory
        )
    ).expanduser().resolve()
    init_short_term(memory_data_directory, service.db_path)
    configure_archive(service.db_path)
    service.configure_stores()
    context.provide(MEMORY_SERVICE_ID, service, replace=True)
    _bind_hook(context, SESSION_START, "session_start", service.on_session_start, root_only=True)
    _bind_hook(context, CONTEXT_USED, "context_used", service.on_context_used)
    _bind_hook(context, SESSION_END, "session_end", service.on_session_end, root_only=True)
    _bind_hook(context, STOP, "stop", service.on_stop)


__all__ = ["MEMORY_SERVICE_ID", "MemoryService", "setup_memory"]
