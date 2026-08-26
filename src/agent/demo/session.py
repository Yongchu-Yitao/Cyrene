"""A minimal event-driven Agent session built from Context, Hook, and Plugin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..context import ContextNode, ContextStoreRouter, TreeNotFoundError
from ..hook import CONTEXT_CHANGE, HookEvent, HookRegistration
from ..plugin import (
    PluginBatchRunner,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginRegistry,
    PluginRuntime,
)
from ..plugin.core_impl import (
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
    PermissionReviewPlugin,
)

DEFAULT_SYSTEM_PROMPT = """You are Cyrene, an agent running on a Context Tree.
Answer directly when no tool is needed. Use the available tools when they are necessary.
After receiving tool results, explain the result to the user instead of repeating the same call.
The workspace is {workspace}.
"""

class AgentTreeSession:
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
        self.store = ContextStoreRouter(Path(data_directory) / "context")
        self._state_lock = threading.RLock()
        self._status = "idle"
        self._detail = "Ready"
        self._leaf_id = "root"
        self._current_user_request = ""
        self._model_calls = 0
        self._max_model_calls = max(1, int(max_model_calls))
        self._closed = False
        self._transition_condition = threading.Condition(threading.RLock())
        self._transition_pending: set[str] = set()
        self._transition_work: queue.Queue[tuple[str, ContextNode] | None] = queue.Queue()
        self._transition_thread = threading.Thread(
            target=self._transition_worker_main,
            name=f"agent-transition-{tree_id}",
            daemon=True,
        )
        self._transition_thread.start()

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
                    "content": DEFAULT_SYSTEM_PROMPT.format(workspace=self.workspace),
                },
                tree_id=normalized_tree_id,
                root_id=self._leaf_id,
                initial_hooks=(
                    HookRegistration(
                        event=CONTEXT_CHANGE,
                        plugin_id="demo.agent.transition",
                        plugin=self._context_changed,
                        hook_id="demo-agent-transition",
                    ),
                    permission.registration(),
                ),
            )
        self.hooks = self.store.hooks_for(self.tree.id)
        existing_hooks = {hook.id for hook in self.hooks.list()}
        if "demo-agent-transition" in existing_hooks:
            self.hooks.bind_plugin(
                "demo.agent.transition",
                self._context_changed,
                replace=True,
            )
        else:
            self.hooks.register(
                CONTEXT_CHANGE,
                self._context_changed,
                plugin_id="demo.agent.transition",
                hook_id="demo-agent-transition",
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
        self._restore()

    @property
    def current_user_request(self) -> str:
        with self._state_lock:
            return self._current_user_request

    def _set_state(self, status: str, detail: str, *, leaf_id: str | None = None) -> None:
        with self._state_lock:
            self._status = status
            self._detail = detail
            if leaf_id is not None:
                self._leaf_id = leaf_id

    @staticmethod
    def _stable_id(prefix: str, key: str) -> str:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}_{digest}"

    @staticmethod
    def _transition_key(node: ContextNode) -> str:
        return f"{node.id}:{node.updated_at.isoformat()}"

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
        with self._transition_condition:
            if self._closed or key in self._transition_pending:
                return
            self._transition_pending.add(key)
            self._transition_work.put((kind, node))
            self._transition_condition.notify_all()

    def _transition_worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while True:
                item = self._transition_work.get()
                if item is None:
                    return
                kind, node = item
                key = f"{kind}:{self._transition_key(node)}"
                try:
                    if kind == "advance":
                        loop.run_until_complete(self._advance(node))
                    else:
                        loop.run_until_complete(self._continue_tools(node))
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    self._mount_assistant(
                        node.id,
                        f"Agent transition failed: {exc}",
                        error=True,
                        caused_by=self._transition_key(node),
                    )
                finally:
                    with self._transition_condition:
                        self._transition_pending.discard(key)
                        self._transition_condition.notify_all()
        finally:
            loop.close()

    def _wait_for_transitions(self) -> None:
        with self._transition_condition:
            while self._transition_pending:
                self._transition_condition.wait()

    def _restore(self) -> None:
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
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

    def submit(self, text: str) -> ContextNode:
        content = str(text or "").strip()
        if not content:
            raise ValueError("message cannot be empty")
        with self._state_lock:
            if self._status != "idle":
                raise RuntimeError("the Agent is still processing the previous message")
            parent_id = self._leaf_id
            self._status = "queued"
            self._detail = "User context mounted"
            self._current_user_request = content
            self._model_calls = 0
        try:
            node = self.store.mount(
                self.tree.id,
                parent_id,
                {"role": "user", "content": content, "trigger_model": True},
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
        self._enqueue_transition("advance", node)

    async def _advance(self, trigger: ContextNode) -> None:
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
                data={"model_call_kind": "agent"},
            ),
        )
        if not result.success or not isinstance(result.value, Mapping):
            self._mount_assistant(
                trigger.id,
                result.error or "Model call failed",
                error=True,
                caused_by=self._transition_key(trigger),
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
            "value": AgentTreeSession._json_value(result.value),
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

    async def _continue_tools(self, assistant: ContextNode) -> None:
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
            ),
            completed=completed,
            on_result=lambda result: self._persist_effect_result(assistant.id, result),
        )
        batch_key = str(value.get("batch_key") or self._stable_id("batch", assistant.id))
        tool_node = self.store.mount(
            self.tree.id,
            assistant.id,
            {
                "role": "tool_results",
                "trigger_model": True,
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
                data={"model_call_kind": "permission"},
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

    def snapshot(self) -> dict[str, Any]:
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        with self._state_lock:
            status = self._status
            detail = self._detail
            leaf_id = self._leaf_id
        return {
            "tree_id": self.tree.id,
            "root_id": self.tree.root_id,
            "leaf_id": leaf_id,
            "status": status,
            "detail": detail,
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
            await self.hooks.drain()
            await asyncio.to_thread(self._wait_for_transitions)
            await self.hooks.drain()
            with self._transition_condition:
                pending = bool(self._transition_pending)
            with self._state_lock:
                idle = self._status == "idle"
            if not pending and idle:
                return
        raise RuntimeError("Agent session did not become idle while draining")

    def close(self) -> None:
        with self._transition_condition:
            if self._closed:
                return
        self._wait_for_transitions()
        with self._transition_condition:
            self._closed = True
            self._transition_work.put(None)
            self._transition_condition.notify_all()
        if self._transition_thread is not threading.current_thread():
            self._transition_thread.join()
        self.store.close()


__all__ = ["AgentTreeSession", "DEFAULT_SYSTEM_PROMPT"]
