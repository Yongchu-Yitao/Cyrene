"""Single durable ContextTree runtime for Workbench conversations."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..context import ContextError, ContextStoreRouter, TreeNotFoundError
from ..permission import runtime_permission_mode
from ..plugin import PluginRegistry, default_plugin_impl_directory
from ..plugin.model_gateway import ensure_model_router
from ..plugin.model_router import MODEL_ROUTER_PLUGIN
from ..prompt import DEFAULT_SYSTEM_PROMPT
from .bridge import (
    WorkbenchChatResult,
    WorkbenchPendingQuestion,
    WorkbenchPublisher,
    WorkbenchSessionBridge,
)


class _ThreadsafeConversationPublisher:
    """Marshal worker-loop Plugin events onto the Workbench owner loop."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        publish: WorkbenchPublisher,
    ) -> None:
        self._loop = loop
        self._publish = publish

    async def _send(self, event: dict[str, Any]) -> Any:
        result = self._publish(dict(event))
        if inspect.isawaitable(result):
            return await result
        return result

    async def __call__(self, event: dict[str, Any]) -> Any:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            return await self._send(event)
        future = asyncio.run_coroutine_threadsafe(self._send(event), self._loop)
        return await asyncio.wrap_future(future)


def _conversation_data_directory(
    db_path: str,
    override: str | Path | None = None,
) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    if str(db_path or "").strip():
        return Path(db_path).expanduser().resolve().parent / "agent-state"
    from cyrene.runtime.paths import USER_DATA_DIR

    return Path(USER_DATA_DIR).expanduser().resolve() / "agent-state"


@dataclass(frozen=True, slots=True)
class ConversationConfig:
    """Host values needed to reopen one Workbench conversation tree."""

    session_id: str
    workspace_dir: str
    db_path: str
    bot: Any = None
    host_chat_id: Any = None
    client_request_id: str = ""
    permission_mode: str = "default"
    command: str = ""
    public_user_message: str = ""
    attachment_paths: Mapping[str, str] = field(default_factory=dict)
    remote_device_ids: Sequence[str] = ()
    soul_enabled: bool | None = None
    workspace_enabled: bool | None = None
    system_extra: str = ""
    project_id: str = ""
    project_memory_snapshot: Mapping[str, Any] | None = None
    session_title: str = ""
    memory_write_enabled: bool = True
    memory_trigger_enabled: bool = True
    memory_archive_enabled: bool = True
    retry: bool = False
    completed_turn_count: int = 0
    response_capabilities: Sequence[str] = ()
    ui_instance_id: str = ""
    conversation_source: str = ""
    plugin_directory: str | Path | None = None
    data_directory: str | Path | None = None
    max_model_calls: int = 12


