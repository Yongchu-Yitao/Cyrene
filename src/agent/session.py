"""Production event-driven Agent session built from Context, Hook, and Plugin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .context import (
    ContextChange,
    ContextNode,
    ContextStoreRouter,
    NodeNotFoundError,
    TreeNotFoundError,
)
from .context.compaction import (
    COMPACT_BLOCK_PREFIX,
    COMPACT_TRIGGER_RATIO,
    compact_messages,
    message_token_estimate,
    messages_token_estimate,
    replace_compacted_summary,
)
from .hook import CONTEXT_CHANGE, SESSION_START, HookEvent, HookRegistration
from .observability import log_operation, operation
from .plugin import (
    PluginBatchRunner,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginRegistry,
    PluginRuntime,
    PluginSetupContext,
)
from .plugin.core_impl import (
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
    PermissionReviewPlugin,
)
from .prompt import DEFAULT_SYSTEM_PROMPT


logger = logging.getLogger(__name__)
_DEFAULT_INITIAL_ROOT = object()

AgentEventType = Literal[
    "session.state",
    "input.accepted",
    "input.answered",
    "assistant.tool_calls",
    "tool.completed",
    "tools.completed",
    "assistant.completed",
    "run.failed",
    "run.cancelled",
]


@dataclass(frozen=True, slots=True)
class AgentSessionEvent:
    """One serializable observation emitted by :class:`AgentSession`."""

    sequence: int
    type: AgentEventType
    tree_id: str
    run_id: str
    time: datetime
    node_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "tree_id": self.tree_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "time": self.time.isoformat(),
            "data": deepcopy(dict(self.data)),
        }


AgentEventListener = Callable[[AgentSessionEvent], None]


class AgentSession:
    """One tree whose passive trigger nodes advance the Agent state machine."""

    def __init__(
        self,
        data_directory: str | Path,
        workspace: str | Path,
        plugin_directory: str | Path,
        *,
        model_plugin: str = "MiniMax",
        max_model_calls: int = 12,
        tree_id: str = "demo",
        registry: PluginRegistry | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        host_context: Mapping[str, Any] | None = None,
        plugin_context_data: Mapping[str, Any] | None = None,
        plugin_services: Mapping[str, Any] | None = None,
        initial_root_value: Any = _DEFAULT_INITIAL_ROOT,
        agent_id: str = "main",
        parent_agent_id: str = "",
        subagent_manager: Any = None,
        load_plugins: bool = True,
        permission_user_request: str | None = None,
    ) -> None:
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.plugin_directory = Path(plugin_directory).expanduser().resolve()
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._system_prompt = (
            str(system_prompt or DEFAULT_SYSTEM_PROMPT).replace(
                "{workspace}", str(self.workspace)
            )
            if initial_root_value is _DEFAULT_INITIAL_ROOT
            else ""
        )
        self.agent_id = str(agent_id or "main").strip() or "main"
        self.parent_agent_id = str(parent_agent_id or "").strip()
        self._permission_user_request = (
            None
            if permission_user_request is None
            else str(permission_user_request)
        )
        self.registry = registry or PluginRegistry()
        failures = (
            self.registry.load_directory(self.plugin_directory)
            if load_plugins
            else ()
        )
        if failures:
            logger.warning(
                "Some optional Plugin contributions failed to load: %s",
                "; ".join(
                    f"{failure.path.name}: {failure.error}" for failure in failures
                ),
            )
        self._initial_plugin_load_failures = failures
        self._model_tools = self.registry.direct_tool_definitions(agent_id=self.agent_id)
        model = self.registry.resolve(model_plugin)
        if model.kind != "model":
            raise ValueError(f"Plugin is not a model component: {model_plugin}")
        self.model_plugin = model_plugin
        self.runtime = PluginRuntime(self.registry)
        self.batch = PluginBatchRunner(self.runtime)
        self._plugin_context_data = {
            **dict(host_context or {}),
            **dict(plugin_context_data or {}),
        }
        self._plugin_service_values = dict(plugin_services or {})
        if "model" not in self._plugin_service_values:
            from .plugin.model_gateway import PluginModelGateway

            self._plugin_service_values["model"] = PluginModelGateway(
                self.registry,
                self.runtime,
            )
        self.store = ContextStoreRouter(self.data_directory / "context")
        self._tree_id_hint = str(tree_id or "demo")
        self._state_lock = threading.RLock()
        self._context_event_deferral = threading.local()
        self._event_lock = threading.RLock()
        self._event_sequence = 0
        self._event_listeners: dict[int, AgentEventListener] = {}
        self._next_event_listener_id = 1
        self._status = "idle"
        self._detail = "Ready"
        self._leaf_id = "root"
        self._current_user_request = ""
        self._current_run_id = ""
        self._run_permission_user_request = ""
        self._cancelled_run_ids: set[str] = set()
        self._model_calls = 0
        self._max_model_calls = max(1, int(max_model_calls))
        self._subagent_manager = subagent_manager
        self._owns_subagent_manager = False
        self._closed = False
        self._transition_condition = threading.Condition(threading.RLock())
        self._transition_pending: set[str] = set()
        self._transition_work: queue.Queue[tuple[str, ContextNode] | None] = queue.Queue()
        self._transition_loop: asyncio.AbstractEventLoop | None = None
        self._active_transition_task: asyncio.Task[None] | None = None
        self._active_transition_run_id = ""
        self._transition_thread = threading.Thread(
            target=self._transition_worker_main,
            name=f"agent-transition-{tree_id}",
            daemon=True,
        )

        permission = PermissionReviewPlugin(
            self._permission_model,
            user_request=lambda _event: self.permission_user_request,
        )
        normalized_tree_id = self._tree_id_hint
        try:
            self.tree = self.store.get_tree(normalized_tree_id)
        except TreeNotFoundError:
            root_value = (
                {
                    "role": "system",
                    "content": self._system_prompt,
                }
                if initial_root_value is _DEFAULT_INITIAL_ROOT
                else deepcopy(initial_root_value)
            )
            self.tree = self.store.create_tree(
                root_value,
                tree_id=normalized_tree_id,
                root_id=self._leaf_id,
                initial_hooks=(
                    HookRegistration(
                        event=CONTEXT_CHANGE,
                        plugin_id="agent.session.context_mount",
                        plugin=self._context_mount_changed,
                        hook_id="agent-session-context-mount",
                    ),
                    HookRegistration(
                        event=CONTEXT_CHANGE,
                        plugin_id="agent.session.transition",
                        plugin=self._context_changed,
                        hook_id="agent-session-transition",
                    ),
                    permission.registration(),
                ),
            )
        root_node = self.store.get_node(self.tree.id, self.tree.root_id)
        self._initial_root_value = deepcopy(root_node.value)
        if isinstance(self._initial_root_value, dict):
            self._initial_root_value.pop("_cyrene_subagents", None)
            if self._system_prompt:
                self._initial_root_value["role"] = "system"
                self._initial_root_value["content"] = self._system_prompt
        self.hooks = self.store.hooks_for(self.tree.id)
        existing_hooks = {hook.id for hook in self.hooks.list()}
        if "agent-session-context-mount" in existing_hooks:
            self.hooks.bind_plugin(
                "agent.session.context_mount",
                self._context_mount_changed,
                replace=True,
            )
        else:
            self.hooks.register(
                CONTEXT_CHANGE,
                self._context_mount_changed,
                plugin_id="agent.session.context_mount",
                hook_id="agent-session-context-mount",
            )
        if "agent-session-transition" in existing_hooks:
            self.hooks.bind_plugin(
                "agent.session.transition",
                self._context_changed,
                replace=True,
            )
        else:
            self.hooks.register(
                CONTEXT_CHANGE,
                self._context_changed,
                plugin_id="agent.session.transition",
                hook_id="agent-session-transition",
            )
        if "core-permission-review" in existing_hooks:
            self.hooks.bind_plugin("core.permission", permission, replace=True)
        else:
            registration = permission.registration()
            self.hooks.register(
                registration.event,
                registration.plugin,
                plugin_id=registration.plugin_id,
                hook_id=registration.hook_id,
                root_only=registration.root_only,
                matcher=registration.matcher,
                failure_policy=registration.failure_policy,
                config=registration.config,
                enabled=registration.enabled,
            )
        if self._system_prompt and isinstance(root_node.value, Mapping):
            current_root = dict(root_node.value)
            if (
                current_root.get("role") != "system"
                or str(current_root.get("content") or "") != self._system_prompt
            ):
                current_root["role"] = "system"
                current_root["content"] = self._system_prompt
                root_node = self.store.update_node(
                    self.tree.id,
                    self.tree.root_id,
                    current_root,
                )
                self._initial_root_value = deepcopy(current_root)
                self._initial_root_value.pop("_cyrene_subagents", None)
        self._attach_plugin_packs()
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        self._event_sequence = sum(
            self._event_for_node(node, sequence=0) is not None for node in nodes
        )
        self._unsubscribe_context_events = self.store.subscribe(
            self._context_output_changed,
            tree_id=self.tree.id,
        )
        self._restore()
        if self._subagent_manager is None and self.agent_id == "main":
            from .subagent import SubagentManager

            self._subagent_manager = SubagentManager(self)
            self._owns_subagent_manager = True
        log_operation(
            logger,
            "agent.session",
            "initialize",
            phase="completed",
            tree_id=self.tree.id,
            root_id=self.tree.root_id,
            workspace=self.workspace,
            model_plugin=self.model_plugin,
            max_model_calls=self._max_model_calls,
            plugin_directory=self.plugin_directory,
            plugin_count=len(self.registry.list_plugins()),
            restored_status=self._status,
            restored_leaf_id=self._leaf_id,
            restored_run_id=self._current_run_id,
        )
        self._transition_thread.start()
        if self._owns_subagent_manager:
            self._subagent_manager.restore()

    @property
    def current_user_request(self) -> str:
        with self._state_lock:
            return self._current_user_request

    @property
    def current_run_id(self) -> str:
        with self._state_lock:
            return self._current_run_id

    @property
    def is_idle(self) -> bool:
        with self._state_lock:
            return self._status == "idle"

    @property
    def is_awaiting_user(self) -> bool:
        with self._state_lock:
            return self._status == "awaiting_user"

    @property
    def max_model_calls(self) -> int:
        return self._max_model_calls

    @property
    def permission_user_request(self) -> str:
        if self._permission_user_request is not None:
            return self._permission_user_request
        with self._state_lock:
            return self._run_permission_user_request or self._current_user_request

    @property
    def initial_root_value(self) -> Any:
        return deepcopy(self._initial_root_value)

    @property
    def subagent_manager(self) -> Any:
        return self._subagent_manager

    @property
    def plugin_context_data(self) -> dict[str, Any]:
        """Return the host data included in every Plugin invocation."""

        return dict(self._plugin_context_data)

    @property
    def plugin_services(self) -> dict[str, Any]:
        """Return host-owned services inherited by child Agent sessions."""

        return dict(self._plugin_service_values)

    def _plugin_data(self, *, run_id: str = "", **details: Any) -> dict[str, Any]:
        data = dict(self._plugin_context_data)
        caller = "main_agent"
        if self.agent_id != "main":
            caller = f"subagent_{self.agent_id}"
            data["agent_id"] = self.agent_id
            data["parent_agent_id"] = self.parent_agent_id
            data["caller"] = caller
        if run_id:
            data["run_id"] = run_id
        raw_run_context = data.get("run_context")
        if isinstance(raw_run_context, Mapping):
            run_context = dict(raw_run_context)
            run_context["agent_id"] = self.agent_id
            run_context["caller"] = caller
            if run_id:
                run_context["round_id"] = run_id
            data["run_context"] = run_context
        data.update(details)
        return data

    def _attach_plugin_packs(self) -> None:
        """Attach session services and Hooks contributed by loaded Plugin packs."""

        context = PluginSetupContext(
            data_directory=self.data_directory,
            plugin_directory=self.plugin_directory,
            workspace=self.workspace,
            tree=self.store,
            tree_id=self.tree.id,
            root_id=self.tree.root_id,
            hooks=self.hooks,
            data=self._plugin_data(),
            services=self._plugin_service_values,
            agent_id=self.agent_id,
            parent_agent_id=self.parent_agent_id,
        )
        for pack in self.registry.list_packs():
            if pack.setup is None or not self.registry.pack_enabled(pack.id):
                continue
            try:
                pack.setup(context)
            except Exception:
                logger.exception(
                    "Failed to attach Plugin pack %s to Agent session %s",
                    pack.id,
                    self.tree.id,
                )

    def _plugin_services(self) -> dict[str, Any]:
        services = dict(self._plugin_service_values)
        if self._subagent_manager is not None:
            services["subagents"] = self._subagent_manager
        return services

    def subscribe(
        self,
        listener: AgentEventListener,
        *,
        replay: bool = False,
        after_sequence: int = 0,
    ) -> Callable[[], None]:
        """Subscribe to structured output without coupling the Agent to Workbench."""

        if not callable(listener):
            raise TypeError("listener must be callable")
        if replay:
            replayed = self.events(after_sequence=after_sequence)
            for event in replayed:
                listener(event)
        else:
            replayed = ()
        with self._event_lock:
            listener_id = self._next_event_listener_id
            self._next_event_listener_id += 1
            self._event_listeners[listener_id] = listener
        log_operation(
            logger,
            "agent.session",
            "subscribe",
            phase="completed",
            tree_id=self.tree.id,
            listener_id=listener_id,
            listener=getattr(listener, "__qualname__", type(listener).__qualname__),
            replay=replay,
            after_sequence=after_sequence,
            replayed=len(replayed),
        )

        def unsubscribe() -> None:
            with self._event_lock:
                removed = self._event_listeners.pop(listener_id, None) is not None
            log_operation(
                logger,
                "agent.session",
                "unsubscribe",
                phase="completed",
                tree_id=self.tree.id,
                listener_id=listener_id,
                removed=removed,
            )

        return unsubscribe

    def _emit_event(
        self,
        event_type: AgentEventType,
        *,
        run_id: str = "",
        node_id: str | None = None,
        time: datetime | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> AgentSessionEvent:
        with self._event_lock:
            self._event_sequence += 1
            event = AgentSessionEvent(
                sequence=self._event_sequence,
                type=event_type,
                tree_id=self.tree.id,
                run_id=str(run_id or ""),
                node_id=node_id,
                time=time or datetime.now(timezone.utc),
                data=deepcopy(dict(data or {})),
            )
            listeners = tuple(self._event_listeners.values())
        log_operation(
            logger,
            "agent.session",
            "emit_event",
            phase="started",
            tree_id=self.tree.id,
            run_id=event.run_id,
            node_id=event.node_id,
            sequence=event.sequence,
            event_type=event.type,
            event_time=event.time,
            data=event.data,
            listener_count=len(listeners),
        )
        delivered = 0
        failed = 0
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:
                failed += 1
                log_operation(
                    logger,
                    "agent.session",
                    "notify_listener",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    tree_id=self.tree.id,
                    run_id=event.run_id,
                    node_id=event.node_id,
                    sequence=event.sequence,
                    event_type=event.type,
                    listener=getattr(listener, "__qualname__", type(listener).__qualname__),
                    error=exc,
                )
            else:
                delivered += 1
        log_operation(
            logger,
            "agent.session",
            "emit_event",
            phase="completed",
            tree_id=self.tree.id,
            run_id=event.run_id,
            node_id=event.node_id,
            sequence=event.sequence,
            event_type=event.type,
            delivered=delivered,
            failed=failed,
        )
        return event

    @staticmethod
    def _event_for_node(
        node: ContextNode,
        *,
        sequence: int,
    ) -> AgentSessionEvent | None:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        event_type: AgentEventType
        if role == "user":
            event_type = "input.accepted"
        elif role == "tool_results":
            event_type = "tools.completed"
        elif role == "assistant" and value.get("cancelled") is True:
            event_type = "run.cancelled"
        elif role == "assistant" and value.get("error") is True:
            event_type = "run.failed"
        elif role == "assistant" and value.get("tool_calls"):
            event_type = "assistant.tool_calls"
        elif role == "assistant":
            event_type = "assistant.completed"
        else:
            return None
        return AgentSessionEvent(
            sequence=sequence,
            type=event_type,
            tree_id=node.tree_id,
            run_id=str(value.get("run_id") or ""),
            node_id=node.id,
            time=node.created_at,
            data=deepcopy(dict(value)),
        )

    def events(self, *, after_sequence: int = 0) -> tuple[AgentSessionEvent, ...]:
        """Rebuild durable output events from the Context Tree.

        Transient ``session.state`` and per-tool progress events are deliberately
        omitted; a reconnect receives the durable node projection plus
        :meth:`snapshot` for the current state.
        """

        events: list[AgentSessionEvent] = []
        for node in self.store.get_subtree(self.tree.id, self.tree.root_id):
            event = self._event_for_node(node, sequence=len(events) + 1)
            if event is not None:
                events.append(event)
        result = tuple(event for event in events if event.sequence > int(after_sequence))
        log_operation(
            logger,
            "agent.session",
            "events",
            phase="completed",
            tree_id=self.tree.id,
            after_sequence=after_sequence,
            count=len(result),
            event_types=[event.type for event in result],
        )
        return result

    def _context_output_changed(self, change: ContextChange) -> None:
        if change.action != "mount":
            return
        pending = getattr(self._context_event_deferral, "changes", None)
        if pending is not None:
            pending.append(change)
            return
        self._publish_context_output(change)

    def _publish_context_output(self, change: ContextChange) -> None:
        try:
            node = self.store.get_node(change.tree_id, change.node_id)
        except Exception:
            return
        value = node.value if isinstance(node.value, Mapping) else {}
        run_id = str(value.get("run_id") or "")
        if (
            run_id
            and value.get("cancelled") is not True
            and self._is_cancelled(run_id)
        ):
            return
        projected = self._event_for_node(node, sequence=0)
        if projected is None:
            return
        self._emit_event(
            projected.type,
            run_id=projected.run_id,
            node_id=node.id,
            time=change.time,
            data=projected.data,
        )

    @contextmanager
    def _linearized_context_commit(self):
        """Serialize a durable run commit with cancellation.

        ContextStore publishes committed mounts synchronously.  Deferring this
        session's node projection until after ``_state_lock`` is released keeps
        output listeners from re-entering cancellation in the middle of a
        commit while retaining ContextStore's normal publication semantics.
        """

        pending = getattr(self._context_event_deferral, "changes", None)
        outermost = pending is None
        if outermost:
            self._context_event_deferral.changes = []
        try:
            with self._state_lock:
                yield
        finally:
            if outermost:
                changes = tuple(self._context_event_deferral.changes)
                del self._context_event_deferral.changes
                for change in changes:
                    self._publish_context_output(change)

    def _set_state_locked(
        self,
        status: str,
        detail: str,
        *,
        leaf_id: str | None = None,
    ) -> tuple[str, str, str, str]:
        self._status = status
        self._detail = detail
        if leaf_id is not None:
            self._leaf_id = leaf_id
        return status, detail, self._current_run_id, self._leaf_id

    def _emit_state_snapshot(self, snapshot: tuple[str, str, str, str]) -> None:
        status, detail, run_id, leaf_id = snapshot
        with self._state_lock:
            if (
                self._status != status
                or self._detail != detail
                or self._current_run_id != run_id
                or self._leaf_id != leaf_id
            ):
                return
        self._emit_event(
            "session.state",
            run_id=run_id,
            node_id=leaf_id,
            data={"status": status, "detail": detail, "leaf_id": leaf_id},
        )

    def _set_state(self, status: str, detail: str, *, leaf_id: str | None = None) -> None:
        with self._state_lock:
            snapshot = self._set_state_locked(status, detail, leaf_id=leaf_id)
        self._emit_state_snapshot(snapshot)

    @staticmethod
    def _stable_id(prefix: str, key: str) -> str:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}_{digest}"

    @staticmethod
    def _transition_key(node: ContextNode) -> str:
        return f"{node.id}:{node.updated_at.isoformat()}"

    def _node_run_id(self, node: ContextNode) -> str:
        value = node.value if isinstance(node.value, Mapping) else {}
        run_id = str(value.get("run_id") or "")
        if run_id:
            return run_id
        try:
            path = self.store.get_path(node.tree_id, node.id)
        except Exception:
            return ""
        for ancestor in reversed(path):
            ancestor_value = ancestor.value if isinstance(ancestor.value, Mapping) else {}
            run_id = str(ancestor_value.get("run_id") or "")
            if run_id:
                return run_id
        return ""

    def _permission_request_for_run(self, run_id: str) -> str:
        target_run_id = str(run_id or "")
        if not target_run_id:
            return ""
        fallback = ""
        nodes = sorted(
            self.store.get_subtree(self.tree.id, self.tree.root_id),
            key=lambda item: (item.created_at, item.id),
        )
        for node in nodes:
            value = node.value if isinstance(node.value, Mapping) else {}
            if (
                value.get("role") != "user"
                or str(value.get("run_id") or "") != target_run_id
            ):
                continue
            authorization = str(value.get("authorization_request") or "")
            if authorization:
                return authorization
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if str(metadata.get("source") or "") != "agent_inbox" and not fallback:
                fallback = str(value.get("content") or "")
        return fallback

    def _is_cancelled(self, run_id: str) -> bool:
        with self._state_lock:
            return bool(run_id and run_id in self._cancelled_run_ids)

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

    @staticmethod
    def _decoded_plugin_value(value: Any) -> Any:
        """Decode JSON-shaped Plugin output without changing ordinary strings."""

        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped or stripped[0] not in "[{":
            return value
        try:
            return json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value

    @staticmethod
    def _question_options(raw: Any) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for index, item in enumerate(raw if isinstance(raw, list) else (), start=1):
            if isinstance(item, Mapping):
                label = next(
                    (
                        str(item.get(key) or "").strip()
                        for key in ("label", "text", "value", "title", "name")
                        if str(item.get(key) or "").strip()
                    ),
                    "",
                )
                option_id = str(item.get("id") or "").strip()
            else:
                label = str(item or "").strip()
                option_id = ""
            if not label:
                continue
            options.append(
                {
                    "id": option_id or f"option_{index}",
                    "label": label,
                }
            )
            if len(options) >= 6:
                break
        return options

    def _pending_question_from_results(
        self,
        calls: list[Any],
        results: tuple[PluginCallResult, ...],
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        calls_by_id = {
            str(call.get("id") or ""): call
            for call in calls
            if isinstance(call, Mapping) and str(call.get("id") or "")
        }
        for result in results:
            if not result.success:
                continue
            call = calls_by_id.get(str(result.call_id), {})
            arguments = call.get("arguments") if isinstance(call, Mapping) else {}
            arguments = arguments if isinstance(arguments, Mapping) else {}
            tool_name = str(result.name or call.get("name") or "")
            payload = self._decoded_plugin_value(result.value)
            if (
                isinstance(payload, Mapping)
                and str(payload.get("operation") or "") == "invoke"
            ):
                tool_name = str(
                    payload.get("name") or arguments.get("name") or tool_name
                )
                nested_arguments = arguments.get("arguments")
                arguments = (
                    nested_arguments
                    if isinstance(nested_arguments, Mapping)
                    else {}
                )
                payload = self._decoded_plugin_value(payload.get("result"))
            if (
                not isinstance(payload, Mapping)
                or str(payload.get("status") or "") != "awaiting_user"
            ):
                continue
            kind = str(payload.get("kind") or "").strip()
            text = str(payload.get("text") or arguments.get("text") or "").strip()
            raw_options = payload.get("options")
            if not isinstance(raw_options, list):
                raw_options = arguments.get("options")
            allow_custom = (
                bool(payload.get("allow_custom"))
                if isinstance(payload.get("allow_custom"), bool)
                else True
            )
            if tool_name == "enter_plan_mode" or kind == "plan_confirmation":
                kind = "plan_confirmation"
                text = text or "计划已准备好，是否同意并开始执行？"
                raw_options = raw_options if isinstance(raw_options, list) and raw_options else [
                    "同意并开始",
                    "拒绝",
                ]
            elif tool_name == "browser_request_takeover" or payload.get("takeover") is True:
                kind = kind or "browser_takeover"
                text = str(arguments.get("reason") or text).strip() or "请在浏览器窗口完成操作，然后确认继续。"
                raw_options = raw_options if isinstance(raw_options, list) and raw_options else ["我已完成"]
                allow_custom = False
            else:
                kind = kind or "clarification"
                text = text or "请补充继续处理所需的信息。"
            question: dict[str, Any] = {
                "status": "awaiting_user",
                "id": str(payload.get("question_id") or payload.get("id") or "").strip()
                or f"question_{str(result.call_id)[:24]}",
                "text": text,
                "options": self._question_options(raw_options),
                "allow_custom": allow_custom,
                "kind": kind,
                "round_id": str(payload.get("round_id") or run_id),
                "client_request_id": str(payload.get("client_request_id") or ""),
                "asked_at": result.time.isoformat(),
                "call_id": str(result.call_id),
                "tool_name": tool_name,
            }
            plan = payload.get("plan")
            if isinstance(plan, (Mapping, list)):
                question["plan"] = self._json_value(plan)
            return question
        return None

    @staticmethod
    def _pending_from_node(node: ContextNode) -> dict[str, Any] | None:
        value = node.value if isinstance(node.value, Mapping) else {}
        pending = value.get("pending_question")
        if (
            value.get("role") != "tool_results"
            or value.get("trigger_model") is not False
            or not isinstance(pending, Mapping)
            or str(pending.get("status") or "awaiting_user") != "awaiting_user"
        ):
            return None
        return deepcopy(dict(pending))

    def pending_output(self) -> dict[str, Any] | None:
        """Return the newest unanswered question stored in the ContextTree."""

        candidates: list[tuple[ContextNode, dict[str, Any]]] = []
        for node in self.store.get_subtree(self.tree.id, self.tree.root_id):
            pending = self._pending_from_node(node)
            if pending is not None:
                candidates.append((node, pending))
        if not candidates:
            return None
        node, pending = max(candidates, key=lambda item: (item[0].updated_at, item[0].id))
        return {"node_id": node.id, **pending}

    def _transition_assistant(self, trigger: ContextNode) -> ContextNode | None:
        key = self._transition_key(trigger)
        for child in self.store.get_children(self.tree.id, trigger.id):
            value = child.value if isinstance(child.value, Mapping) else {}
            if value.get("role") == "assistant" and value.get("caused_by") == key:
                return child
        return None

    def _batch_result_node(self, assistant: ContextNode) -> ContextNode | None:
        batch_key = str(
            (assistant.value if isinstance(assistant.value, Mapping) else {}).get(
                "batch_key"
            )
            or ""
        )
        for child in self.store.get_children(self.tree.id, assistant.id):
            value = child.value if isinstance(child.value, Mapping) else {}
            if value.get("role") == "tool_results" and value.get("caused_by") == batch_key:
                return child
        return None

    def _enqueue_transition(self, kind: str, node: ContextNode) -> None:
        key = f"{kind}:{self._transition_key(node)}"
        run_id = self._node_run_id(node)
        cancelled = self._is_cancelled(run_id)
        with self._transition_condition:
            if self._closed or cancelled or key in self._transition_pending:
                log_operation(
                    logger,
                    "agent.session",
                    "enqueue_transition",
                    phase="skipped",
                    tree_id=self.tree.id,
                    run_id=run_id,
                    node_id=node.id,
                    transition_kind=kind,
                    transition_key=key,
                    closed=self._closed,
                    cancelled=cancelled,
                    duplicate=key in self._transition_pending,
                )
                return
            self._transition_pending.add(key)
            self._transition_work.put((kind, node))
            self._transition_condition.notify_all()
        log_operation(
            logger,
            "agent.session",
            "enqueue_transition",
            phase="queued",
            tree_id=self.tree.id,
            run_id=run_id,
            node_id=node.id,
            transition_kind=kind,
            transition_key=key,
        )

    def _transition_worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._transition_condition:
            self._transition_loop = loop
            self._transition_condition.notify_all()
        log_operation(
            logger,
            "agent.session",
            "transition_worker",
            phase="started",
            tree_id=self._tree_id_hint,
            thread=threading.current_thread().name,
        )
        try:
            while True:
                item = self._transition_work.get()
                if item is None:
                    return
                kind, node = item
                key = f"{kind}:{self._transition_key(node)}"
                run_id = self._node_run_id(node)
                try:
                    cancelled = self._is_cancelled(run_id)
                    with self._transition_condition:
                        if self._closed or cancelled:
                            log_operation(
                                logger,
                                "agent.session",
                                "transition",
                                phase="skipped",
                                tree_id=self.tree.id,
                                run_id=run_id,
                                node_id=node.id,
                                transition_kind=kind,
                                transition_key=key,
                                closed=self._closed,
                                cancelled=cancelled,
                            )
                            continue
                    if kind == "advance":
                        coroutine = self._advance(node)
                    elif kind == "tools":
                        coroutine = self._continue_tools(node)
                    elif kind == "finish":
                        coroutine = self._finish_success(node)
                    else:
                        raise RuntimeError(f"unsupported Agent transition: {kind}")
                    with operation(
                        logger,
                        "agent.session",
                        "transition",
                        tree_id=self.tree.id,
                        run_id=run_id,
                        node_id=node.id,
                        transition_kind=kind,
                        transition_key=key,
                    ) as op:
                        task = loop.create_task(coroutine)
                        with self._transition_condition:
                            self._active_transition_task = task
                            self._active_transition_run_id = run_id
                            self._transition_condition.notify_all()
                        loop.run_until_complete(task)
                        op.finish(status=self._status, leaf_id=self._leaf_id)
                except asyncio.CancelledError as exc:
                    log_operation(
                        logger,
                        "agent.session",
                        "transition_cancelled",
                        phase="completed",
                        level=logging.WARNING,
                        tree_id=self.tree.id,
                        run_id=run_id,
                        node_id=node.id,
                        transition_kind=kind,
                        reason=exc,
                    )
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    log_operation(
                        logger,
                        "agent.session",
                        "transition_failure",
                        phase="failed",
                        level=logging.ERROR,
                        exc_info=True,
                        tree_id=self.tree.id,
                        run_id=run_id,
                        node_id=node.id,
                        transition_kind=kind,
                        error=exc,
                    )
                    failure = self._mount_assistant(
                        node.id,
                        f"Agent transition failed: {exc}",
                        error=True,
                        caused_by=self._transition_key(node),
                        run_id=run_id,
                    )
                    if failure is not None:
                        loop.run_until_complete(
                            self._finish_terminal(failure, status="failed")
                        )
                finally:
                    with self._transition_condition:
                        self._active_transition_task = None
                        self._active_transition_run_id = ""
                        self._transition_pending.discard(key)
                        no_pending_transitions = not self._transition_pending
                        self._transition_condition.notify_all()
                    if no_pending_transitions:
                        with self._state_lock:
                            if (
                                run_id
                                and run_id in self._cancelled_run_ids
                                and self._current_run_id == run_id
                                and self._status == "cancelling"
                            ):
                                cancelled_state = self._set_state_locked(
                                    "idle",
                                    "Cancelled",
                                )
                            else:
                                cancelled_state = None
                        if cancelled_state is not None:
                            self._emit_state_snapshot(cancelled_state)
        finally:
            with self._transition_condition:
                self._active_transition_task = None
                self._active_transition_run_id = ""
                self._transition_loop = None
                self._transition_condition.notify_all()
            loop.close()
            log_operation(
                logger,
                "agent.session",
                "transition_worker",
                phase="stopped",
                tree_id=self._tree_id_hint,
                thread=threading.current_thread().name,
            )

    def _wait_for_transitions(self) -> None:
        with self._transition_condition:
            while self._transition_pending:
                self._transition_condition.wait()

    def _restore(self) -> None:
        log_operation(
            logger,
            "agent.session",
            "restore",
            phase="started",
            tree_id=self.tree.id,
            root_id=self.tree.root_id,
        )
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        self._cancelled_run_ids = {
            str(node.value.get("run_id") or "")
            for node in nodes
            if isinstance(node.value, Mapping)
            and node.value.get("cancelled") is True
            and node.value.get("run_id")
        }
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
        leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
        self._leaf_id = leaf.id
        path = self.store.get_path(self.tree.id, leaf.id)
        latest_user = next(
            (
                node
                for node in reversed(path)
                if isinstance(node.value, Mapping) and node.value.get("role") == "user"
            ),
            None,
        )
        self._current_user_request = str(
            latest_user.value.get("content") if latest_user is not None else ""
        )
        self._current_run_id = self._node_run_id(leaf)
        self._run_permission_user_request = self._permission_request_for_run(
            self._current_run_id
        )
        if latest_user is not None:
            user_position = path.index(latest_user)
            self._model_calls = sum(
                1
                for node in path[user_position + 1:]
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "assistant"
                and node.value.get("caused_by")
            )
        value = leaf.value if isinstance(leaf.value, Mapping) else {}
        if value.get("cancelled") is True:
            self._current_user_request = ""
            self._set_state("idle", "Restored cancelled run", leaf_id=leaf.id)
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome="cancelled",
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        pending = self._pending_from_node(leaf)
        if pending is not None:
            self._set_state(
                "awaiting_user",
                str(pending.get("text") or "Waiting for user answer"),
                leaf_id=leaf.id,
            )
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome="awaiting_user",
                question_id=str(pending.get("id") or ""),
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        if value.get("role") == "context_compaction":
            should_resume = value.get("resume_model") is True
            if should_resume and self._transition_assistant(leaf) is None:
                self._set_state(
                    "queued",
                    "Resuming model after context compaction",
                    leaf_id=leaf.id,
                )
                self._enqueue_transition("advance", leaf)
                outcome = "resume_compacted_model"
            else:
                self._current_user_request = ""
                self._set_state(
                    "idle",
                    "Restored compacted context",
                    leaf_id=leaf.id,
                )
                outcome = "compacted_idle"
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome=outcome,
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        if value.get("role") == "assistant" and value.get("tool_calls"):
            if self._batch_result_node(leaf) is None:
                self._set_state("queued", "Resuming tool batch", leaf_id=leaf.id)
                self._enqueue_transition("tools", leaf)
                log_operation(
                    logger,
                    "agent.session",
                    "restore",
                    phase="completed",
                    tree_id=self.tree.id,
                    run_id=self._current_run_id,
                    leaf_id=leaf.id,
                    outcome="resume_tools",
                    node_count=len(nodes),
                    model_calls=self._model_calls,
                )
            return
        if value.get("role") == "context" and value.get("trigger_model") is False:
            self._set_state(
                "queued",
                "Resuming context mount",
                leaf_id=leaf.id,
            )
            source_id = str(value.get("source_node_id") or "")
            try:
                source = self.store.get_node(self.tree.id, source_id)
            except (NodeNotFoundError, ValueError):
                resumed_value = dict(value)
                resumed_value["trigger_model"] = True
                self.store.update_node(self.tree.id, leaf.id, resumed_value)
            else:
                source_value = (
                    dict(source.value)
                    if isinstance(source.value, Mapping)
                    else {}
                )
                self.store.update_node(self.tree.id, source.id, source_value)
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome="resume_context_mount",
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        if value.get("role") == "user" and value.get("trigger_model") is False:
            has_context_provider = bool(self.hooks.list(SESSION_START))
            if has_context_provider:
                self._set_state(
                    "queued",
                    "Resuming context mount",
                    leaf_id=leaf.id,
                )
                self.store.update_node(self.tree.id, leaf.id, dict(value))
                log_operation(
                    logger,
                    "agent.session",
                    "restore",
                    phase="completed",
                    tree_id=self.tree.id,
                    run_id=self._current_run_id,
                    leaf_id=leaf.id,
                    outcome="resume_context_mount",
                    node_count=len(nodes),
                    model_calls=self._model_calls,
                )
                return
        if (
            value.get("role") == "assistant"
            and not value.get("tool_calls")
            and value.get("error") is not True
            and value.get("cancelled") is not True
            and value.get("session_end_complete") is not True
        ):
            self._set_state(
                "queued",
                "Resuming SessionEnd Hooks",
                leaf_id=leaf.id,
            )
            self._enqueue_transition("finish", leaf)
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome="resume_session_end",
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        if value.get("trigger_model") is True and self._transition_assistant(leaf) is None:
            self._set_state("queued", "Resuming model transition", leaf_id=leaf.id)
            self._enqueue_transition("advance", leaf)
            log_operation(
                logger,
                "agent.session",
                "restore",
                phase="completed",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                leaf_id=leaf.id,
                outcome="resume_model",
                node_count=len(nodes),
                model_calls=self._model_calls,
            )
            return
        self._set_state("idle", "Restored", leaf_id=leaf.id)
        if value.get("role") == "assistant":
            self._current_user_request = ""
        log_operation(
            logger,
            "agent.session",
            "restore",
            phase="completed",
            tree_id=self.tree.id,
            run_id=self._current_run_id,
            leaf_id=leaf.id,
            outcome="idle",
            node_count=len(nodes),
            model_calls=self._model_calls,
        )

    def submit(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        permission_user_request: str | None = None,
    ) -> ContextNode:
        content = str(text or "").strip()
        if not content:
            raise ValueError("message cannot be empty")
        normalized_run_id = str(run_id or f"run_{uuid4().hex}").strip()
        if not normalized_run_id:
            raise ValueError("run_id cannot be empty")
        if self._permission_user_request is not None:
            authorization_request = self._permission_user_request
        elif permission_user_request is not None:
            authorization_request = str(permission_user_request)
        else:
            with self._state_lock:
                current_run_id = self._current_run_id
                current_authorization = self._run_permission_user_request
            authorization_request = (
                current_authorization
                if normalized_run_id == current_run_id and current_authorization
                else self._permission_request_for_run(normalized_run_id) or content
            )
        normalized_metadata = deepcopy(dict(metadata or {}))
        has_session_context = bool(self.hooks.list(SESSION_START))
        with self._state_lock:
            if self._closed:
                raise RuntimeError("the Agent session is closed")
            if self._status != "idle":
                raise RuntimeError("the Agent is still processing the previous message")
            parent_id = self._leaf_id
            self._status = "queued"
            self._detail = "User context mounted"
            self._current_user_request = content
            self._current_run_id = normalized_run_id
            self._run_permission_user_request = authorization_request
            self._model_calls = 0
        log_operation(
            logger,
            "agent.session",
            "submit",
            phase="started",
            tree_id=self.tree.id,
            run_id=normalized_run_id,
            parent_id=parent_id,
            content=content,
            metadata=dict(metadata or {}),
        )
        try:
            node = self.store.mount(
                self.tree.id,
                parent_id,
                {
                    "role": "user",
                    "content": content,
                    # Context providers get the first transition. Their Hook
                    # mounts durable child nodes and only the final child
                    # triggers the model, so Hook ordering is irrelevant even
                    # for trees created before the provider was registered.
                    "trigger_model": not has_session_context,
                    "run_id": normalized_run_id,
                    "authorization_request": authorization_request,
                    "metadata": normalized_metadata,
                },
                node_id=node_id,
            )
        except Exception as exc:
            log_operation(
                logger,
                "agent.session",
                "submit",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                tree_id=self.tree.id,
                run_id=normalized_run_id,
                parent_id=parent_id,
                error=exc,
            )
            self._set_state("idle", "Mount failed")
            raise
        self._set_state("queued", "Waiting for ContextChange Hook", leaf_id=node.id)
        log_operation(
            logger,
            "agent.session",
            "submit",
            phase="completed",
            tree_id=self.tree.id,
            run_id=normalized_run_id,
            parent_id=parent_id,
            node_id=node.id,
        )
        return node

    def answer(self, question_id: str, answer: str) -> ContextNode:
        """Resolve one durable pending Plugin result and continue the same run."""

        normalized_question_id = str(question_id or "").strip()
        normalized_answer = str(answer or "").strip()
        if not normalized_question_id or not normalized_answer:
            raise ValueError("question_id and answer are required")
        with self._linearized_context_commit():
            if self._closed:
                raise RuntimeError("the Agent session is closed")
            if self._status != "awaiting_user":
                raise RuntimeError("the Agent session is not awaiting a user answer")
            node = self.store.get_node(self.tree.id, self._leaf_id)
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            pending = self._pending_from_node(node)
            if pending is None or str(pending.get("id") or "") != normalized_question_id:
                raise ValueError("no matching pending question")
            run_id = str(value.get("run_id") or self._current_run_id)
            if not run_id or run_id in self._cancelled_run_ids:
                raise RuntimeError("the pending Agent run was cancelled")

            matched = False
            updated_results: list[Any] = []
            for raw in value.get("results") if isinstance(value.get("results"), list) else ():
                if not isinstance(raw, Mapping):
                    updated_results.append(raw)
                    continue
                stored = dict(raw)
                if str(stored.get("call_id") or "") == str(pending.get("call_id") or ""):
                    decoded = self._decoded_plugin_value(stored.get("value"))
                    wrapper: dict[str, Any] | None = None
                    if (
                        isinstance(decoded, Mapping)
                        and str(decoded.get("operation") or "") == "invoke"
                    ):
                        wrapper = dict(decoded)
                        decoded = self._decoded_plugin_value(wrapper.get("result"))
                    payload = dict(decoded) if isinstance(decoded, Mapping) else {}
                    payload.update(
                        {
                            "status": "answered",
                            "question_id": normalized_question_id,
                            "answer": normalized_answer,
                        }
                    )
                    if wrapper is not None:
                        wrapper["result"] = payload
                        stored["value"] = wrapper
                    else:
                        stored["value"] = payload
                    matched = True
                updated_results.append(stored)
            if not matched:
                raise RuntimeError("the pending tool result is no longer available")

            value["results"] = updated_results
            value["pending_question"] = {
                **pending,
                "status": "answered",
                "answer": normalized_answer,
                "answered_at": datetime.now(timezone.utc).isoformat(),
            }
            value["trigger_model"] = True
            node = self.store.update_node(self.tree.id, node.id, value)
            answered_state = self._set_state_locked(
                "queued",
                "User answer mounted; waiting for model",
                leaf_id=node.id,
            )
        self._emit_event(
            "input.answered",
            run_id=run_id,
            node_id=node.id,
            data={
                "question_id": normalized_question_id,
                "answer": normalized_answer,
            },
        )
        self._emit_state_snapshot(answered_state)
        return node

    async def _context_mount_changed(self, event: HookEvent) -> None:
        """Mount pending turn context before allowing the model transition."""

        change = event.payload
        if getattr(change, "action", "") not in {"mount", "update"}:
            return
        try:
            source = self.store.get_node(event.tree_id, str(change.node_id))
        except Exception:
            return
        value = source.value if isinstance(source.value, Mapping) else {}
        if value.get("role") != "user" or value.get("trigger_model") is not False:
            return
        metadata = value.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        mounts: list[dict[str, Any]] = []
        run_id = self._node_run_id(source)
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
        if value.get("session_start_complete") is not True:
            session_context = await self.hooks.session_start(
                {
                    "run_id": run_id,
                    "agent_id": self.agent_id,
                    "parent_agent_id": self.parent_agent_id,
                    "user_request": str(value.get("content") or ""),
                    "user_node_id": source.id,
                    "metadata": deepcopy(dict(metadata)),
                }
            )
        else:
            session_context = str(value.get("session_start_context") or "").strip()
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            source_value = dict(value)
            if value.get("session_start_complete") is not True:
                source_value["session_start_complete"] = True
                if session_context:
                    source_value["session_start_context"] = session_context
                self.store.update_node(self.tree.id, source.id, source_value)
            if session_context:
                mounts.insert(
                    0,
                    {
                        "kind": "plugin_session",
                        "content": session_context,
                        "metadata": {"source": "SessionStart"},
                    },
                )
            if not mounts:
                # A malformed provider payload must not strand the user turn.
                source_value["trigger_model"] = True
                self.store.update_node(self.tree.id, source.id, source_value)
                self._leaf_id = source.id
                return

            parent = source
            for index, mount in enumerate(mounts):
                node_id = self._stable_id(
                    "context",
                    f"{source.id}:{index}:{mount['kind']}",
                )
                try:
                    child = self.store.get_node(self.tree.id, node_id)
                except NodeNotFoundError:
                    child = self.store.mount(
                        self.tree.id,
                        parent.id,
                        {
                            "role": "context",
                            "content": mount["content"],
                            "context_kind": mount["kind"],
                            "context_source": "hook",
                            "source_node_id": source.id,
                            "context_index": index,
                            "metadata": mount["metadata"],
                            "run_id": run_id,
                            "trigger_model": index == len(mounts) - 1,
                        },
                        node_id=node_id,
                    )
                else:
                    child_value = (
                        dict(child.value)
                        if isinstance(child.value, Mapping)
                        else {}
                    )
                    should_trigger = index == len(mounts) - 1
                    if child_value.get("trigger_model") is not should_trigger:
                        child_value["trigger_model"] = should_trigger
                        child = self.store.update_node(
                            self.tree.id,
                            child.id,
                            child_value,
                        )
                parent = child
            self._leaf_id = parent.id

    def _configured_compaction_limit(self) -> int:
        try:
            from .plugin.model_catalog import configured_context_limit

            return max(
                0,
                int(
                    configured_context_limit(
                        self.tree.id,
                        route="primary",
                    )
                    or 0
                ),
            )
        except Exception:
            logger.debug(
                "Could not resolve the configured context limit for %s",
                self.tree.id,
                exc_info=True,
            )
            return 0

    def _model_tool_tokens(self) -> int:
        self.registry.refresh_customizations()
        self._model_tools = self.registry.direct_tool_definitions(agent_id=self.agent_id)
        if not self._model_tools:
            return 0
        return message_token_estimate(
            {
                "role": "system",
                "tools": list(self._model_tools),
            }
        )

    @staticmethod
    def _mechanical_compaction_summary(
        messages: list[dict[str, Any]],
    ) -> str:
        for message in messages:
            if message.get("compacted_block") is not True:
                continue
            content = str(message.get("content") or "").strip()
            if content.startswith(COMPACT_BLOCK_PREFIX):
                content = content[len(COMPACT_BLOCK_PREFIX) :].lstrip("\n")
            return content
        return ""

    async def _distill_compacted_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        context_limit: int,
        reserved_tokens: int,
        run_id: str,
        node_id: str,
    ) -> tuple[list[dict[str, Any]], bool, str]:
        mechanical = self._mechanical_compaction_summary(messages)
        if not mechanical:
            return messages, False, "mechanical summary is empty"
        gateway = self._plugin_services().get("model")
        complete = getattr(gateway, "complete", None)
        if not callable(complete):
            return messages, False, "secondary model gateway is unavailable"
        try:
            output = await complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Distill the mechanically compacted conversation into "
                            "a terse continuation record. Preserve user goals, "
                            "constraints, decisions, concrete file paths, errors, "
                            "and material tool outcomes. Omit bulky raw output and "
                            "do not invent facts. Return only the continuation record."
                        ),
                    },
                    {"role": "user", "content": mechanical},
                ],
                max_tokens=min(
                    2_048,
                    max(256, int(max(1, context_limit) * 0.1)),
                ),
                temperature=0.1,
                route="secondary",
                caller="context_compactor",
                session_id=self.tree.id,
                context=PluginContext(
                    workspace=self.workspace,
                    tree=self.store,
                    tree_id=self.tree.id,
                    node_id=node_id,
                    data=self._plugin_data(
                        run_id=run_id,
                        model_call_kind="compaction",
                        user_request=self.current_user_request,
                    ),
                    services=self._plugin_services(),
                ),
            )
            summary = str(output.get("content") or "").strip()
            if not summary:
                return messages, False, "secondary model returned an empty summary"
            distilled = replace_compacted_summary(messages, summary)
            mechanical_tokens = messages_token_estimate(messages) + reserved_tokens
            distilled_tokens = messages_token_estimate(distilled) + reserved_tokens
            if distilled_tokens >= mechanical_tokens:
                return messages, False, "secondary summary was not smaller"
            return distilled, True, ""
        except Exception as exc:
            logger.warning(
                "Context distillation failed for %s; keeping mechanical summary: %s",
                self.tree.id,
                exc,
            )
            return messages, False, str(exc)[:500]

    async def _compact_at_node(
        self,
        trigger: ContextNode,
        *,
        context_limit: int,
        force: bool,
        reason: str,
        resume_model: bool,
    ) -> tuple[ContextNode | None, dict[str, Any]]:
        limit = max(0, int(context_limit or 0))
        messages = self._messages(trigger.id)
        reserved_tokens = self._model_tool_tokens()
        before = messages_token_estimate(messages) + reserved_tokens
        base_result: dict[str, Any] = {
            "compacted": False,
            "before": before,
            "after": before,
            "limit": limit,
            "reason": reason,
            "node_id": "",
            "distilled": False,
        }
        if not limit and not force:
            base_result["reason"] = "context_limit_unavailable"
            return None, base_result

        compaction_key = self._stable_id(
            "context_compaction",
            ":".join(
                (
                    trigger.id,
                    trigger.updated_at.isoformat(),
                    str(limit),
                    reason,
                )
            ),
        )
        try:
            existing = self.store.get_node(self.tree.id, compaction_key)
        except NodeNotFoundError:
            existing = None
        if existing is not None:
            stored = existing.value if isinstance(existing.value, Mapping) else {}
            with self._state_lock:
                self._leaf_id = existing.id
            return existing, {
                "compacted": True,
                "before": int(stored.get("before_tokens") or before),
                "after": int(stored.get("after_tokens") or before),
                "limit": int(stored.get("context_limit") or limit),
                "reason": str(stored.get("reason") or reason),
                "node_id": existing.id,
                "distilled": bool(stored.get("distilled")),
            }

        compacted = compact_messages(
            messages,
            context_limit=limit,
            force=force,
            reserved_tokens=reserved_tokens,
        )
        if not compacted.compacted:
            base_result["reason"] = (
                "below_trigger"
                if not force
                and before <= int(limit * COMPACT_TRIGGER_RATIO)
                else "nothing_to_compact"
            )
            return None, base_result

        projected = [dict(message) for message in compacted.messages]
        distilled = False
        distillation_error = ""
        if compacted.needs_distillation:
            projected, distilled, distillation_error = (
                await self._distill_compacted_messages(
                    projected,
                    context_limit=limit,
                    reserved_tokens=reserved_tokens,
                    run_id=self._node_run_id(trigger),
                    node_id=trigger.id,
                )
            )
        after = messages_token_estimate(projected) + reserved_tokens
        if after >= before:
            base_result.update(
                after=after,
                reason=distillation_error or "compaction_not_smaller",
                distilled=distilled,
            )
            return None, base_result
        run_id = self._node_run_id(trigger)
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return None, {
                    **base_result,
                    "reason": "cancelled",
                }
            try:
                node = self.store.get_node(self.tree.id, compaction_key)
            except NodeNotFoundError:
                payload: dict[str, Any] = {
                    "role": "context_compaction",
                    "run_id": run_id,
                    "source_node_id": trigger.id,
                    "caused_by": compaction_key,
                    "trigger_model": False,
                    "resume_model": bool(resume_model),
                    "reason": reason,
                    "context_limit": limit,
                    "trigger_tokens": int(limit * COMPACT_TRIGGER_RATIO),
                    "reserved_tokens": reserved_tokens,
                    "before_tokens": before,
                    "after_tokens": after,
                    "distilled": distilled,
                    "messages": projected,
                }
                if distillation_error:
                    payload["distillation_error"] = distillation_error
                node = self.store.mount(
                    self.tree.id,
                    trigger.id,
                    payload,
                    node_id=compaction_key,
                )
            self._leaf_id = node.id
        return node, {
            "compacted": True,
            "before": before,
            "after": after,
            "limit": limit,
            "reason": reason,
            "node_id": node.id,
            "distilled": distilled,
        }

    async def compact_context(
        self,
        *,
        context_limit: int,
    ) -> dict[str, Any]:
        """Force one durable compaction while the Agent is fully idle."""

        with self._transition_condition:
            transitions_pending = bool(self._transition_pending)
        with self._state_lock:
            if self._closed:
                raise RuntimeError("the Agent session is closed")
            if self._status == "awaiting_user":
                raise RuntimeError("cannot compact while awaiting a user answer")
            if self._status != "idle" or transitions_pending:
                raise RuntimeError("manual context compaction requires an idle Agent")
            leaf = self.store.get_node(self.tree.id, self._leaf_id)
            compacting_state = self._set_state_locked(
                "compacting",
                "Compacting durable context",
            )
        self._emit_state_snapshot(compacting_state)
        node: ContextNode | None = None
        try:
            node, result = await self._compact_at_node(
                leaf,
                context_limit=context_limit,
                force=True,
                reason="manual",
                resume_model=False,
            )
        finally:
            with self._state_lock:
                idle_state = self._set_state_locked(
                    "idle",
                    "Context compacted" if node else "Ready",
                    leaf_id=(node.id if node else leaf.id),
                )
                self._current_user_request = ""
            self._emit_state_snapshot(idle_state)
        return result

    def prepare_retry(self) -> dict[str, str]:
        """Move the in-process leaf before the latest user turn.

        Retrying creates a new ContextTree branch instead of deleting the
        completed branch.  The next :meth:`submit` mounts a fresh user node
        below the previous turn's parent, so a crash before that mount leaves
        the durable conversation untouched and a crash afterwards restores the
        newer branch by timestamp.
        """

        with self._transition_condition:
            transitions_pending = bool(self._transition_pending)
        with self._state_lock:
            if self._closed:
                raise RuntimeError("the Agent session is closed")
            if self._status == "awaiting_user":
                raise RuntimeError("cannot retry while awaiting a user answer")
            if self._status != "idle" or transitions_pending:
                raise RuntimeError("retry requires an idle Agent")
            path = self.store.get_path(self.tree.id, self._leaf_id)
            latest_user = next(
                (
                    node
                    for node in reversed(path)
                    if isinstance(node.value, Mapping)
                    and node.value.get("role") == "user"
                ),
                None,
            )
            if latest_user is None or latest_user.parent_id is None:
                raise RuntimeError("the conversation has no user turn to retry")
            previous_run_id = str(latest_user.value.get("run_id") or "")
            parent_id = str(latest_user.parent_id)
            state = self._set_state_locked(
                "idle",
                "Ready to retry",
                leaf_id=parent_id,
            )
            self._current_user_request = ""
            self._current_run_id = ""
            self._run_permission_user_request = ""
            self._model_calls = 0
        self._emit_state_snapshot(state)
        return {
            "user_node_id": latest_user.id,
            "parent_node_id": parent_id,
            "previous_run_id": previous_run_id,
        }

    async def _context_changed(self, event: HookEvent) -> None:
        change = event.payload
        if getattr(change, "action", "") not in {"mount", "update"}:
            return
        try:
            node = self.store.get_node(event.tree_id, str(change.node_id))
        except Exception:
            return
        value = node.value if isinstance(node.value, Mapping) else {}
        if value.get("trigger_model") is not True:
            return
        if self._is_cancelled(self._node_run_id(node)):
            return
        self._enqueue_transition("advance", node)

    async def _advance(self, trigger: ContextNode) -> None:
        run_id = self._node_run_id(trigger)
        if self._is_cancelled(run_id):
            return
        existing = self._transition_assistant(trigger)
        if existing is not None:
            calls = (
                existing.value.get("tool_calls")
                if isinstance(existing.value, Mapping)
                else None
            )
            if calls:
                await self._continue_tools(existing)
            else:
                await self._finish_success(existing)
            return
        trigger_value = (
            trigger.value if isinstance(trigger.value, Mapping) else {}
        )
        if trigger_value.get("role") != "context_compaction":
            compacted_node, _compaction = await self._compact_at_node(
                trigger,
                context_limit=self._configured_compaction_limit(),
                force=False,
                reason="automatic_60_percent",
                resume_model=True,
            )
            if compacted_node is not None:
                trigger = compacted_node
                run_id = self._node_run_id(trigger)
        if self._is_cancelled(run_id):
            return
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
            self._model_calls += 1
            count = self._model_calls
        if count > self._max_model_calls:
            failure = self._mount_assistant(
                trigger.id,
                "Stopped because the model-call limit for this user turn was reached.",
                error=True,
                caused_by=self._transition_key(trigger),
                run_id=run_id,
            )
            if failure is not None:
                await self._finish_terminal(failure, status="failed")
            return

        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
            model_state = self._set_state_locked(
                "model",
                f"Calling {self.model_plugin} ({count}/{self._max_model_calls})",
            )
        self._emit_state_snapshot(model_state)
        self.registry.refresh_customizations()
        self._model_tools = self.registry.direct_tool_definitions(agent_id=self.agent_id)
        arguments = {
            "messages": self._messages(trigger.id),
            "tools": deepcopy(list(self._model_tools)),
        }
        result = await self.runtime.call(
            self.model_plugin,
            arguments,
            PluginContext(
                workspace=self.workspace,
                tree=self.store,
                tree_id=self.tree.id,
                node_id=trigger.id,
                data=self._plugin_data(
                    run_id=run_id,
                    model_call_kind="agent",
                    user_request=self.current_user_request,
                ),
                services=self._plugin_services(),
            ),
        )
        if self._is_cancelled(run_id):
            return
        if not result.success or not isinstance(result.value, Mapping):
            failure = self._mount_assistant(
                trigger.id,
                result.error or "Model call failed",
                error=True,
                caused_by=self._transition_key(trigger),
                run_id=run_id,
            )
            if failure is not None:
                await self._finish_terminal(failure, status="failed")
            return
        output = dict(result.value)
        calls = output.get("tool_calls")
        calls = calls if isinstance(calls, list) else []
        transition_key = self._transition_key(trigger)
        batch_key = self._stable_id(
            "batch",
            transition_key
            + json.dumps(calls, ensure_ascii=False, sort_keys=True, default=str),
        )
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            assistant = self.store.mount(
                self.tree.id,
                trigger.id,
                {
                    "role": "assistant",
                    "content": str(output.get("content") or ""),
                    "reasoning": str(output.get("reasoning") or ""),
                    "reasoning_details": output.get("reasoning_details") or [],
                    "tool_calls": calls,
                    "model": str(output.get("model") or self.model_plugin),
                    "model_identity": self._json_value(
                        output.get("model_identity") or {}
                    ),
                    "usage": self._json_value(output.get("usage") or {}),
                    "model_observation_id": str(
                        output.get("observation_node_id") or ""
                    ),
                    "model_latency_ms": self._json_value(
                        output.get("latency_ms") or 0.0
                    ),
                    "finish_reason": str(output.get("finish_reason") or ""),
                    "response_id": str(output.get("response_id") or ""),
                    "run_id": run_id,
                    "caused_by": transition_key,
                    "batch_key": batch_key,
                    "effect_results": {},
                },
                node_id=self._stable_id("assistant", transition_key),
            )
            assistant_state = self._set_state_locked(
                "tools" if calls else "finalizing",
                "Executing tools" if calls else "Running SessionEnd Hooks",
                leaf_id=assistant.id,
            )
        self._emit_state_snapshot(assistant_state)
        if not calls:
            await self._finish_success(assistant)
            return

        await self._continue_tools(assistant)

    async def _finish_success(self, assistant: ContextNode) -> None:
        """Dispatch terminal lifecycle Hooks exactly once for a successful run."""

        value = assistant.value if isinstance(assistant.value, Mapping) else {}
        await self._finish_terminal(
            assistant,
            status="failed" if value.get("error") is True else "completed",
        )

    async def _finish_terminal(
        self,
        assistant: ContextNode,
        *,
        status: str,
    ) -> None:
        """Dispatch terminal lifecycle Hooks exactly once for any settled run."""

        value = dict(assistant.value) if isinstance(assistant.value, Mapping) else {}
        run_id = str(value.get("run_id") or self.current_run_id)
        terminal_status = str(
            value.get("session_end_status") or status or "completed"
        )
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
        if value.get("session_end_complete") is not True:
            user_value: Mapping[str, Any] = {}
            user_node_id = ""
            path = self.store.get_path(self.tree.id, assistant.id)
            for node in reversed(path):
                candidate = node.value if isinstance(node.value, Mapping) else {}
                if (
                    candidate.get("role") == "user"
                    and str(candidate.get("run_id") or "") == run_id
                ):
                    user_value = candidate
                    user_node_id = node.id
                    break
            metadata = user_value.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            with self._state_lock:
                if self._closed or run_id in self._cancelled_run_ids:
                    return
            await self.hooks.session_end(
                {
                    "status": terminal_status,
                    "run_id": run_id,
                    "agent_id": self.agent_id,
                    "parent_agent_id": self.parent_agent_id,
                    "user_request": str(user_value.get("content") or ""),
                    "user_node_id": user_node_id,
                    "assistant_node_id": assistant.id,
                    "assistant_text": str(value.get("content") or ""),
                    "model": str(value.get("model") or ""),
                    "model_identity": deepcopy(dict(value.get("model_identity") or {})),
                    "usage": deepcopy(dict(value.get("usage") or {})),
                    "metadata": deepcopy(dict(metadata)),
                }
            )
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            current = self.store.get_node(self.tree.id, assistant.id)
            completed = (
                dict(current.value) if isinstance(current.value, Mapping) else value
            )
            if completed.get("session_end_complete") is not True:
                completed["session_end_complete"] = True
                completed["session_end_status"] = terminal_status
                assistant = self.store.update_node(
                    self.tree.id,
                    assistant.id,
                    completed,
                )
            else:
                assistant = current
            completed_state = self._set_state_locked(
                "idle",
                "Complete" if terminal_status == "completed" else "Failed",
                leaf_id=assistant.id,
            )
            self._current_user_request = ""
        self._emit_state_snapshot(completed_state)

    @staticmethod
    def _stored_result(result: PluginCallResult) -> dict[str, Any]:
        return {
            "call_id": result.call_id,
            "name": result.name,
            "success": result.success,
            "value": AgentSession._json_value(result.value),
            "error": result.error,
            "time": result.time.isoformat(),
        }

    @staticmethod
    def _restored_result(raw: Mapping[str, Any]) -> PluginCallResult:
        return PluginCallResult(
            str(raw.get("call_id") or ""),
            str(raw.get("name") or ""),
            bool(raw.get("success")),
            raw.get("value"),
            str(raw.get("error") or ""),
            datetime.fromisoformat(str(raw.get("time"))),
        )

    def _persist_effect_result(self, assistant_id: str, result: PluginCallResult) -> None:
        with self._state_lock:
            node = self.store.get_node(self.tree.id, assistant_id)
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            effects = dict(value.get("effect_results") or {})
            effects[result.call_id] = self._stored_result(result)
            value["effect_results"] = effects
            self.store.update_node(self.tree.id, assistant_id, value)
            run_id = str(value.get("run_id") or "")
        self._emit_event(
            "tool.completed",
            run_id=run_id,
            node_id=assistant_id,
            time=result.time,
            data=self._stored_result(result),
        )

    async def _continue_tools(self, assistant: ContextNode) -> None:
        run_id = self._node_run_id(assistant)
        if self._is_cancelled(run_id):
            return
        existing_result = self._batch_result_node(assistant)
        if existing_result is not None:
            pending = self._pending_from_node(existing_result)
            with self._state_lock:
                if self._closed or run_id in self._cancelled_run_ids:
                    return
                if pending is not None:
                    restored_state = self._set_state_locked(
                        "awaiting_user",
                        str(pending.get("text") or "Waiting for user answer"),
                        leaf_id=existing_result.id,
                    )
                else:
                    restored_state = self._set_state_locked(
                        "model",
                        "Tool results restored; waiting for model",
                        leaf_id=existing_result.id,
                    )
            self._emit_state_snapshot(restored_state)
            if pending is None and self._transition_assistant(existing_result) is None:
                self._enqueue_transition("advance", existing_result)
            return

        value = assistant.value if isinstance(assistant.value, Mapping) else {}
        calls = value.get("tool_calls")
        calls = calls if isinstance(calls, list) else []

        plugin_calls = tuple(
            PluginCall(
                name=str(call.get("name") or ""),
                arguments=dict(call.get("arguments") or {}),
                id=str(call.get("id") or f"call_{uuid4().hex}"),
            )
            for call in calls
            if isinstance(call, Mapping)
        )
        if not plugin_calls:
            failure = self._mount_assistant(
                assistant.id,
                "The model returned no valid tool calls.",
                error=True,
                caused_by=str(value.get("batch_key") or ""),
                run_id=run_id,
            )
            if failure is not None:
                await self._finish_terminal(failure, status="failed")
            return
        completed: dict[str, PluginCallResult] = {}
        for call_id, raw in dict(value.get("effect_results") or {}).items():
            if not isinstance(raw, Mapping):
                continue
            try:
                result = self._restored_result(raw)
            except (TypeError, ValueError):
                continue
            if result.call_id == str(call_id):
                completed[str(call_id)] = result
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
            tools_state = self._set_state_locked(
                "tools",
                "Reviewing and executing tools",
                leaf_id=assistant.id,
            )
        self._emit_state_snapshot(tools_state)
        results = await self.batch.run(
            plugin_calls,
            PluginContext(
                workspace=self.workspace,
                tree=self.store,
                tree_id=self.tree.id,
                node_id=assistant.id,
                hooks=self.hooks,
                data=self._plugin_data(
                    run_id=run_id,
                    model_call_kind="tool",
                    user_request=self.current_user_request,
                ),
                services=self._plugin_services(),
            ),
            completed=completed,
            on_result=lambda result: self._persist_effect_result(assistant.id, result),
        )
        if self._is_cancelled(run_id):
            return
        batch_key = str(value.get("batch_key") or self._stable_id("batch", assistant.id))
        pending_question = self._pending_question_from_results(
            calls,
            results,
            run_id=run_id,
        )
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            tool_node = self.store.mount(
                self.tree.id,
                assistant.id,
                {
                    "role": "tool_results",
                    "trigger_model": pending_question is None,
                    "run_id": run_id,
                    "caused_by": batch_key,
                    "results": [
                        {
                            "call_id": item.call_id,
                            "name": item.name,
                            "success": item.success,
                            "value": self._json_value(item.value),
                            "error": item.error,
                        }
                        for item in results
                    ],
                    **(
                        {"pending_question": pending_question}
                        if pending_question is not None
                        else {}
                    ),
                },
                node_id=self._stable_id("tool_results", batch_key),
            )
            if pending_question is not None:
                tool_state = self._set_state_locked(
                    "awaiting_user",
                    str(pending_question.get("text") or "Waiting for user answer"),
                    leaf_id=tool_node.id,
                )
            else:
                tool_state = self._set_state_locked(
                    "model",
                    "Tool results mounted; waiting for model",
                    leaf_id=tool_node.id,
                )
        self._emit_state_snapshot(tool_state)

    def _mount_assistant(
        self,
        parent_id: str,
        content: str,
        *,
        error: bool,
        caused_by: str = "",
        run_id: str = "",
    ) -> ContextNode | None:
        node_id = self._stable_id("assistant_error", caused_by) if caused_by else None
        with self._linearized_context_commit():
            effective_run_id = str(run_id or self._current_run_id)
            if self._closed or effective_run_id in self._cancelled_run_ids:
                return None
            existing = None
            if node_id is not None:
                try:
                    existing = self.store.get_node(self.tree.id, node_id)
                except Exception:
                    pass
            if existing is None:
                node = self.store.mount(
                    self.tree.id,
                    parent_id,
                    {
                        "role": "assistant",
                        "content": str(content),
                        "error": bool(error),
                        "run_id": effective_run_id,
                        "caused_by": caused_by,
                    },
                    node_id=node_id,
                )
            else:
                node = existing
            terminal_state = self._set_state_locked(
                "finalizing",
                "Running SessionEnd Hooks",
                leaf_id=node.id,
            )
        self._emit_state_snapshot(terminal_state)
        return node

    def _mark_leaf_waiting_for_subagents(self) -> None:
        with self._state_lock:
            leaf_id = self._leaf_id
        node = self.store.get_node(self.tree.id, leaf_id)
        value = dict(node.value) if isinstance(node.value, Mapping) else {}
        if (
            value.get("role") != "assistant"
            or value.get("tool_calls")
            or value.get("intermediate") is True
        ):
            return
        value["intermediate"] = True
        value["waiting_for_subagents"] = True
        self.store.update_node(self.tree.id, leaf_id, value)

    def _messages(self, node_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        path = self.store.get_path(self.tree.id, node_id)
        current_run_id = next(
            (
                str(node.value.get("run_id") or "")
                for node in reversed(path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "user"
            ),
            "",
        )
        current_context_by_kind = {
            str(node.value.get("context_kind") or node.id): node.id
            for node in path
            if isinstance(node.value, Mapping)
            and node.value.get("role") == "context"
            and str(node.value.get("run_id") or "") == current_run_id
        }
        current_context_ids = set(current_context_by_kind.values())
        root_value = (
            path[0].value
            if path and isinstance(path[0].value, Mapping)
            else {}
        )
        base_system_content = (
            self._system_prompt
            or str(root_value.get("content") or "")
        )
        for node in path:
            value = node.value if isinstance(node.value, Mapping) else {}
            role = str(value.get("role") or "")
            if role == "context_compaction":
                compacted = value.get("messages")
                if isinstance(compacted, list) and all(
                    isinstance(message, Mapping) for message in compacted
                ):
                    messages = [deepcopy(dict(message)) for message in compacted]
                    if str(value.get("run_id") or "") != current_run_id:
                        system = next(
                            (
                                message
                                for message in messages
                                if str(message.get("role") or "") == "system"
                                and message.get("compacted_block") is not True
                            ),
                            None,
                        )
                        if system is None and base_system_content:
                            messages.insert(
                                0,
                                {
                                    "role": "system",
                                    "content": base_system_content,
                                },
                            )
                        elif system is not None:
                            system["content"] = base_system_content
                continue
            if role in {"system", "user"}:
                content = str(value.get("content") or "")
                if role == "system" and node.id == self.tree.root_id and self._system_prompt:
                    content = self._system_prompt
                messages.append({"role": role, "content": content})
            elif role == "context":
                if node.id not in current_context_ids:
                    continue
                content = str(value.get("content") or "").strip()
                if not content:
                    continue
                system = next(
                    (
                        message
                        for message in messages
                        if str(message.get("role") or "") == "system"
                    ),
                    None,
                )
                if system is None:
                    messages.insert(0, {"role": "system", "content": content})
                else:
                    current = str(system.get("content") or "").strip()
                    system["content"] = "\n\n".join(
                        part for part in (current, content) if part
                    )
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
                        message["reasoning_details"] = reasoning_details
                messages.append(message)
            elif role == "tool_results":
                results = value.get("results")
                observations: list[dict[str, Any]] = []
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
                    from .plugin.mcp_content import build_mcp_observation_content

                    observation = build_mcp_observation_content(
                        result.get("value"),
                        tool_name=str(result.get("name") or ""),
                    )
                    if observation:
                        observations.append(
                            {
                                "role": "user",
                                "content": observation,
                                "ephemeral_model_observation": True,
                            }
                        )
                messages.extend(observations)
        return messages

    def _persist_auxiliary_model_usage(
        self,
        assistant_id: str,
        output: Mapping[str, Any],
    ) -> None:
        entry = {
            "kind": "permission",
            "usage": self._json_value(output.get("usage") or {}),
            "model": str(output.get("model") or self.model_plugin),
            "model_identity": self._json_value(output.get("model_identity") or {}),
            "response_id": str(output.get("response_id") or ""),
            "model_observation_id": str(output.get("observation_node_id") or ""),
            "model_latency_ms": self._json_value(output.get("latency_ms") or 0.0),
        }
        with self._state_lock:
            try:
                node = self.store.get_node(self.tree.id, assistant_id)
            except NodeNotFoundError:
                return
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            if value.get("role") != "assistant":
                return
            stored = value.get("auxiliary_usage")
            auxiliary_usage = list(stored) if isinstance(stored, list) else []
            auxiliary_usage.append(entry)
            value["auxiliary_usage"] = auxiliary_usage
            self.store.update_node(self.tree.id, assistant_id, value)

    async def _permission_model(
        self,
        system_prompt: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._state_lock:
            assistant_id = self._leaf_id
            run_id = self._current_run_id
            user_request = self._current_user_request
        result = await self.runtime.call(
            self.model_plugin,
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii=False, default=str),
                    },
                ],
                "tools": [PERMISSION_DECIDE_TOOL],
                "tool_choice": PERMISSION_DECIDE_TOOL_CHOICE,
            },
            PluginContext(
                workspace=self.workspace,
                tree=self.store,
                tree_id=self.tree.id,
                node_id=assistant_id,
                data=self._plugin_data(
                    run_id=run_id,
                    model_call_kind="permission",
                    user_request=user_request,
                ),
                services=self._plugin_services(),
            ),
        )
        if not result.success or not isinstance(result.value, Mapping):
            raise RuntimeError(result.error or "permission model failed")
        self._persist_auxiliary_model_usage(assistant_id, result.value)
        decisions = [
            call
            for call in result.value.get("tool_calls") or ()
            if isinstance(call, Mapping) and call.get("name") == "decide"
        ]
        if len(decisions) != 1:
            raise RuntimeError(
                f"permission model must call decide exactly once; got {len(decisions)}"
            )
        arguments = decisions[0].get("arguments")
        if not isinstance(arguments, Mapping):
            raise RuntimeError("permission model returned invalid decide arguments")
        return dict(arguments)

    def request_cancel(self, reason: str = "user_cancelled") -> bool:
        """Request cancellation from any thread and persist a terminal marker."""

        normalized_reason = str(reason or "user_cancelled")
        with self._state_lock:
            if self._closed:
                log_operation(
                    logger,
                    "agent.session",
                    "request_cancel",
                    phase="skipped",
                    tree_id=self.tree.id,
                    run_id=self._current_run_id,
                    status=self._status,
                    closed=True,
                    reason=normalized_reason,
                )
                return False
        manager = self._subagent_manager if self._owns_subagent_manager else None
        children_active = bool(manager is not None and manager.has_active)
        if children_active:
            manager.request_cancel_all(normalized_reason)
        with self._linearized_context_commit():
            if (
                self._closed
                or (self._status == "idle" and not children_active)
                or not self._current_run_id
            ):
                log_operation(
                    logger,
                    "agent.session",
                    "request_cancel",
                    phase="skipped",
                    tree_id=self.tree.id,
                    run_id=self._current_run_id,
                    status=self._status,
                    closed=self._closed,
                    reason=normalized_reason,
                    children_cancelled=children_active,
                )
                return children_active
            run_id = self._current_run_id
            parent_id = self._leaf_id
            cancel_id = self._stable_id("cancel", run_id)
            try:
                cancelled = self.store.get_node(self.tree.id, cancel_id)
            except Exception:
                cancelled = self.store.mount(
                    self.tree.id,
                    parent_id,
                    {
                        "role": "assistant",
                        "content": "",
                        "cancelled": True,
                        "cancel_reason": normalized_reason,
                        "run_id": run_id,
                        "caused_by": f"cancel:{run_id}",
                    },
                    node_id=cancel_id,
                )
            self._cancelled_run_ids.add(run_id)
            cancelling_state = self._set_state_locked(
                "cancelling",
                normalized_reason,
                leaf_id=cancelled.id,
            )
            self._current_user_request = ""
        log_operation(
            logger,
            "agent.session",
            "request_cancel",
            phase="started",
            tree_id=self.tree.id,
            run_id=run_id,
            parent_id=parent_id,
            reason=normalized_reason,
        )
        self._emit_state_snapshot(cancelling_state)

        with self._transition_condition:
            loop = self._transition_loop
            task = self._active_transition_task
            active_run_id = self._active_transition_run_id
            has_pending = bool(self._transition_pending)
        if (
            loop is not None
            and task is not None
            and not task.done()
            and active_run_id == run_id
        ):
            loop.call_soon_threadsafe(task.cancel)
            active_task_cancelled = True
        elif not has_pending:
            self._set_state("idle", "Cancelled", leaf_id=cancelled.id)
            active_task_cancelled = False
        else:
            active_task_cancelled = False
        log_operation(
            logger,
            "agent.session",
            "request_cancel",
            phase="completed",
            tree_id=self.tree.id,
            run_id=run_id,
            node_id=cancelled.id,
            reason=normalized_reason,
            active_task_cancelled=active_task_cancelled,
            pending_transitions=has_pending,
        )
        return True

    async def cancel(
        self,
        reason: str = "user_cancelled",
        *,
        timeout: float | None = None,
    ) -> bool:
        """Cancel the active run, notify Stop Hooks, and wait for it to settle."""

        with operation(
            logger,
            "agent.session",
            "cancel",
            tree_id=self.tree.id,
            run_id=self.current_run_id,
            reason=reason,
            timeout=timeout,
        ) as op:
            with self._state_lock:
                own_run_active = (
                    not self._closed
                    and self._status != "idle"
                    and bool(self._current_run_id)
                )
            manager = self._subagent_manager if self._owns_subagent_manager else None
            changed = self.request_cancel(reason)
            if not changed:
                op.finish(changed=False)
                return False

            if manager is not None:
                await manager.cancel_all(reason)

            async def settle() -> None:
                await self.hooks.stop(
                    reason,
                    {"run_id": self.current_run_id},
                )
                await asyncio.to_thread(self._wait_for_transitions)

            if own_run_active:
                if timeout is None:
                    await settle()
                else:
                    await asyncio.wait_for(
                        settle(),
                        timeout=max(0.0, float(timeout)),
                    )
                self._set_state("idle", "Cancelled")
            op.finish(changed=True, status="idle", leaf_id=self._leaf_id)
            return True

    def final_output(self, run_id: str | None = None) -> dict[str, Any] | None:
        """Return the latest terminal assistant payload for one run."""

        target_run_id = str(run_id or self.current_run_id)
        candidates = []
        for node in self.store.get_subtree(self.tree.id, self.tree.root_id):
            value = node.value if isinstance(node.value, Mapping) else {}
            if (
                value.get("role") == "assistant"
                and str(value.get("run_id") or "") == target_run_id
                and not value.get("tool_calls")
            ):
                candidates.append(node)
        if not candidates:
            log_operation(
                logger,
                "agent.session",
                "final_output",
                phase="completed",
                tree_id=self.tree.id,
                run_id=target_run_id,
                found=False,
            )
            return None
        node = max(candidates, key=lambda item: (item.created_at, item.id))
        result = {
            "node_id": node.id,
            **deepcopy(dict(node.value)),
        }
        log_operation(
            logger,
            "agent.session",
            "final_output",
            phase="completed",
            tree_id=self.tree.id,
            run_id=target_run_id,
            node_id=node.id,
            found=True,
            output=result,
        )
        return result

    def snapshot(self) -> dict[str, Any]:
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        with self._state_lock:
            status = self._status
            detail = self._detail
            leaf_id = self._leaf_id
            run_id = self._current_run_id
        with self._event_lock:
            event_sequence = self._event_sequence
        pending_subagents = bool(
            self._owns_subagent_manager
            and self._subagent_manager is not None
            and self._subagent_manager.has_pending_work
        )
        public_status = "running" if status == "idle" and pending_subagents else status
        public_detail = "Waiting for subagents" if pending_subagents else detail
        result = {
            "tree_id": self.tree.id,
            "root_id": self.tree.root_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "leaf_id": leaf_id,
            "status": public_status,
            "detail": public_detail,
            "run_id": run_id,
            "event_sequence": event_sequence,
            "workspace": str(self.workspace),
            "nodes": [
                {
                    "id": node.id,
                    "parent_id": node.parent_id,
                    "created_at": node.created_at.isoformat(),
                    "updated_at": node.updated_at.isoformat(),
                    "value": node.value,
                }
                for node in nodes
            ],
        }
        if self._owns_subagent_manager and self._subagent_manager is not None:
            result["subagents"] = self._subagent_manager.query()
        log_operation(
            logger,
            "agent.session",
            "snapshot",
            phase="completed",
            tree_id=self.tree.id,
            run_id=run_id,
            root_id=self.tree.root_id,
            leaf_id=leaf_id,
            status=public_status,
            detail=public_detail,
            event_sequence=event_sequence,
            node_count=len(nodes),
        )
        return result

    async def drain(self) -> None:
        """Wait until queued Hooks and all resulting transitions are idle."""

        with operation(
            logger,
            "agent.session",
            "drain",
            tree_id=self.tree.id,
            run_id=self.current_run_id,
        ) as op:
            for attempt in range(1, self._max_model_calls + 67):
                await asyncio.shield(self.hooks.drain())
                await asyncio.to_thread(self._wait_for_transitions)
                await asyncio.shield(self.hooks.drain())
                with self._transition_condition:
                    pending = bool(self._transition_pending)
                with self._state_lock:
                    status = self._status
                if not pending and status in {"idle", "awaiting_user"}:
                    if status == "awaiting_user":
                        op.finish(
                            attempts=attempt,
                            status=status,
                            leaf_id=self._leaf_id,
                        )
                        return
                    if (
                        self._owns_subagent_manager
                        and self._subagent_manager is not None
                        and self._subagent_manager.has_pending_work
                    ):
                        self._mark_leaf_waiting_for_subagents()
                        if await self._subagent_manager.drive():
                            continue
                    op.finish(attempts=attempt, status=self._status, leaf_id=self._leaf_id)
                    return
            raise RuntimeError("Agent session did not become idle while draining")

    def close(self) -> None:
        """Stop process-local workers while leaving unfinished tree state recoverable."""

        with self._transition_condition:
            if self._closed:
                log_operation(
                    logger,
                    "agent.session",
                    "close",
                    phase="skipped",
                    tree_id=self.tree.id,
                    reason="already_closed",
                )
                return
            log_operation(
                logger,
                "agent.session",
                "close",
                phase="started",
                tree_id=self.tree.id,
                run_id=self._current_run_id,
                status=self._status,
                pending_transitions=len(self._transition_pending),
            )
            self._closed = True
            loop = self._transition_loop
            task = self._active_transition_task
            if loop is not None and task is not None and not task.done():
                loop.call_soon_threadsafe(task.cancel)
            self._transition_work.put(None)
            self._transition_condition.notify_all()
        if self._transition_thread is not threading.current_thread():
            self._transition_thread.join()
        if self._owns_subagent_manager and self._subagent_manager is not None:
            self._subagent_manager.close()
        self._unsubscribe_context_events()
        self.store.close()
        log_operation(
            logger,
            "agent.session",
            "close",
            phase="completed",
            tree_id=self.tree.id,
            run_id=self._current_run_id,
            status=self._status,
        )


AgentTreeSession = AgentSession


__all__ = [
    "AgentEventListener",
    "AgentSession",
    "AgentSessionEvent",
    "AgentTreeSession",
    "DEFAULT_SYSTEM_PROMPT",
]
