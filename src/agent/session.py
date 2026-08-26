"""Production event-driven Agent session built from Context, Hook, and Plugin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .context import ContextChange, ContextNode, ContextStoreRouter, TreeNotFoundError
from .hook import CONTEXT_CHANGE, HookEvent, HookRegistration
from .plugin import (
    PluginBatchRunner,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginRegistry,
    PluginRuntime,
)
from .plugin.core_impl import (
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
    PermissionReviewPlugin,
)

DEFAULT_SYSTEM_PROMPT = """You are Cyrene, an agent running on a Context Tree.
Answer directly when no tool is needed. Bash, Read, Write, and toolbox are the only
tools exposed directly. For every other tool, use toolbox.list to discover it,
toolbox.describe to read its current input schema, then toolbox.invoke to call it.
After receiving tool results, explain the result to the user instead of repeating the same call.
The workspace is {workspace}.
"""


logger = logging.getLogger(__name__)

AgentEventType = Literal[
    "session.state",
    "input.accepted",
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
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = registry or PluginRegistry()
        failures = self.registry.load_directory(plugin_directory)
        if failures:
            detail = "; ".join(f"{failure.path.name}: {failure.error}" for failure in failures)
            raise RuntimeError(f"failed to load Plugin packs: {detail}")
        self._model_tools = self.registry.direct_tool_definitions()
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
        self.store = ContextStoreRouter(Path(data_directory) / "context")
        self._state_lock = threading.RLock()
        self._event_lock = threading.RLock()
        self._event_sequence = 0
        self._event_listeners: dict[int, AgentEventListener] = {}
        self._next_event_listener_id = 1
        self._status = "idle"
        self._detail = "Ready"
        self._leaf_id = "root"
        self._current_user_request = ""
        self._current_run_id = ""
        self._cancelled_run_ids: set[str] = set()
        self._model_calls = 0
        self._max_model_calls = max(1, int(max_model_calls))
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
            user_request=lambda _event: self.current_user_request,
        )
        normalized_tree_id = str(tree_id or "demo")
        try:
            self.tree = self.store.get_tree(normalized_tree_id)
        except TreeNotFoundError:
            self.tree = self.store.create_tree(
                {
                    "role": "system",
                    "content": str(system_prompt or DEFAULT_SYSTEM_PROMPT).replace(
                        "{workspace}", str(self.workspace)
                    ),
                },
                tree_id=normalized_tree_id,
                root_id=self._leaf_id,
                initial_hooks=(
                    HookRegistration(
                        event=CONTEXT_CHANGE,
                        plugin_id="agent.session.transition",
                        plugin=self._context_changed,
                        hook_id="agent-session-transition",
                    ),
                    permission.registration(),
                ),
            )
        self.hooks = self.store.hooks_for(self.tree.id)
        existing_hooks = {hook.id for hook in self.hooks.list()}
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
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        self._event_sequence = sum(
            self._event_for_node(node, sequence=0) is not None for node in nodes
        )
        self._unsubscribe_context_events = self.store.subscribe(
            self._context_output_changed,
            tree_id=self.tree.id,
        )
        self._restore()
        self._transition_thread.start()

    @property
    def current_user_request(self) -> str:
        with self._state_lock:
            return self._current_user_request

    @property
    def current_run_id(self) -> str:
        with self._state_lock:
            return self._current_run_id

    @property
    def plugin_context_data(self) -> dict[str, Any]:
        """Return the host data included in every Plugin invocation."""

        return dict(self._plugin_context_data)

    def _plugin_data(self, *, run_id: str = "", **details: Any) -> dict[str, Any]:
        data = dict(self._plugin_context_data)
        if run_id:
            data["run_id"] = run_id
        data.update(details)
        return data

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
            for event in self.events(after_sequence=after_sequence):
                listener(event)
        with self._event_lock:
            listener_id = self._next_event_listener_id
            self._next_event_listener_id += 1
            self._event_listeners[listener_id] = listener

        def unsubscribe() -> None:
            with self._event_lock:
                self._event_listeners.pop(listener_id, None)

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
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception(
                    "Agent output listener failed (tree=%s, event=%s)",
                    self.tree.id,
                    event.type,
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
        return tuple(event for event in events if event.sequence > int(after_sequence))

    def _context_output_changed(self, change: ContextChange) -> None:
        if change.action != "mount":
            return
        try:
            node = self.store.get_node(change.tree_id, change.node_id)
        except Exception:
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

    def _set_state(self, status: str, detail: str, *, leaf_id: str | None = None) -> None:
        with self._state_lock:
            self._status = status
            self._detail = detail
            if leaf_id is not None:
                self._leaf_id = leaf_id
            run_id = self._current_run_id
            current_leaf_id = self._leaf_id
        self._emit_event(
            "session.state",
            run_id=run_id,
            node_id=current_leaf_id,
            data={"status": status, "detail": detail, "leaf_id": current_leaf_id},
        )

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

    def _is_cancelled(self, run_id: str) -> bool:
        with self._state_lock:
            return bool(run_id and run_id in self._cancelled_run_ids)

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))

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
                return
            self._transition_pending.add(key)
            self._transition_work.put((kind, node))
            self._transition_condition.notify_all()

    def _transition_worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._transition_condition:
            self._transition_loop = loop
            self._transition_condition.notify_all()
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
                            continue
                    coroutine = (
                        self._advance(node)
                        if kind == "advance"
                        else self._continue_tools(node)
                    )
                    task = loop.create_task(coroutine)
                    with self._transition_condition:
                        self._active_transition_task = task
                        self._active_transition_run_id = run_id
                        self._transition_condition.notify_all()
                    loop.run_until_complete(task)
                except asyncio.CancelledError:
                    pass
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    self._mount_assistant(
                        node.id,
                        f"Agent transition failed: {exc}",
                        error=True,
                        caused_by=self._transition_key(node),
                        run_id=run_id,
                    )
                finally:
                    with self._transition_condition:
                        self._active_transition_task = None
                        self._active_transition_run_id = ""
                        self._transition_pending.discard(key)
                        self._transition_condition.notify_all()
        finally:
            with self._transition_condition:
                self._active_transition_task = None
                self._active_transition_run_id = ""
                self._transition_loop = None
                self._transition_condition.notify_all()
            loop.close()

    def _wait_for_transitions(self) -> None:
        with self._transition_condition:
            while self._transition_pending:
                self._transition_condition.wait()

    def _restore(self) -> None:
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
            and node.value.get("role") in {"system", "user", "assistant", "tool_results"}
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
            return
        if value.get("role") == "assistant" and value.get("tool_calls"):
            if self._batch_result_node(leaf) is None:
                self._set_state("queued", "Resuming tool batch", leaf_id=leaf.id)
                self._enqueue_transition("tools", leaf)
            return
        if value.get("trigger_model") is True and self._transition_assistant(leaf) is None:
            self._set_state("queued", "Resuming model transition", leaf_id=leaf.id)
            self._enqueue_transition("advance", leaf)
            return
        self._set_state("idle", "Restored", leaf_id=leaf.id)
        if value.get("role") == "assistant":
            self._current_user_request = ""

    def submit(
        self,
        text: str,
        *,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextNode:
        content = str(text or "").strip()
        if not content:
            raise ValueError("message cannot be empty")
        normalized_run_id = str(run_id or f"run_{uuid4().hex}").strip()
        if not normalized_run_id:
            raise ValueError("run_id cannot be empty")
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
            self._model_calls = 0
        try:
            node = self.store.mount(
                self.tree.id,
                parent_id,
                {
                    "role": "user",
                    "content": content,
                    "trigger_model": True,
                    "run_id": normalized_run_id,
                    "metadata": deepcopy(dict(metadata or {})),
                },
            )
        except Exception:
            self._set_state("idle", "Mount failed")
            raise
        self._set_state("queued", "Waiting for ContextChange Hook", leaf_id=node.id)
        return node

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
                self._set_state("idle", "Complete", leaf_id=existing.id)
                with self._state_lock:
                    self._current_user_request = ""
            return
        with self._state_lock:
            self._model_calls += 1
            count = self._model_calls
        if count > self._max_model_calls:
            self._mount_assistant(
                trigger.id,
                "Stopped because the model-call limit for this user turn was reached.",
                error=True,
                caused_by=self._transition_key(trigger),
                run_id=run_id,
            )
            return

        self._set_state("model", f"Calling {self.model_plugin} ({count}/{self._max_model_calls})")
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
            ),
        )
        if self._is_cancelled(run_id):
            return
        if not result.success or not isinstance(result.value, Mapping):
            self._mount_assistant(
                trigger.id,
                result.error or "Model call failed",
                error=True,
                caused_by=self._transition_key(trigger),
                run_id=run_id,
            )
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
                "run_id": run_id,
                "caused_by": transition_key,
                "batch_key": batch_key,
                "effect_results": {},
            },
            node_id=self._stable_id("assistant", transition_key),
        )
        self._set_state("tools" if calls else "idle", "Executing tools" if calls else "Complete", leaf_id=assistant.id)
        if not calls:
            with self._state_lock:
                self._current_user_request = ""
            return

        await self._continue_tools(assistant)

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
            self._set_state(
                "model",
                "Tool results restored; waiting for model",
                leaf_id=existing_result.id,
            )
            if self._transition_assistant(existing_result) is None:
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
            self._mount_assistant(
                assistant.id,
                "The model returned no valid tool calls.",
                error=True,
                caused_by=str(value.get("batch_key") or ""),
                run_id=run_id,
            )
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
        self._set_state("tools", "Reviewing and executing tools", leaf_id=assistant.id)
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
            ),
            completed=completed,
            on_result=lambda result: self._persist_effect_result(assistant.id, result),
        )
        if self._is_cancelled(run_id):
            return
        batch_key = str(value.get("batch_key") or self._stable_id("batch", assistant.id))
        tool_node = self.store.mount(
            self.tree.id,
            assistant.id,
            {
                "role": "tool_results",
                "trigger_model": True,
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
            },
            node_id=self._stable_id("tool_results", batch_key),
        )
        self._set_state("model", "Tool results mounted; waiting for model", leaf_id=tool_node.id)

    def _mount_assistant(
        self,
        parent_id: str,
        content: str,
        *,
        error: bool,
        caused_by: str = "",
        run_id: str = "",
    ) -> None:
        node_id = self._stable_id("assistant_error", caused_by) if caused_by else None
        if node_id is not None:
            try:
                existing = self.store.get_node(self.tree.id, node_id)
            except Exception:
                existing = None
            if existing is not None:
                self._set_state("idle", "Failed", leaf_id=existing.id)
                return
        node = self.store.mount(
            self.tree.id,
            parent_id,
            {
                "role": "assistant",
                "content": str(content),
                "error": bool(error),
                "run_id": str(run_id or self.current_run_id),
                "caused_by": caused_by,
            },
            node_id=node_id,
        )
        self._set_state("idle", "Failed" if error else "Complete", leaf_id=node.id)
        with self._state_lock:
            self._current_user_request = ""

    def _messages(self, node_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for node in self.store.get_path(self.tree.id, node_id):
            value = node.value if isinstance(node.value, Mapping) else {}
            role = str(value.get("role") or "")
            if role in {"system", "user"}:
                messages.append({"role": role, "content": str(value.get("content") or "")})
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

    async def _permission_model(
        self,
        system_prompt: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
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
                node_id=self._leaf_id,
                data=self._plugin_data(
                    run_id=self.current_run_id,
                    model_call_kind="permission",
                    user_request=self.current_user_request,
                ),
            ),
        )
        if not result.success or not isinstance(result.value, Mapping):
            raise RuntimeError(result.error or "permission model failed")
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
            if self._closed or self._status == "idle" or not self._current_run_id:
                return False
            run_id = self._current_run_id
            parent_id = self._leaf_id
            self._cancelled_run_ids.add(run_id)
            self._status = "cancelling"
            self._detail = normalized_reason
        self._emit_event(
            "session.state",
            run_id=run_id,
            node_id=parent_id,
            data={
                "status": "cancelling",
                "detail": normalized_reason,
                "leaf_id": parent_id,
            },
        )

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
        with self._state_lock:
            self._leaf_id = cancelled.id
            self._current_user_request = ""

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
        elif not has_pending:
            self._set_state("idle", "Cancelled", leaf_id=cancelled.id)
        return True

    async def cancel(
        self,
        reason: str = "user_cancelled",
        *,
        timeout: float | None = None,
    ) -> bool:
        """Cancel the active run, notify Stop Hooks, and wait for it to settle."""

        changed = self.request_cancel(reason)
        if not changed:
            return False

        async def settle() -> None:
            await self.hooks.stop(
                reason,
                {"run_id": self.current_run_id},
            )
            await asyncio.to_thread(self._wait_for_transitions)

        if timeout is None:
            await settle()
        else:
            await asyncio.wait_for(settle(), timeout=max(0.0, float(timeout)))
        self._set_state("idle", "Cancelled")
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
            return None
        node = max(candidates, key=lambda item: (item.created_at, item.id))
        return {
            "node_id": node.id,
            **deepcopy(dict(node.value)),
        }

    def snapshot(self) -> dict[str, Any]:
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        with self._state_lock:
            status = self._status
            detail = self._detail
            leaf_id = self._leaf_id
            run_id = self._current_run_id
        with self._event_lock:
            event_sequence = self._event_sequence
        return {
            "tree_id": self.tree.id,
            "root_id": self.tree.root_id,
            "leaf_id": leaf_id,
            "status": status,
            "detail": detail,
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

    async def drain(self) -> None:
        """Wait until queued Hooks and all resulting transitions are idle."""

        for _ in range(self._max_model_calls + 2):
            await asyncio.shield(self.hooks.drain())
            await asyncio.to_thread(self._wait_for_transitions)
            await asyncio.shield(self.hooks.drain())
            with self._transition_condition:
                pending = bool(self._transition_pending)
            with self._state_lock:
                idle = self._status == "idle"
            if not pending and idle:
                return
        raise RuntimeError("Agent session did not become idle while draining")

    def close(self) -> None:
        """Stop process-local workers while leaving unfinished tree state recoverable."""

        with self._transition_condition:
            if self._closed:
                return
            self._closed = True
            loop = self._transition_loop
            task = self._active_transition_task
            if loop is not None and task is not None and not task.done():
                loop.call_soon_threadsafe(task.cancel)
            self._transition_work.put(None)
            self._transition_condition.notify_all()
        if self._transition_thread is not threading.current_thread():
            self._transition_thread.join()
        self._unsubscribe_context_events()
        self.store.close()


AgentTreeSession = AgentSession


__all__ = [
    "AgentEventListener",
    "AgentSession",
    "AgentSessionEvent",
    "AgentTreeSession",
    "DEFAULT_SYSTEM_PROMPT",
]