class ConversationRuntime:
    """Open, resume, answer, and cancel Workbench Agent trees by chat id.

    Bridges are process-local and short-lived. The ContextTree is the durable
    owner of the run, so an answer after a restart simply opens the same tree.
    """

    def __init__(self, db_path: str = "") -> None:
        self._db_path = str(db_path or "")
        self._active_lock = threading.RLock()
        self._active: dict[str, WorkbenchSessionBridge] = {}
        self._configs: dict[str, ConversationConfig] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def configure(self, db_path: str) -> None:
        with self._active_lock:
            if self._active:
                raise RuntimeError("cannot reconfigure ConversationRuntime while runs are active")
            self._db_path = str(db_path or "")

    def _state_root(self, config: ConversationConfig | None = None) -> Path:
        if config is not None and config.data_directory is not None:
            return _conversation_data_directory(config.db_path, config.data_directory)
        return _conversation_data_directory(self._db_path)

    def has_context(self, chat_id: str) -> bool:
        """Return whether a durable Agent tree exists without resuming it."""

        router = ContextStoreRouter(self._state_root() / "context")
        try:
            router.get_tree(str(chat_id))
            return True
        except TreeNotFoundError:
            return False
        finally:
            router.close()

    def context_checkpoint(self, chat_id: str) -> dict[str, Any] | None:
        """Read a tree's durable outcome without binding Plugins or resuming Hooks."""

        router = ContextStoreRouter(self._state_root() / "context")
        try:
            tree = router.get_tree(str(chat_id))
            nodes = router.get_subtree(tree.id, tree.root_id)
        except TreeNotFoundError:
            return None
        finally:
            router.close()
        dialogue = [
            node
            for node in nodes
            if isinstance(node.value, Mapping)
            and node.value.get("role")
            in {
                "system",
                "user",
                "context",
                "context_compaction",
                "assistant",
                "tool_results",
            }
        ]
        if not dialogue:
            return None
        leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
        by_id = {node.id: node for node in nodes}
        current = leaf
        run_id = ""
        while current is not None:
            value = current.value if isinstance(current.value, Mapping) else {}
            run_id = str(value.get("run_id") or "")
            if run_id:
                break
            current = by_id.get(str(current.parent_id or ""))
        value = leaf.value if isinstance(leaf.value, Mapping) else {}
        pending = value.get("pending_question")
        if (
            value.get("role") == "tool_results"
            and value.get("trigger_model") is False
            and isinstance(pending, Mapping)
            and str(pending.get("status") or "awaiting_user") == "awaiting_user"
        ):
            question = WorkbenchPendingQuestion.from_mapping(pending)
            return {
                "status": "awaiting_user",
                "run_id": run_id,
                "node_id": leaf.id,
                "pending_question": question,
                "active_plan": question.plan,
            }
        if value.get("cancelled") is True:
            status = "cancelled"
        elif value.get("error") is True:
            status = "failed"
        elif value.get("role") == "assistant" and value.get("session_end_complete") is True:
            status = "completed"
        elif (
            value.get("role") == "context_compaction"
            and value.get("resume_model") is not True
        ):
            status = "completed"
        else:
            status = "running"
        return {
            "status": status,
            "run_id": run_id,
            "node_id": leaf.id,
        }

    def fork_context(
        self,
        source_chat_id: str,
        target_chat_id: str,
        *,
        user_ordinal: int,
    ) -> dict[str, Any]:
        """Copy the active source prefix before one user turn into a new tree."""

        source_id = str(source_chat_id or "").strip()
        target_id = str(target_chat_id or "").strip()
        ordinal = int(user_ordinal)
        if not source_id or not target_id:
            raise ValueError("source and target chat ids are required")
        if source_id == target_id:
            raise ValueError("source and target chat ids must differ")
        if ordinal < 1:
            raise ValueError("user_ordinal must be at least one")

        router = ContextStoreRouter(self._state_root() / "context")
        try:
            source = router.get_tree(source_id)
            nodes = router.get_subtree(source.id, source.root_id)
            dialogue = [
                node
                for node in nodes
                if isinstance(node.value, Mapping)
                and node.value.get("role")
                in {
                    "system",
                    "user",
                    "context",
                    "context_compaction",
                    "assistant",
                    "tool_results",
                }
            ]
            if not dialogue:
                raise RuntimeError("source conversation has no durable dialogue")
            leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
            path = router.get_path(source.id, leaf.id)
            seen_users = 0
            cutoff = -1
            for index, node in enumerate(path):
                value = node.value if isinstance(node.value, Mapping) else {}
                if value.get("role") != "user":
                    continue
                seen_users += 1
                if seen_users == ordinal:
                    cutoff = index
                    break
            if cutoff < 0:
                raise LookupError("source user turn was not found")

            root_value = deepcopy(path[0].value)
            if isinstance(root_value, dict):
                root_value.pop("_cyrene_subagents", None)
            target = router.create_tree(
                root_value,
                tree_id=target_id,
                root_id=path[0].id,
            )
            parent_id = target.root_id
            copied = 0
            for node in path[1:cutoff]:
                router.mount(
                    target.id,
                    parent_id,
                    deepcopy(node.value),
                    node_id=node.id,
                )
                parent_id = node.id
                copied += 1
            return {
                "source_tree_id": source.id,
                "target_tree_id": target.id,
                "leaf_id": parent_id,
                "copied_nodes": copied,
                "user_ordinal": ordinal,
            }
        except ContextError:
            raise
        finally:
            router.close()

    def delete_context(self, chat_id: str) -> bool:
        """Delete one conversation tree and every recorded subagent tree."""

        router = ContextStoreRouter(self._state_root() / "context")
        deleted = False
        visited: set[str] = set()

        def delete_tree(tree_id: str) -> None:
            nonlocal deleted
            normalized = str(tree_id or "").strip()
            if not normalized or normalized in visited:
                return
            visited.add(normalized)
            try:
                tree = router.get_tree(normalized)
                root = router.get_node(tree.id, tree.root_id)
            except TreeNotFoundError:
                return
            value = root.value if isinstance(root.value, Mapping) else {}
            records = value.get("_cyrene_subagents")
            if isinstance(records, Mapping):
                for record in records.values():
                    if isinstance(record, Mapping):
                        delete_tree(str(record.get("tree_id") or ""))
            router.delete_tree(normalized)
            deleted = True

        try:
            delete_tree(str(chat_id))
            return deleted
        finally:
            router.close()

    def _chat_lock(self, chat_id: str) -> asyncio.Lock:
        target = str(chat_id)
        lock = self._locks.get(target)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[target] = lock
        return lock

    @staticmethod
    def _permission_mode(value: str) -> str:
        return runtime_permission_mode(value)

    def _open_bridge(
        self,
        config: ConversationConfig,
        *,
        owner_loop: asyncio.AbstractEventLoop,
        raw_publisher: WorkbenchPublisher | None,
    ) -> WorkbenchSessionBridge:
        from ..plugin import native_tools

        plugin_root = Path(
            config.plugin_directory or default_plugin_impl_directory()
        ).expanduser().resolve()
        native_tools.seed_builtin_plugin_directory(plugin_root)
        registry = PluginRegistry()
        ensure_model_router(registry)

        worker_publisher: WorkbenchPublisher | None = None
        if raw_publisher is not None:
            worker_publisher = _ThreadsafeConversationPublisher(
                owner_loop,
                raw_publisher,
            )

        def submit_background(awaitable: Awaitable[Any]):
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                return owner_loop.create_task(awaitable)
            return asyncio.run_coroutine_threadsafe(awaitable, owner_loop)

        def call_on_owner(callback: Callable[[], Any]) -> Any:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                return callback()
            result: concurrent.futures.Future[Any] = concurrent.futures.Future()

            def invoke() -> None:
                try:
                    result.set_result(callback())
                except BaseException as exc:
                    result.set_exception(exc)

            owner_loop.call_soon_threadsafe(invoke)
            return result.result(timeout=30)

        from agent.plugin import active_plugin_service
        from cyrene.runtime.schedule_runtime import get_schedule_runtime

        plugin_services: dict[str, Any] = {}
        knowledge_service = active_plugin_service("knowledge")
        memory_application = active_plugin_service("memory")
        map_service = active_plugin_service("maps")
        if knowledge_service is not None:
            plugin_services["knowledge"] = knowledge_service
        if map_service is not None:
            plugin_services["maps"] = map_service
        if str(config.db_path or "").strip():
            plugin_services["schedules"] = (
                active_plugin_service("schedules")
                or get_schedule_runtime(str(config.db_path), bot=config.bot)
            )

        run_context = {
            "agent_id": "main",
            "caller": "main_agent",
            "client_request_id": str(config.client_request_id or ""),
            "command": str(config.command or ""),
            "user_request_text": str(config.public_user_message or ""),
            "conversation_source": str(config.conversation_source or ""),
            "session_id": str(config.session_id),
            "ui_instance_id": str(config.ui_instance_id or ""),
            "workspace_dir": str(config.workspace_dir or ""),
            "soul_enabled": config.soul_enabled,
            "workspace_enabled": config.workspace_enabled,
            "permission_mode": self._permission_mode(config.permission_mode),
            "temporary_full_access": self._permission_mode(config.permission_mode) == "full_access",
            "response_capabilities": frozenset(
                str(item or "").strip()
                for item in config.response_capabilities
                if str(item or "").strip()
            ),
            "deep_research": str(config.command or "").strip() == "deep-research",
            "attachment_paths": dict(config.attachment_paths),
        }
        if worker_publisher is not None:
            run_context["reply_stream_writer"] = worker_publisher
            run_context["runtime_event_writer"] = worker_publisher

        state_root = self._state_root(config)
        return WorkbenchSessionBridge.open(
            state_root,
            config.workspace_dir,
            plugin_root,
            registry=registry,
            model_plugin=MODEL_ROUTER_PLUGIN,
            chat_id=str(config.session_id),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            host_context={
                "bot": config.bot,
                "chat_id": config.host_chat_id,
                "db_path": str(config.db_path or ""),
                "notify_state": None,
            },
            plugin_context_data={
                "session_id": str(config.session_id),
                "system_extra": str(config.system_extra or ""),
                "project_id": str(config.project_id or ""),
                "project_memory_snapshot": (
                    deepcopy(dict(config.project_memory_snapshot))
                    if isinstance(config.project_memory_snapshot, Mapping)
                    else None
                ),
                "session_title": str(config.session_title or ""),
                "remote_device_ids": tuple(
                    str(item or "").strip()
                    for item in config.remote_device_ids
                    if str(item or "").strip()
                ),
                "soul_enabled": config.soul_enabled,
                "memory_write_enabled": bool(config.memory_write_enabled),
                "memory_trigger_enabled": bool(config.memory_trigger_enabled),
                "memory_archive_enabled": bool(config.memory_archive_enabled),
                "retry": bool(config.retry),
                "completed_turn_count": max(0, int(config.completed_turn_count or 0)),
                "memory_data_directory": str(
                    getattr(memory_application, "data_directory", state_root)
                ),
                "background_submitter": submit_background,
                "owner_call": call_on_owner,
                "run_context": run_context,
            },
            plugin_services=plugin_services,
            max_model_calls=config.max_model_calls,
        )

    async def _with_bridge(
        self,
        config: ConversationConfig,
        operation: Callable[[WorkbenchSessionBridge], Awaitable[Any]],
        *,
        publish: WorkbenchPublisher | None,
    ) -> Any:
        chat_id = str(config.session_id or "").strip()
        if not chat_id:
            raise ValueError("session_id cannot be empty")
        owner_loop = asyncio.get_running_loop()
        async with self._chat_lock(chat_id):
            bridge = await asyncio.to_thread(
                self._open_bridge,
                config,
                owner_loop=owner_loop,
                raw_publisher=publish,
            )
            with self._active_lock:
                self._configs[chat_id] = config
                self._active[chat_id] = bridge
            try:
                return await operation(bridge)
            finally:
                with self._active_lock:
                    if self._active.get(chat_id) is bridge:
                        self._active.pop(chat_id, None)
                await asyncio.to_thread(bridge.close)

    async def send(
        self,
        config: ConversationConfig,
        text: str,
        *,
        run_id: str,
        metadata: Mapping[str, Any] | None = None,
        publish: WorkbenchPublisher | None = None,
    ) -> WorkbenchChatResult:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id cannot be empty")

        async def operate(bridge: WorkbenchSessionBridge) -> WorkbenchChatResult:
            snapshot = bridge.snapshot()
            status = str(snapshot.get("status") or "")
            restored_run_id = str(snapshot.get("run_id") or "")
            if status == "awaiting_user":
                if restored_run_id == normalized_run_id:
                    return bridge.pending_result(restored_run_id)
                raise RuntimeError("the conversation is awaiting a user answer")
            if status == "idle" and restored_run_id == normalized_run_id:
                return bridge.current_result(restored_run_id)
            if status != "idle":
                if restored_run_id == normalized_run_id:
                    return await bridge.resume_result(
                        publish=publish,
                        cancel_on_caller_cancel=False,
                    )
                await bridge.cancel("superseded_by_new_workbench_run")
            retry_branch = bool(
                config.retry
                and not (
                    isinstance(metadata, Mapping)
                    and metadata.get("fork_replay") is True
                )
            )
            if retry_branch:
                bridge.prepare_retry()
            return await bridge.submit_result(
                str(text or "").strip(),
                run_id=normalized_run_id,
                metadata=metadata,
                publish=publish,
                cancel_on_caller_cancel=False,
            )

        return await self._with_bridge(config, operate, publish=publish)

    async def answer(
        self,
        config: ConversationConfig,
        question_id: str,
        answer: str,
        *,
        publish: WorkbenchPublisher | None = None,
    ) -> WorkbenchChatResult:
        async def operate(bridge: WorkbenchSessionBridge) -> WorkbenchChatResult:
            return await bridge.answer_result(
                question_id,
                answer,
                publish=publish,
                cancel_on_caller_cancel=False,
            )

        return await self._with_bridge(config, operate, publish=publish)

    async def resume(
        self,
        config: ConversationConfig,
        *,
        publish: WorkbenchPublisher | None = None,
    ) -> WorkbenchChatResult:
        async def operate(bridge: WorkbenchSessionBridge) -> WorkbenchChatResult:
            return await bridge.resume_result(
                publish=publish,
                cancel_on_caller_cancel=False,
            )

        return await self._with_bridge(config, operate, publish=publish)

    async def compact(
        self,
        config: ConversationConfig,
        *,
        context_limit: int,
    ) -> dict[str, Any]:
        """Force idle-tree compaction with the same services as a Chat turn."""

        async def operate(bridge: WorkbenchSessionBridge) -> dict[str, Any]:
            return await bridge.compact(context_limit=context_limit)

        result = await self._with_bridge(config, operate, publish=None)
        return dict(result)

    def request_cancel(self, chat_id: str, reason: str = "user_cancelled") -> bool:
        with self._active_lock:
            bridge = self._active.get(str(chat_id))
        return bool(bridge is not None and bridge.session.request_cancel(reason))


__all__ = ["ConversationConfig", "ConversationRuntime"]
