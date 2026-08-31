"""Production event-driven Agent session built from Context, Hook, and Plugin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
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
from .context.projection import (
    project_context_message,
    selected_context_node_ids,
)
from .hook import (
    CONTEXT_CHANGE,
    PRE_TOOL_USE,
    SESSION_START,
    TURN_START,
    HookEvent,
    HookRegistration,
)
from .observability import log_operation, operation
from .plugin import (
    PluginBatchRunner,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginFailure,
    PluginLoadFailure,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
    PluginSetupContext,
    TOOLBOX_PLUGIN_NAME,
    plugin_session_state,
    split_resource_reveal,
    with_plugin_session_state,
    workspace_resource_locations,
)
from .plugin.core_impl import (
    PERMISSION_BATCH_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
    PermissionDecision,
    PermissionRequirement,
    PermissionReviewPlugin,
)
from .plugin.scopes import ApplicationPluginScope, application_plugin_scope
from .localization import localized, normalize_language, system_language


logger = logging.getLogger(__name__)
_DEFAULT_INITIAL_ROOT = object()
_AGENT_LIFECYCLE_STATE_ID = "agent.lifecycle"


def _l(en: str, zh: str, **values: Any) -> str:
    return localized(en, zh, **values)


def _model_failure_projection(
    raw_details: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    details = dict(raw_details or {})
    message = _l(
        str(details.get("message_en") or "The model call failed."),
        str(details.get("message_zh") or "模型调用失败。"),
    )
    metadata: dict[str, Any] = {
        "failure_kind": str(details.get("code") or "model_call_failed"),
        "detail_key": str(
            details.get("detail_key") or "workbenchChat.error.modelCallFailed"
        ),
        "detail_params": dict(details.get("detail_params") or {}),
        "retryable": bool(details.get("retryable", True)),
    }
    status_code = int(details.get("status_code") or 0)
    if status_code:
        metadata["status_code"] = status_code
    return message, metadata

AgentEventType = Literal[
    "session.state",
    "input.accepted",
    "input.answered",
    "guidance.applied",
    "assistant.tool_calls",
    "permission.reviewed",
    "tool.completed",
    "tools.completed",
    "assistant.completed",
    "assistant.stream.started",
    "assistant.stream.delta",
    "assistant.stream.done",
    "assistant.reasoning.started",
    "assistant.reasoning.delta",
    "assistant.reasoning.done",
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


class _SetupHookTracker:
    """Record Hooks a pack setup creates or rebinds without constraining it."""

    def __init__(self, hooks: Any) -> None:
        self._hooks = hooks
        self.touched: set[str] = set()
        self.created: set[str] = set()
        self._previous_plugins: dict[str, Any | None] = {}
        self._previous_configs: dict[str, Mapping[str, Any]] = {}
        self._previous_failure_policies: dict[str, str] = {}

    def register(self, *args: Any, **kwargs: Any) -> Any:
        before = {hook.id for hook in self._hooks.list()}
        unsubscribe = self._hooks.register(*args, **kwargs)
        created = {hook.id for hook in self._hooks.list() if hook.id not in before}
        self.created.update(created)
        self.touched.update(created)
        return unsubscribe

    def bind_plugin(self, plugin_id: str, *args: Any, **kwargs: Any) -> Any:
        normalized_id = str(plugin_id)
        if normalized_id not in self._previous_plugins:
            self._previous_plugins[normalized_id] = self._hooks._plugins.resolve(
                normalized_id
            )
        result = self._hooks.bind_plugin(plugin_id, *args, **kwargs)
        self.touched.update(
            hook.id
            for hook in self._hooks.list()
            if hook.plugin_id == str(plugin_id)
        )
        return result

    def update_config(self, hook_id: str, config: Mapping[str, Any]) -> None:
        normalized_id = str(hook_id)
        if normalized_id not in self._previous_configs:
            previous = next(
                (hook for hook in self._hooks.list() if hook.id == normalized_id),
                None,
            )
            if previous is not None:
                self._previous_configs[normalized_id] = dict(previous.config)
        self._hooks.update_config(normalized_id, config)
        self.touched.add(normalized_id)

    def update_failure_policy(self, hook_id: str, failure_policy: str) -> None:
        normalized_id = str(hook_id)
        if normalized_id not in self._previous_failure_policies:
            previous = next(
                (hook for hook in self._hooks.list() if hook.id == normalized_id),
                None,
            )
            if previous is not None:
                self._previous_failure_policies[normalized_id] = (
                    previous.failure_policy
                )
        self._hooks.update_failure_policy(normalized_id, failure_policy)
        self.touched.add(normalized_id)

    def rollback(self) -> None:
        """Undo a failed setup without deleting restored durable bindings."""

        for hook_id in self.created:
            self._hooks.unregister(hook_id)
        for hook_id, config in self._previous_configs.items():
            if hook_id not in self.created:
                self._hooks.update_config(hook_id, config)
        for hook_id, failure_policy in self._previous_failure_policies.items():
            if hook_id not in self.created:
                self._hooks.update_failure_policy(hook_id, failure_policy)
        for plugin_id, previous in self._previous_plugins.items():
            if previous is None:
                self._hooks._plugins.unregister(plugin_id)
            else:
                self._hooks.bind_plugin(plugin_id, previous, replace=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hooks, name)


@dataclass(slots=True)
class _SessionPackAttachment:
    pack: PluginPack
    source: str
    setup_fingerprint: tuple[Any, ...]
    hooks: set[str]
    previous_services: dict[str, tuple[bool, Any]]
    provided_services: dict[str, Any]
    driver: Any = None


@dataclass(frozen=True, slots=True)
class _PreparedModelInput:
    """One internally consistent projection used by a model transition."""

    trigger_id: str
    registry_sync_token: tuple[int, int, int, int]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    message_tokens: int
    tool_tokens: int
    compaction_tokens: int
    routing_tokens: int
    services: dict[str, Any]


class AgentSession:
    """One tree whose passive trigger nodes advance the Agent state machine."""

    def __init__(
        self,
        data_directory: str | Path,
        workspace: str | Path,
        plugin_directory: str | Path,
        *,
        model_plugin: str = "MiniMax",
        max_model_calls: int | None = None,
        tree_id: str = "agent-session",
        registry: PluginRegistry | None = None,
        host_context: Mapping[str, Any] | None = None,
        plugin_context_data: Mapping[str, Any] | None = None,
        plugin_services: Mapping[str, Any] | None = None,
        application_scope: ApplicationPluginScope | None = None,
        initial_root_value: Any = _DEFAULT_INITIAL_ROOT,
        agent_id: str = "main",
        parent_agent_id: str = "",
        extra_direct_tool_names: Sequence[str] = (),
        load_plugins: bool = True,
        permission_user_request: str | None = None,
    ) -> None:
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.plugin_directory = Path(plugin_directory).expanduser().resolve()
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.agent_id = str(agent_id or "main").strip() or "main"
        self.parent_agent_id = str(parent_agent_id or "").strip()
        self._extra_direct_tool_names = tuple(
            dict.fromkeys(
                str(name or "").strip()
                for name in extra_direct_tool_names
                if str(name or "").strip()
            )
        )
        context_values = {
            **dict(host_context or {}),
            **dict(plugin_context_data or {}),
        }
        run_context = context_values.get("run_context")
        self._read_only = bool(
            context_values.get("read_only") is True
            or (
                isinstance(run_context, Mapping)
                and run_context.get("read_only") is True
            )
        )
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
        self._model_tools = self._direct_model_tool_definitions()
        model = self.registry.resolve(model_plugin)
        if model.kind != "model":
            raise ValueError(f"Plugin is not a model component: {model_plugin}")
        self.model_plugin = model_plugin
        self.runtime = PluginRuntime(self.registry)
        self.batch = PluginBatchRunner(self.runtime)
        self._plugin_context_data = context_values
        self._plugin_service_values = dict(plugin_services or {})
        self._application_scope = application_scope or application_plugin_scope()
        if "model" not in self._plugin_service_values:
            from .plugin.model import RuntimeModelGateway

            self._plugin_service_values["model"] = RuntimeModelGateway(
                self.runtime,
                self.model_plugin,
            )
        self._plugin_reconcile_lock = threading.RLock()
        self._session_start_build_lock = threading.Lock()
        self._plugin_pack_attachments: dict[str, _SessionPackAttachment] = {}
        self._plugin_setup_failures: dict[str, str] = {}
        self._plugin_load_failures: tuple[PluginLoadFailure, ...] = tuple(failures)
        self._required_session_pack_ids: set[str] = {
            pack.id
            for pack in self.registry.list_packs()
            if pack.has_session_contributions and bool(pack.metadata.get("required"))
        }
        self._plugin_sync_token: tuple[Any, ...] | None = None
        self._authoritative_directory_revision: int | None = None
        self._authoritative_customization_revision: int | None = None
        self._host_service_names: set[str] = set()
        self._capture_application_host_services()
        self.store = ContextStoreRouter(self.data_directory / "context")
        self._tree_id_hint = str(tree_id or "agent-session")
        self._state_lock = threading.RLock()
        self._context_event_deferral = threading.local()
        self._event_lock = threading.RLock()
        self._event_sequence = 0
        self._event_listeners: dict[int, AgentEventListener] = {}
        self._next_event_listener_id = 1
        self._status = "idle"
        self._detail = _l("Ready", "就绪")
        self._leaf_id = "root"
        self._current_user_request = ""
        self._current_run_id = ""
        self._run_permission_user_request = ""
        self._permission_once_grants: set[str] = set()
        self._permission_session_grants: set[str] = set()
        self._explicit_delegation_quotes: set[str] = set()
        self._explicit_delegation_batches: dict[str, tuple[tuple[str, ...], int]] = {}
        if "permission" in self._plugin_service_values:
            raise ValueError("Plugin service name is reserved: permission")
        self._plugin_service_values["permission"] = self
        self._cancelled_run_ids: set[str] = set()
        self._model_calls = 0
        self._max_model_calls = (
            None if max_model_calls is None else max(1, int(max_model_calls))
        )
        self._streamed_transition_keys: set[str] = set()
        self._session_driver: Any = None
        self._owns_session_driver = False
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
            policy=self._permission_requirement,
            on_review=self._record_permission_review,
        )
        self._permission_review_plugin = permission
        normalized_tree_id = self._tree_id_hint
        try:
            self.tree = self.store.get_tree(normalized_tree_id)
        except TreeNotFoundError:
            root_value = (
                {
                    "role": "system",
                    "content": "",
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
                        plugin_id="cyrene.core.session.context_mount",
                        plugin=self._context_mount_changed,
                        hook_id="agent-session-context-mount",
                    ),
                    HookRegistration(
                        event=CONTEXT_CHANGE,
                        plugin_id="cyrene.core.session.transition",
                        plugin=self._context_changed,
                        hook_id="agent-session-transition",
                    ),
                    permission.registration(),
                ),
            )
        root_node = self.store.get_node(self.tree.id, self.tree.root_id)
        self._initial_root_value = deepcopy(root_node.value)
        self.hooks = self.store.hooks_for(self.tree.id)
        existing_hooks = {hook.id for hook in self.hooks.list()}
        if "agent-session-context-mount" in existing_hooks:
            self.hooks.bind_plugin(
                "cyrene.core.session.context_mount",
                self._context_mount_changed,
                replace=True,
            )
        else:
            self.hooks.register(
                CONTEXT_CHANGE,
                self._context_mount_changed,
                plugin_id="cyrene.core.session.context_mount",
                hook_id="agent-session-context-mount",
            )
        if "agent-session-transition" in existing_hooks:
            self.hooks.bind_plugin(
                "cyrene.core.session.transition",
                self._context_changed,
                replace=True,
            )
        else:
            self.hooks.register(
                CONTEXT_CHANGE,
                self._context_changed,
                plugin_id="cyrene.core.session.transition",
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
        self._attach_plugin_packs()
        self.hooks.set_before_dispatch(self.reconcile_plugins)
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        self._event_sequence = sum(
            self._event_for_node(node, sequence=0) is not None for node in nodes
        )
        self._unsubscribe_context_events = self.store.subscribe(
            self._context_output_changed,
            tree_id=self.tree.id,
        )
        self._restore()
        log_operation(
            logger,
            "cyrene.core.session",
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
        if self._owns_session_driver:
            self._session_driver.attach()

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
    def max_model_calls(self) -> int | None:
        return self._max_model_calls

    @property
    def permission_user_request(self) -> str:
        if self._permission_user_request is not None:
            return self._permission_user_request
        with self._state_lock:
            return self._run_permission_user_request or self._current_user_request

    @staticmethod
    def _permission_fingerprint(
        tool_name: str,
        arguments: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> str:
        explicit = str(request.get("fingerprint") or "").strip()
        if explicit:
            return explicit
        payload = {
            "tool": str(tool_name or "").strip(),
            "arguments": dict(arguments),
            "kind": str(request.get("kind") or "scope_elevation"),
            "operation": str(request.get("operation") or ""),
            "path_hint": str(request.get("path_hint") or ""),
            "reason": str(request.get("reason") or "")[:500],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _consume_permission_grant(self, fingerprint: str) -> bool:
        normalized = str(fingerprint or "").strip()
        if not normalized:
            return False
        with self._state_lock:
            if normalized in self._permission_session_grants:
                return True
            if normalized in self._permission_once_grants:
                self._permission_once_grants.remove(normalized)
                return True
        return False

    def _persist_session_permission_grant(self, fingerprint: str) -> None:
        normalized = str(fingerprint or "").strip()
        if not normalized:
            return
        root = self.store.get_node(self.tree.id, self.tree.root_id)
        value = dict(root.value) if isinstance(root.value, Mapping) else {}
        grants = {
            str(item).strip()
            for item in value.get("permission_session_grants") or ()
            if str(item).strip()
        }
        grants.add(normalized)
        value["permission_session_grants"] = sorted(grants)
        self.store.update_node(self.tree.id, root.id, value)

    def _permission_requirement(self, event: HookEvent) -> PermissionRequirement:
        """Apply the 0.7.13 boundary/mode rules inside the review Plugin."""

        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_request = payload.get("permission")
        if not isinstance(raw_request, Mapping):
            return PermissionRequirement(
                "allow",
                "The Plugin reported no permission boundary.",
            )
        request = dict(raw_request)
        tool = payload.get("tool") if isinstance(payload, Mapping) else None
        tool = tool if isinstance(tool, Mapping) else {}
        tool_name = str(tool.get("name") or "")
        arguments = tool.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        fingerprint = self._permission_fingerprint(tool_name, arguments, request)
        if self._consume_permission_grant(fingerprint):
            return PermissionRequirement(
                "allow",
                "An exact user-approved permission grant was consumed.",
            )

        run_context = self._plugin_context_data.get("run_context")
        run_context = run_context if isinstance(run_context, Mapping) else {}
        context_agent_id = str(run_context.get("agent_id") or "").strip()
        agent_id = (
            str(self.agent_id or "main")
            if self.agent_id != "main"
            else context_agent_id or "main"
        )
        operation = str(request.get("operation") or "受限操作")
        if agent_id != "main":
            return PermissionRequirement(
                "deny",
                f"Subagent 无权申请权限提升：{operation}",
            )
        if bool(run_context.get("system_initiated")) or agent_id == "scheduler":
            return PermissionRequirement(
                "deny",
                f"系统发起的后台轮次不能申请用户权限：{operation}",
            )

        mode = str(run_context.get("permission_mode") or "default").strip().lower()
        temporary_full_access = bool(run_context.get("temporary_full_access"))
        kind = str(request.get("kind") or "scope_elevation")
        requires_human = bool(request.get("requires_human")) or kind in {
            "destructive_confirmation",
            "external_upload_confirmation",
            "self_configuration_confirmation",
            "host_lifecycle_confirmation",
        }
        always_review = bool(request.get("always_review")) or kind == "extension_change"
        if bool(run_context.get("bounded_remote_authorization")) and not requires_human:
            return PermissionRequirement(
                "allow",
                "A bounded remote authorization covers this exact invocation.",
            )
        global_full_path_access = False
        if kind in {"read_elevation", "write_permission_request"}:
            try:
                from cyrene.platform.settings_store import get_write_permission_mode

                global_full_path_access = get_write_permission_mode() == "full_access"
            except Exception:
                global_full_path_access = False
        if (mode == "full_access" or temporary_full_access) and not (
            requires_human or always_review
        ):
            return PermissionRequirement(
                "allow",
                "Full-access mode covers this ordinary elevation boundary.",
            )
        if global_full_path_access:
            return PermissionRequirement(
                "allow",
                "The configured full-path-access mode covers this file boundary.",
            )
        if (mode == "auto" or always_review) and not requires_human:
            return PermissionRequirement("review")

        path_hint = str(request.get("path_hint") or "").strip()
        reason = str(request.get("reason") or "").strip()
        detail = f"\n📂 目标：{path_hint}" if path_hint else ""
        why = f"\n💡 原因：{reason}" if reason else ""
        authored_options = request.get("options")
        if requires_human:
            options = (
                [str(item) for item in authored_options if str(item).strip()]
                if isinstance(authored_options, list) and authored_options
                else ["允许这次", "拒绝"]
                if bool(request.get("single_use"))
                else ["允许这次", "本次会话内总是允许", "拒绝"]
            )
        else:
            options = (
                [str(item) for item in authored_options if str(item).strip()]
                if isinstance(authored_options, list) and authored_options
                else ["在本次会话同意", "同意一次", "拒绝"]
            )
        scope_hint = str(request.get("scope_hint") or "").strip()
        question = {
            "status": "awaiting_user",
            "question_id": f"permission_{fingerprint[:24]}",
            "kind": kind,
            "text": (
                f"⚠️ Agent 尝试执行 {scope_hint}{operation}\n\n"
                f"工具：{tool_name}{detail}{why}\n\n"
                "请确认是否允许此精确操作。"
            ),
            "options": options,
            "allow_custom": False,
            "round_id": str(run_context.get("round_id") or self._current_run_id),
            "client_request_id": str(run_context.get("client_request_id") or ""),
            "permission": {
                **request,
                "fingerprint": fingerprint,
                "tool_name": tool_name,
            },
        }
        return PermissionRequirement(
            "confirm",
            "This boundary requires an exact user decision.",
            question,
        )

    def request_permission(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve a dynamic Plugin boundary discovered inside its handler."""

        requirement = self._permission_requirement(HookEvent(
            PRE_TOOL_USE,
            self.tree.id,
            datetime.now(timezone.utc),
            payload={
                "tool": {
                    "name": str(tool_name or ""),
                    "arguments": dict(arguments),
                },
                "permission": dict(request),
            },
        ))
        if requirement.action == "allow":
            return None
        if requirement.action == "confirm":
            return dict(requirement.question or {})
        if requirement.action == "deny":
            return {
                "status": "denied",
                "error": requirement.rationale or "Permission denied.",
            }
        raise RuntimeError(
            "Dynamic permission requests requiring automatic review must be "
            "awaited through request_dynamic_permission."
        )

    async def request_dynamic_permission(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve a handler-discovered boundary, including auto review."""

        event = HookEvent(
            PRE_TOOL_USE,
            self.tree.id,
            datetime.now(timezone.utc),
            payload={
                "tool": {
                    "name": str(tool_name or ""),
                    "arguments": dict(arguments),
                },
                "permission": dict(request),
            },
        )
        output = (await self._permission_review_plugin.review_batch((event,)))[0]
        decision = str(output.get("decision") or "block").strip().lower()
        if decision == "allow":
            return None
        if decision == "ask":
            question = output.get("question")
            return dict(question) if isinstance(question, Mapping) else {
                "status": "denied",
                "error": "Permission confirmation payload was invalid.",
            }
        return {
            "status": "denied",
            "error": str(output.get("reason") or "Permission denied."),
        }

    def explicit_delegation_status(
        self,
        *,
        quote_identity: str,
        batch_id: str,
        operation_keys: Sequence[str],
        current_operation_key: str,
    ) -> str:
        """Return whether an exact 0.7.13-style delegation batch can advance."""

        keys = tuple(str(item or "").strip() for item in operation_keys)
        quote_key = str(quote_identity or "").strip()
        normalized_batch_id = str(batch_id or "").strip()
        current_key = str(current_operation_key or "").strip()
        if not quote_key or not normalized_batch_id or not keys or any(not key for key in keys):
            return "invalid"
        with self._state_lock:
            record = self._explicit_delegation_batches.get(normalized_batch_id)
            if record is None:
                if quote_key in self._explicit_delegation_quotes or current_key != keys[0]:
                    return "invalid"
                return "missing"
            recorded_keys, next_index = record
            if recorded_keys != keys or next_index >= len(keys):
                return "invalid"
            return "ready" if keys[next_index] == current_key else "invalid"

    def consume_explicit_delegation(
        self,
        *,
        quote_identity: str,
        batch_id: str,
        operation_keys: Sequence[str],
        current_operation_key: str,
        approve_new: bool = False,
    ) -> int:
        """Mint or consume one ordered, argument-bound delegation position."""

        keys = tuple(str(item or "").strip() for item in operation_keys)
        quote_key = str(quote_identity or "").strip()
        normalized_batch_id = str(batch_id or "").strip()
        current_key = str(current_operation_key or "").strip()
        if not quote_key or not normalized_batch_id or not keys or any(not key for key in keys):
            return 0
        with self._state_lock:
            record = self._explicit_delegation_batches.get(normalized_batch_id)
            if record is None:
                if (
                    not approve_new
                    or quote_key in self._explicit_delegation_quotes
                    or current_key != keys[0]
                ):
                    return 0
                self._explicit_delegation_quotes.add(quote_key)
                record = (keys, 0)
            recorded_keys, next_index = record
            if (
                recorded_keys != keys
                or next_index >= len(keys)
                or recorded_keys[next_index] != current_key
            ):
                return 0
            position = next_index + 1
            self._explicit_delegation_batches[normalized_batch_id] = (
                recorded_keys,
                position,
            )
            return position

    @property
    def initial_root_value(self) -> Any:
        return deepcopy(self._initial_root_value)

    @property
    def session_driver(self) -> Any:
        """Return the optional generic coordinator contributed by a Plugin pack."""

        return self._session_driver

    @property
    def plugin_context_data(self) -> dict[str, Any]:
        """Return the host data included in every Plugin invocation."""

        return dict(self._plugin_context_data)

    @property
    def plugin_services(self) -> dict[str, Any]:
        """Return host-owned services inherited by child Agent sessions."""

        return {
            name: service
            for name, service in self._plugin_service_values.items()
            if name != "permission"
        }

    def application_plugin_services(self) -> dict[str, Any]:
        """Return reconciled services after enforcing required session packs."""

        return self._plugin_services()

    @property
    def application_scope(self) -> ApplicationPluginScope | None:
        """Return the explicit process scope shared with child sessions."""

        return self._application_scope

    async def build_session_context(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        """Return the conversation's stable, cached SessionStart context."""

        mounts = await self.build_session_mounts(details)
        return "\n\n".join(str(mount["content"]) for mount in mounts)

    async def build_session_mounts(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Build SessionStart once and durably reuse its byte-stable mounts."""

        self.reconcile_plugins()
        self._ensure_required_session_packs()
        await asyncio.to_thread(self._session_start_build_lock.acquire)
        try:
            fingerprint = await self._session_start_fingerprint(details)
            cached = self._cached_session_start_mounts(fingerprint)
            if cached is not None:
                return tuple(cached)
            contributions = await self.hooks.session_start_mounts(dict(details or {}))
            mounts = self._contribution_mounts(
                contributions,
                system_kind="system_prompt",
                ordinary_kind="plugin_session",
                system_source="SessionStart",
                ordinary_source="SessionStart",
                lifecycle="session",
            )
            with self._linearized_context_commit():
                root = self.store.get_node(self.tree.id, self.tree.root_id)
                state = {
                    "session_start_complete": True,
                    "fingerprint": fingerprint,
                    "session_start_mounts": deepcopy(mounts),
                }
                root_value = with_plugin_session_state(
                    root.value,
                    _AGENT_LIFECYCLE_STATE_ID,
                    state,
                )
                self.store.update_node(self.tree.id, root.id, root_value)
            return tuple(mounts)
        finally:
            self._session_start_build_lock.release()

    async def build_turn_mounts(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Build the dynamic context suffix for one user turn."""

        self.reconcile_plugins()
        self._ensure_required_session_packs()
        contributions = await self.hooks.turn_start_mounts(dict(details or {}))
        return tuple(self._contribution_mounts(
            contributions,
            system_kind="turn_system_prompt",
            ordinary_kind="turn_context",
            system_source="TurnStart",
            ordinary_source="TurnStart",
            lifecycle="turn",
        ))

    async def build_model_context(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        """Return stable SessionStart followed by this call's TurnStart suffix."""

        mounts = await self.build_model_mounts(details)
        return "\n\n".join(
            mount["content"] for mount in mounts if mount["content"]
        )

    async def build_model_mounts(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Return stable mounts first and dynamic mounts second."""

        stable = await self.build_session_mounts(details)
        dynamic = await self.build_turn_mounts(details)
        return tuple(self._unique_context_mounts([*stable, *dynamic]))

    def _cached_session_start_mounts(
        self,
        fingerprint: str | None = None,
    ) -> list[dict[str, str]] | None:
        root = self.store.get_node(self.tree.id, self.tree.root_id)
        state = plugin_session_state(root.value, _AGENT_LIFECYCLE_STATE_ID)
        if state.get("session_start_complete") is not True:
            return None
        if fingerprint is not None and str(state.get("fingerprint") or "") != fingerprint:
            return None
        return self._stored_context_mounts(state.get("session_start_mounts"))

    async def _session_start_fingerprint(
        self,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        """Hash only explicit inputs that can alter the stable prompt prefix."""

        hooks = self.hooks.list(SESSION_START)
        hook_ids = {hook.id for hook in hooks}
        pack_versions = []
        for pack_id, attachment in sorted(self._plugin_pack_attachments.items()):
            if not hook_ids.intersection(attachment.hooks):
                continue
            pack_versions.append({
                "id": pack_id,
                "source": attachment.source,
                "setup": attachment.setup_fingerprint,
                "version": attachment.pack.metadata.get("version"),
            })

        hook_dependencies = await self.hooks.session_start_fingerprints(details)

        payload = {
            "schema": 1,
            "hooks": [
                {
                    "id": hook.id,
                    "plugin_id": hook.plugin_id,
                    "root_only": hook.root_only,
                    "matcher": hook.matcher,
                    "failure_policy": hook.failure_policy,
                    "config": dict(hook.config),
                    "enabled": hook.enabled,
                }
                for hook in hooks
            ],
            "packs": pack_versions,
            "hook_dependencies": hook_dependencies,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _stored_context_mounts(raw_mounts: Any) -> list[dict[str, str]]:
        if not isinstance(raw_mounts, list):
            return []
        return [
            {
                "kind": str(item.get("kind") or "context"),
                "content": str(item.get("content") or "").strip(),
                "source": str(item.get("source") or "context_tree"),
                "lifecycle": str(item.get("lifecycle") or ""),
            }
            for item in raw_mounts
            if isinstance(item, Mapping)
            and str(item.get("content") or "").strip()
        ]

    @staticmethod
    def _unique_context_mounts(
        mounts: tuple[dict[str, str], ...] | list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Keep later turn mounts from shadowing an earlier stable kind."""

        result: list[dict[str, str]] = []
        used_names: set[str] = set()
        next_suffix: dict[str, int] = {}
        for raw in mounts:
            mount = dict(raw)
            base = str(mount.get("kind") or "context")
            kind = base
            if kind in used_names:
                suffix = max(2, next_suffix.get(base, 2))
                while f"{base}.{suffix}" in used_names:
                    suffix += 1
                kind = f"{base}.{suffix}"
                next_suffix[base] = suffix + 1
            used_names.add(kind)
            mount["kind"] = kind
            result.append(mount)
        return result

    @staticmethod
    def _contribution_mounts(
        contributions: tuple[dict[str, str], ...],
        *,
        system_kind: str,
        ordinary_kind: str,
        system_source: str,
        ordinary_source: str,
        lifecycle: str,
    ) -> list[dict[str, str]]:
        mounts: list[dict[str, str]] = []
        used_kinds: dict[str, int] = {}
        for item in contributions:
            content = str(item.get("context") or "").strip()
            if not content:
                continue
            is_system = str(item.get("position") or "") == "system"
            base_kind = str(item.get("context_kind") or "").strip() or (
                system_kind if is_system else ordinary_kind
            )
            occurrence = used_kinds.get(base_kind, 0) + 1
            used_kinds[base_kind] = occurrence
            kind = base_kind if occurrence == 1 else f"{base_kind}.{occurrence}"
            mounts.append({
                "kind": kind,
                "content": content,
                "source": str(item.get("context_source") or "").strip()
                or (system_source if is_system else ordinary_source),
                "lifecycle": lifecycle,
            })
        return mounts

    def _plugin_data(self, *, run_id: str = "", **details: Any) -> dict[str, Any]:
        data = dict(self._plugin_context_data)
        raw_run_context = data.get("run_context")
        inherited_language = (
            raw_run_context.get("language")
            if isinstance(raw_run_context, Mapping)
            else ""
        )
        data["language"] = (
            normalize_language(data.get("language") or inherited_language)
            or system_language()
        )
        caller = "main_agent"
        if self.agent_id != "main":
            caller = f"subagent_{self.agent_id}"
            data["agent_id"] = self.agent_id
            data["parent_agent_id"] = self.parent_agent_id
            data["caller"] = caller
        if run_id:
            data["run_id"] = run_id
        data["permission_user_request"] = self.permission_user_request
        if isinstance(raw_run_context, Mapping):
            run_context = dict(raw_run_context)
            run_context["agent_id"] = self.agent_id
            run_context["caller"] = caller
            if run_id:
                run_context["round_id"] = run_id
            run_context["language"] = data["language"]
            run_context["permission_user_request"] = self.permission_user_request
            data["run_context"] = run_context
        data.update(details)
        return data

    def _has_context_provider(self) -> bool:
        return bool(
            self.hooks.list(SESSION_START)
            or self.hooks.list(TURN_START)
            or self._cached_session_start_mounts() is not None
        )

    def _model_stream_sink(
        self,
        *,
        run_id: str,
        trigger_id: str,
        transition_key: str,
    ) -> Callable[[Mapping[str, Any]], Any]:
        """Translate Provider stream chunks into transient Agent events."""

        event_types = {
            "reply_start": "assistant.stream.started",
            "reply_delta": "assistant.stream.delta",
            "reply_done": "assistant.stream.done",
            "reasoning_start": "assistant.reasoning.started",
            "reasoning_delta": "assistant.reasoning.delta",
            "reasoning_done": "assistant.reasoning.done",
        }

        async def publish(event: Mapping[str, Any]) -> None:
            source_type = str(event.get("type") or "")
            event_type = event_types.get(source_type)
            if event_type is None or self._is_cancelled(run_id):
                return
            if source_type.startswith("reply_"):
                with self._state_lock:
                    self._streamed_transition_keys.add(transition_key)
            self._emit_event(
                event_type,
                run_id=run_id,
                node_id=trigger_id,
                data={
                    key: deepcopy(value)
                    for key, value in event.items()
                    if key != "type"
                },
            )

        return publish

    def _attach_plugin_packs(self) -> None:
        """Attach the initially available session contributions."""

        self.reconcile_plugins(force=True)

    def _application_host(self) -> Any | None:
        host = self._application_scope
        if host is None:
            return None
        if Path(host.plugin_directory).resolve() != self.plugin_directory:
            return None
        return host

    def _capture_application_host_services(self) -> None:
        host = self._application_host()
        if host is None:
            return
        for name, value in host.services.items():
            if self._plugin_service_values.get(name) is value:
                self._host_service_names.add(name)
        self._authoritative_directory_revision = host.registry.directory_revision
        self._authoritative_customization_revision = host.registry.customizations.revision

    def _sync_application_host_services(self, host: Any | None) -> None:
        if host is None:
            return
        active = host.active_services
        owned = {
            name
            for attachment in self._plugin_pack_attachments.values()
            for name in attachment.provided_services
        }
        names = self._host_service_names | set(active)
        self._host_service_names.update(active)
        for name in names:
            if name in owned:
                continue
            if name in active:
                self._plugin_service_values[name] = active[name]
            else:
                self._plugin_service_values.pop(name, None)

    def _failed_pack_sources(self, host: Any | None) -> set[str]:
        failures = list(self._plugin_load_failures)
        if host is not None:
            failures.extend(host.load_failures)
        return {str(item.path.resolve()) for item in failures}

    def _application_pack_state_token(self, host: Any | None) -> tuple[Any, ...]:
        """Include process lifecycle state in lazy session reconciliation."""

        if host is None:
            return ("application_host", "unavailable")
        values = []
        for pack in self.registry.list_packs():
            if not pack.has_application_contributions:
                continue
            values.append(
                (
                    pack.id,
                    host.pack_operational(pack.id),
                    host.pack_restart_required(pack.id),
                    host.startup_failures.get(pack.id, ""),
                )
            )
        return tuple(values)

    @staticmethod
    def _application_pack_error(pack: PluginPack, host: Any | None) -> str:
        if not pack.has_application_contributions:
            return ""
        if host is None:
            # A session can be embedded without Cyrene's process-level
            # application host (for example in a worker, test host, or a
            # standalone Agent integration).  ``application_setup`` is an
            # additional surface; it must not prevent the pack's
            # session-scoped ``setup`` from wiring Hooks and services there.
            # When a host exists its lifecycle state is authoritative and is
            # still enforced below.
            return ""
        if host.pack_restart_required(pack.id):
            return "application contribution changed and requires restart"
        startup_error = host.startup_failures.get(pack.id, "")
        if startup_error:
            return f"application startup failed: {startup_error}"
        if not host.pack_operational(pack.id):
            return "application contribution is not operational"
        return ""

    def _remember_required_session_packs(self, host: Any | None) -> None:
        for pack in self.registry.list_packs():
            if pack.has_session_contributions and bool(pack.metadata.get("required")):
                self._required_session_pack_ids.add(pack.id)
        failures = list(self._plugin_load_failures)
        if host is not None:
            failures.extend(host.load_failures)

    def _required_session_pack_error(self, host: Any | None = None) -> str:
        missing = sorted(
            pack_id
            for pack_id in self._required_session_pack_ids
            if pack_id not in self._plugin_pack_attachments
        )
        if not missing:
            return ""
        failures = list(self._plugin_load_failures)
        if host is not None:
            failures.extend(host.load_failures)
        load_errors = {
            failure.path.name: str(failure.error or "load failed")
            for failure in failures
        }
        details = []
        for pack_id in missing:
            reason = self._plugin_setup_failures.get(pack_id)
            if not reason:
                reason = load_errors.get(pack_id, "setup is not attached")
            details.append(f"{pack_id} ({reason})")
        return ", ".join(details)

    def _ensure_required_session_packs(self) -> None:
        error = self._required_session_pack_error(self._application_host())
        if error:
            raise RuntimeError(
                "Required Plugin session setup unavailable: " + error
            )

    @staticmethod
    def _pack_setup_fingerprint(pack: PluginPack, source: str) -> tuple[Any, ...]:
        """Keep no-op directory refreshes from restarting session services."""

        path = Path(source)
        try:
            if path.is_dir():
                files = tuple(sorted(path.rglob("*.py")))
            elif path.is_file():
                files = (path,)
            else:
                files = ()
            if files:
                return (
                    "files",
                    tuple(
                        (
                            str(item.relative_to(path) if path.is_dir() else item.name),
                            item.stat().st_mtime_ns,
                            item.stat().st_size,
                        )
                        for item in files
                    ),
                )
        except OSError:
            pass
        return ("callable", tuple(id(setup) for setup in pack.session_setups))

    def _detach_session_pack(self, pack_id: str, *, reason: str) -> None:
        attachment = self._plugin_pack_attachments.pop(pack_id, None)
        if attachment is None:
            return
        for hook_id in attachment.hooks:
            self.hooks.unregister(hook_id)
        for name, provided in attachment.provided_services.items():
            if self._plugin_service_values.get(name) is not provided:
                continue
            existed, previous = attachment.previous_services[name]
            if existed:
                self._plugin_service_values[name] = previous
            else:
                self._plugin_service_values.pop(name, None)
        driver = attachment.driver
        if driver is not None:
            request_cancel = getattr(driver, "request_cancel_all", None)
            if callable(request_cancel):
                try:
                    request_cancel(reason)
                except Exception:
                    logger.exception("Failed to cancel session driver for %s", pack_id)
            close = getattr(driver, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.exception("Failed to close session driver for %s", pack_id)
            if self._session_driver is driver:
                self._session_driver = None
                self._owns_session_driver = False

    def _attach_session_pack(self, pack: PluginPack, source: str) -> None:
        if "agent_session" in self._plugin_service_values:
            raise ValueError("Plugin service name is reserved: agent_session")
        before = dict(self._plugin_service_values)
        tracker = _SetupHookTracker(self.hooks)
        driver: Any = None
        self._plugin_service_values["agent_session"] = self
        context = PluginSetupContext(
            data_directory=self.data_directory,
            plugin_directory=self.plugin_directory,
            workspace=self.workspace,
            tree=self.store,
            tree_id=self.tree.id,
            root_id=self.tree.root_id,
            hooks=tracker,
            data=self._plugin_data(),
            services=self._plugin_service_values,
            agent_id=self.agent_id,
            parent_agent_id=self.parent_agent_id,
        )
        try:
            for setup in pack.session_setups:
                setup(context)
            driver = self._plugin_service_values.pop("session_driver", None)
            changed = {
                name: value
                for name, value in self._plugin_service_values.items()
                if name != "agent_session" and before.get(name) is not value
            }
            previous = {
                name: (name in before, before.get(name)) for name in changed
            }
            attachment = _SessionPackAttachment(
                pack=pack,
                source=source,
                setup_fingerprint=self._pack_setup_fingerprint(pack, source),
                hooks=set(tracker.touched),
                previous_services=previous,
                provided_services=changed,
                driver=driver,
            )
            if driver is not None:
                if self._session_driver is not None:
                    raise ValueError("Plugin session_driver service already exists")
                self._session_driver = driver
                self._owns_session_driver = True
                attach = getattr(driver, "attach", None)
                if callable(attach) and self._transition_thread.is_alive():
                    attach()
            self._plugin_pack_attachments[pack.id] = attachment
        except Exception as exc:
            self._plugin_setup_failures[pack.id] = str(exc)
            self._plugin_service_values.pop("agent_session", None)
            attachment = self._plugin_pack_attachments.get(pack.id)
            if attachment is not None:
                self._detach_session_pack(pack.id, reason="plugin_setup_failed")
            else:
                if driver is not None and self._session_driver is driver:
                    self._session_driver = None
                    self._owns_session_driver = False
                    close = getattr(driver, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            logger.exception(
                                "Failed to close setup driver for %s", pack.id
                            )
                tracker.rollback()
                for name in tuple(self._plugin_service_values):
                    if name not in before:
                        self._plugin_service_values.pop(name, None)
                self._plugin_service_values.update(before)
            logger.exception(
                "Failed to attach Plugin pack %s to Agent session %s",
                pack.id,
                self.tree.id,
            )
        else:
            self._plugin_setup_failures.pop(pack.id, None)
            self._plugin_service_values.pop("agent_session", None)

    def reconcile_plugins(self, *, force: bool = False) -> None:
        """Synchronize live setup Hooks/services with shared Plugin state."""

        with self._plugin_reconcile_lock:
            if self._closed:
                return
            host = self._application_host()
            host_token = host.registry.sync_token if host is not None else None
            failure_token = tuple(
                sorted(self._failed_pack_sources(host))
            )
            application_token = self._application_pack_state_token(host)
            token = (
                self.registry.sync_token,
                host_token,
                failure_token,
                application_token,
            )
            if not force and token == self._plugin_sync_token:
                return

            if host is not None:
                authoritative_directory = host.registry.directory_revision
                if (
                    host.registry is not self.registry
                    and self._authoritative_directory_revision is not None
                    and authoritative_directory != self._authoritative_directory_revision
                ):
                    self._plugin_load_failures = tuple(
                        self.registry.refresh_directory(self.plugin_directory)
                    )
                self._authoritative_directory_revision = authoritative_directory
                customization_revision = host.registry.customizations.revision
            else:
                customization_revision = self.registry.customizations.revision
            if customization_revision != self._authoritative_customization_revision:
                self.registry.refresh_customizations()
                self._authoritative_customization_revision = customization_revision

            self._remember_required_session_packs(host)
            failed_sources = self._failed_pack_sources(host)
            desired: dict[str, tuple[PluginPack, str]] = {}
            for pack in self.registry.list_packs():
                if not pack.has_session_contributions:
                    continue
                try:
                    source = self.registry.pack_source(pack.id)
                    enabled = self.registry.pack_enabled(pack.id)
                except Exception:
                    continue
                application_error = self._application_pack_error(pack, host)
                if application_error:
                    self._plugin_setup_failures[pack.id] = application_error
                if (
                    enabled
                    and not application_error
                    and str(Path(source).resolve()) not in failed_sources
                ):
                    desired[pack.id] = (pack, source)

            for pack_id, attachment in tuple(self._plugin_pack_attachments.items()):
                next_value = desired.get(pack_id)
                if next_value is None or (
                    attachment.source != next_value[1]
                    or attachment.setup_fingerprint
                    != self._pack_setup_fingerprint(*next_value)
                ):
                    self._detach_session_pack(
                        pack_id,
                        reason="plugin_disabled_or_reloaded",
                    )

            self._sync_application_host_services(host)
            for pack_id, (pack, source) in desired.items():
                if pack_id not in self._plugin_pack_attachments:
                    self._attach_session_pack(pack, source)

            self._plugin_sync_token = (
                self.registry.sync_token,
                host.registry.sync_token if host is not None else None,
                tuple(sorted(self._failed_pack_sources(host))),
                self._application_pack_state_token(host),
            )

    def _plugin_services(self) -> dict[str, Any]:
        self.reconcile_plugins()
        self._ensure_required_session_packs()
        return dict(self._plugin_service_values)

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
            "cyrene.core.session",
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
                "cyrene.core.session",
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
            "cyrene.core.session",
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
                    "cyrene.core.session",
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
            "cyrene.core.session",
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
            event_type = (
                "guidance.applied"
                if value.get("runtime_guidance") is True
                else "input.accepted"
            )
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
            value = node.value if isinstance(node.value, Mapping) else {}
            stored_reviews = value.get("permission_reviews")
            reviews = stored_reviews if isinstance(stored_reviews, list) else ()
            for raw_review in reviews:
                if not isinstance(raw_review, Mapping):
                    continue
                review = deepcopy(dict(raw_review))
                raw_time = str(review.get("created_at") or "")
                try:
                    review_time = datetime.fromisoformat(raw_time)
                except ValueError:
                    review_time = node.updated_at
                if review_time.tzinfo is None:
                    review_time = review_time.replace(tzinfo=timezone.utc)
                events.append(AgentSessionEvent(
                    sequence=len(events) + 1,
                    type="permission.reviewed",
                    tree_id=node.tree_id,
                    run_id=str(value.get("run_id") or ""),
                    node_id=node.id,
                    time=review_time,
                    data=review,
                ))
        result = tuple(event for event in events if event.sequence > int(after_sequence))
        log_operation(
            logger,
            "cyrene.core.session",
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
        authorizations: list[str] = []
        nodes = sorted(
            self.store.get_subtree(self.tree.id, self.tree.root_id),
            key=lambda item: (item.created_at, item.id),
        )
        for node in nodes:
            value = node.value if isinstance(node.value, Mapping) else {}
            if (
                value.get("role") not in {"user", "context_reflection"}
                or str(value.get("run_id") or "") != target_run_id
            ):
                continue
            authorization = str(value.get("authorization_request") or "")
            if authorization and authorization not in authorizations:
                authorizations.append(authorization)
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if str(metadata.get("source") or "") != "agent_inbox" and not fallback:
                fallback = str(value.get("content") or "")
        return "\n\n".join(authorizations) or fallback

    def _append_clarification_authorization(
        self,
        run_id: str,
        answer: str,
    ) -> str:
        """Persist original request + user-authored clarification for one run."""

        normalized_answer = str(answer or "").strip()
        current = self._permission_request_for_run(run_id).strip()
        if not normalized_answer:
            return current
        marker = f"用户随后澄清：{normalized_answer}"
        updated = current if marker in current else (
            f"{current}\n\n{marker}" if current else normalized_answer
        )
        nodes = sorted(
            self.store.get_subtree(self.tree.id, self.tree.root_id),
            key=lambda item: (item.created_at, item.id),
        )
        for user_node in nodes:
            value = user_node.value if isinstance(user_node.value, Mapping) else {}
            if (
                value.get("role") != "user"
                or str(value.get("run_id") or "") != str(run_id or "")
            ):
                continue
            stored = dict(value)
            stored["authorization_request"] = updated
            self.store.update_node(self.tree.id, user_node.id, stored)
            break
        with self._state_lock:
            self._run_permission_user_request = updated
        return updated

    def _guidance_service(self) -> Any | None:
        service = self._plugin_services().get("guidance")
        if service is None or not bool(getattr(service, "enabled", False)):
            return None
        return service

    async def _collect_guidance(self, *, terminal: bool = False) -> list[dict[str, Any]]:
        service = self._guidance_service()
        if service is None:
            return []
        collect = service.collect_or_seal if terminal else service.collect
        return list(await collect())

    async def _mount_guidance(
        self,
        parent: ContextNode,
        events: list[dict[str, Any]],
        *,
        run_id: str,
    ) -> ContextNode | None:
        """Durably splice accepted guidance into the active Context branch."""

        if not events:
            return None
        service = self._guidance_service()
        if service is None:
            return None
        event_ids = [str(event.get("event_id") or "") for event in events]
        node_key = ":".join(event_ids) or json.dumps(
            events, ensure_ascii=False, sort_keys=True, default=str
        )
        node_id = self._stable_id("guidance", f"{run_id}:{node_key}")
        already_mounted = False
        try:
            node_value = dict(service.node_value(events, run_id=run_id))
            node_value["caused_by"] = node_key
            with self._linearized_context_commit():
                if self._closed or run_id in self._cancelled_run_ids:
                    service.requeue(events)
                    return None
                try:
                    guidance = self.store.get_node(self.tree.id, node_id)
                except NodeNotFoundError:
                    guidance = self.store.mount(
                        self.tree.id,
                        parent.id,
                        node_value,
                        node_id=node_id,
                    )
                else:
                    parent_path = self.store.get_path(self.tree.id, parent.id)
                    already_mounted = guidance.id in {
                        path_node.id for path_node in parent_path
                    }
                    if not already_mounted:
                        raise RuntimeError(
                            "Recovered guidance belongs to a different Context branch"
                        )
                if already_mounted:
                    guidance_state = None
                else:
                    metadata = (
                        node_value.get("metadata")
                        if isinstance(node_value.get("metadata"), Mapping)
                        else {}
                    )
                    raw_guidance = str(metadata.get("raw_guidance") or "").strip()
                    if raw_guidance and raw_guidance not in self._current_user_request:
                        self._current_user_request = "\n\n".join(
                            part
                            for part in (self._current_user_request, raw_guidance)
                            if part
                        )
                    authorization = str(
                        node_value.get("authorization_request") or ""
                    ).strip()
                    if (
                        authorization
                        and authorization not in self._run_permission_user_request
                    ):
                        self._run_permission_user_request = "\n\n".join(
                            part
                            for part in (
                                self._run_permission_user_request,
                                authorization,
                            )
                            if part
                        )
                    guidance_state = self._set_state_locked(
                        "queued",
                        _l("Applying user guidance", "正在应用用户引导"),
                        leaf_id=guidance.id,
                    )
            if guidance_state is not None:
                self._emit_state_snapshot(guidance_state)
        except Exception:
            service.requeue(events)
            raise

        try:
            await service.acknowledge(events)
        except Exception:
            logger.exception("Failed to acknowledge mounted runtime guidance")
            service.requeue(events)
        try:
            await service.fan_out(events)
        except Exception:
            logger.exception("Failed to fan runtime guidance out to child Agents")
        return None if already_mounted else guidance

    def _mark_assistant_intermediate(self, assistant: ContextNode) -> ContextNode:
        with self._linearized_context_commit():
            current = self.store.get_node(self.tree.id, assistant.id)
            value = dict(current.value) if isinstance(current.value, Mapping) else {}
            if value.get("intermediate") is not True:
                value["intermediate"] = True
                value.pop("session_end_complete", None)
                value.pop("session_end_status", None)
                current = self.store.update_node(self.tree.id, current.id, value)
            return current

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

    def _turn_user_context(
        self, run_id: str
    ) -> tuple[Mapping[str, Any], ContextNode | None]:
        try:
            turn_path = self.store.get_path(self.tree.id, self._leaf_id)
        except Exception:
            turn_path = []
        turn_user = next(
            (
                node
                for node in reversed(turn_path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "user"
                and node.value.get("runtime_guidance") is not True
                and str(node.value.get("run_id") or "") == str(run_id)
            ),
            None,
        )
        if (
            turn_user is not None
            and isinstance(turn_user.value, Mapping)
            and isinstance(turn_user.value.get("metadata"), Mapping)
        ):
            return turn_user.value["metadata"], turn_user
        return {}, turn_user

    def _pending_question_from_results(
        self,
        calls: list[Any],
        results: tuple[PluginCallResult, ...],
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        turn_metadata, turn_user = self._turn_user_context(run_id)
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
                "retry": turn_metadata.get("retry") is True,
                "turn_id": str(turn_metadata.get("turn_id") or ""),
                "original_user_message": str(
                    turn_metadata.get("public_user_message")
                    or (
                        turn_user.value.get("content")
                        if turn_user is not None
                        and isinstance(turn_user.value, Mapping)
                        else ""
                    )
                    or ""
                ),
            }
            plan = payload.get("plan")
            if isinstance(plan, (Mapping, list)):
                question["plan"] = self._json_value(plan)
            permission = payload.get("permission")
            if isinstance(permission, Mapping):
                question["permission"] = self._json_value(dict(permission))
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
            if (
                value.get("role") in {"tool_results", "context_reflection"}
                and value.get("caused_by") == batch_key
            ):
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
                    "cyrene.core.session",
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
            "cyrene.core.session",
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
            "cyrene.core.session",
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
                                "cyrene.core.session",
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
                        "cyrene.core.session",
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
                        "cyrene.core.session",
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
                        "cyrene.core.session",
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
                        _l(
                            "The Agent transition failed.",
                            "Agent 状态转换失败。",
                        ),
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
                                    _l("Cancelled", "已取消"),
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
                "cyrene.core.session",
                "transition_worker",
                phase="stopped",
                tree_id=self._tree_id_hint,
                thread=threading.current_thread().name,
            )

    def _wait_for_transitions(self) -> None:
        with self._transition_condition:
            while self._transition_pending:
                self._transition_condition.wait()

    def _select_restore_leaf(self, nodes: Sequence[ContextNode]) -> ContextNode:
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
                "context_reflection",
                "assistant",
                "tool_results",
            }
        ]
        leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
        committed_leaf_id, _committed_run_id = self.store.committed_state(
            self.tree.id
        )
        committed_leaf = next(
            (node for node in dialogue if node.id == committed_leaf_id),
            None,
        )
        latest_value = leaf.value if isinstance(leaf.value, Mapping) else {}
        latest_path = self.store.get_path(self.tree.id, leaf.id)
        latest_run_id = self._node_run_id(leaf)
        latest_run_user = next(
            (
                node
                for node in reversed(latest_path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "user"
                and node.value.get("runtime_guidance") is not True
                and self._node_run_id(node) == latest_run_id
            ),
            None,
        )
        latest_user_metadata = (
            latest_run_user.value.get("metadata")
            if latest_run_user is not None
            and isinstance(latest_run_user.value, Mapping)
            and isinstance(latest_run_user.value.get("metadata"), Mapping)
            else {}
        )
        latest_is_retry = latest_user_metadata.get("retry") is True
        latest_is_terminal = bool(
            latest_value.get("cancelled") is True
            or latest_value.get("error") is True
            or self._pending_from_node(leaf) is not None
            or (
                latest_value.get("role") == "assistant"
                and latest_value.get("session_end_complete") is True
            )
        )
        if (
            committed_leaf is not None
            and committed_leaf.id != leaf.id
            and latest_is_retry
            and latest_is_terminal
        ):
            # A retry branch only becomes authoritative after Workbench saves
            # its public projection. A failed save/cancel therefore restores
            # the previously committed answer; the sibling remains for audit.
            return committed_leaf
        return leaf

    def _restore_run_context(self, leaf: ContextNode) -> None:
        path = self.store.get_path(self.tree.id, leaf.id)
        latest_user = next(
            (
                node
                for node in reversed(path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "user"
                and node.value.get("runtime_guidance") is not True
            ),
            None,
        )
        self._current_run_id = self._node_run_id(leaf)
        request_parts: list[str] = []
        for request_node in path:
            request_value = (
                request_node.value
                if isinstance(request_node.value, Mapping)
                else {}
            )
            if (
                request_value.get("role") != "user"
                or str(request_value.get("run_id") or "") != self._current_run_id
            ):
                continue
            if request_value.get("runtime_guidance") is True:
                request_metadata = request_value.get("metadata")
                request_metadata = (
                    request_metadata
                    if isinstance(request_metadata, Mapping)
                    else {}
                )
                request_text = str(request_metadata.get("raw_guidance") or "")
            else:
                request_text = str(request_value.get("content") or "")
            if request_text and request_text not in request_parts:
                request_parts.append(request_text)
        self._current_user_request = "\n\n".join(request_parts)
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
        if self._current_run_id:
            for node in path:
                node_value = node.value if isinstance(node.value, Mapping) else {}
                if (
                    node_value.get("role") != "tool_results"
                    or str(node_value.get("run_id") or "") != self._current_run_id
                ):
                    continue
                for raw_result in node_value.get("results") or ():
                    if not isinstance(raw_result, Mapping):
                        continue
                    raw_failure = raw_result.get("failure")
                    if isinstance(raw_failure, Mapping):
                        self.runtime.restore_circuit(
                            str(raw_result.get("name") or ""),
                            self._current_run_id,
                            raw_failure,
                            agent_id=self.agent_id,
                        )

    def _restore(self) -> None:
        log_operation(
            logger,
            "cyrene.core.session",
            "restore",
            phase="started",
            tree_id=self.tree.id,
            root_id=self.tree.root_id,
        )
        nodes = self.store.get_subtree(self.tree.id, self.tree.root_id)
        root_value = next(
            (
                node.value
                for node in nodes
                if node.id == self.tree.root_id and isinstance(node.value, Mapping)
            ),
            {},
        )
        self._permission_session_grants = {
            str(item).strip()
            for item in root_value.get("permission_session_grants") or ()
            if str(item).strip()
        }
        self._cancelled_run_ids = {
            str(node.value.get("run_id") or "")
            for node in nodes
            if isinstance(node.value, Mapping)
            and node.value.get("cancelled") is True
            and node.value.get("run_id")
        }
        leaf = self._select_restore_leaf(nodes)
        self._leaf_id = leaf.id
        self._restore_run_context(leaf)
        value = leaf.value if isinstance(leaf.value, Mapping) else {}
        if value.get("cancelled") is True:
            self._current_user_request = ""
            self._set_state(
                "idle", _l("Restored cancelled run", "已恢复取消的运行"), leaf_id=leaf.id
            )
            log_operation(
                logger,
                "cyrene.core.session",
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
                str(pending.get("text") or _l("Waiting for user answer", "正在等待用户答复")),
                leaf_id=leaf.id,
            )
            log_operation(
                logger,
                "cyrene.core.session",
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
        if value.get("role") in {"context_compaction", "context_reflection"}:
            if value.get("role") == "context_reflection":
                model_context = value.get("model_context")
                model_context = (
                    model_context if isinstance(model_context, Mapping) else {}
                )
                reflection = model_context.get("reflection")
                reflection = reflection if isinstance(reflection, Mapping) else {}
                self._current_user_request = str(reflection.get("goal") or "")
            should_resume = value.get("resume_model") is True
            if should_resume and self._transition_assistant(leaf) is None:
                self._set_state(
                    "queued",
                    _l(
                        "Resuming model after context rewrite",
                        "正在重写上下文后恢复模型",
                    ),
                    leaf_id=leaf.id,
                )
                self._enqueue_transition("advance", leaf)
                outcome = "resume_compacted_model"
            else:
                self._current_user_request = ""
                self._set_state(
                    "idle",
                    _l("Restored rewritten context", "已恢复重写后的上下文"),
                    leaf_id=leaf.id,
                )
                outcome = "compacted_idle"
            log_operation(
                logger,
                "cyrene.core.session",
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
                self._set_state(
                    "queued", _l("Resuming tool batch", "正在恢复工具批次"), leaf_id=leaf.id
                )
                self._enqueue_transition("tools", leaf)
                log_operation(
                    logger,
                    "cyrene.core.session",
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
                _l("Resuming context mount", "正在恢复上下文挂载"),
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
                "cyrene.core.session",
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
            has_context_provider = self._has_context_provider()
            if has_context_provider:
                self._set_state(
                    "queued",
                    _l("Resuming context mount", "正在恢复上下文挂载"),
                    leaf_id=leaf.id,
                )
                self.store.update_node(self.tree.id, leaf.id, dict(value))
                log_operation(
                    logger,
                    "cyrene.core.session",
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
                _l("Resuming SessionEnd hooks", "正在恢复 SessionEnd Hook"),
                leaf_id=leaf.id,
            )
            self._enqueue_transition("finish", leaf)
            log_operation(
                logger,
                "cyrene.core.session",
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
            self._set_state(
                "queued",
                _l("Resuming model transition", "正在恢复模型状态转换"),
                leaf_id=leaf.id,
            )
            self._enqueue_transition("advance", leaf)
            log_operation(
                logger,
                "cyrene.core.session",
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
        self._set_state("idle", _l("Restored", "已恢复"), leaf_id=leaf.id)
        if value.get("role") == "assistant":
            self._current_user_request = ""
        log_operation(
            logger,
            "cyrene.core.session",
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
        self.reconcile_plugins()
        self._ensure_required_session_packs()
        content = str(text or "").strip()
        if not content:
            raise ValueError(_l("Message cannot be empty.", "消息不能为空。"))
        normalized_run_id = str(run_id or f"run_{uuid4().hex}").strip()
        if not normalized_run_id:
            raise ValueError(_l("run_id cannot be empty.", "run_id 不能为空。"))
        normalized_metadata = deepcopy(dict(metadata or {}))
        public_request = str(
            normalized_metadata.get("public_user_message") or content
        ).strip()
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
                else self._permission_request_for_run(normalized_run_id) or public_request
            )
        has_session_context = self._has_context_provider()
        with self._state_lock:
            if self._closed:
                raise RuntimeError(_l("The Agent session is closed.", "Agent 会话已关闭。"))
            if self._status != "idle":
                raise RuntimeError(_l(
                    "The Agent is still processing the previous message.",
                    "Agent 仍在处理上一条消息。",
                ))
            parent_id = self._leaf_id
            self._status = "queued"
            self._detail = _l("User context mounted", "用户上下文已挂载")
            self._current_user_request = content
            self._current_run_id = normalized_run_id
            self._run_permission_user_request = authorization_request
            self._model_calls = 0
        log_operation(
            logger,
            "cyrene.core.session",
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
                "cyrene.core.session",
                "submit",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                tree_id=self.tree.id,
                run_id=normalized_run_id,
                parent_id=parent_id,
                error=exc,
            )
            self._set_state("idle", _l("Mount failed", "挂载失败"))
            raise
        self._set_state(
            "queued",
            _l("Waiting for ContextChange hook", "正在等待 ContextChange Hook"),
            leaf_id=node.id,
        )
        log_operation(
            logger,
            "cyrene.core.session",
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

        self.reconcile_plugins()
        normalized_question_id = str(question_id or "").strip()
        normalized_answer = str(answer or "").strip()
        if not normalized_question_id or not normalized_answer:
            raise ValueError(_l(
                "question_id and answer are required.",
                "必须提供 question_id 和 answer。",
            ))
        with self._linearized_context_commit():
            if self._closed:
                raise RuntimeError(_l("The Agent session is closed.", "Agent 会话已关闭。"))
            if self._status != "awaiting_user":
                raise RuntimeError(_l(
                    "The Agent session is not awaiting a user answer.",
                    "Agent 会话当前并未等待用户答复。",
                ))
            node = self.store.get_node(self.tree.id, self._leaf_id)
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            pending = self._pending_from_node(node)
            if pending is None or str(pending.get("id") or "") != normalized_question_id:
                raise ValueError(_l(
                    "No matching pending question was found.",
                    "未找到匹配的待处理问题。",
                ))
            run_id = str(value.get("run_id") or self._current_run_id)
            if not run_id or run_id in self._cancelled_run_ids:
                raise RuntimeError(_l(
                    "The pending Agent run was cancelled.",
                    "待处理的 Agent 运行已取消。",
                ))
            permission = pending.get("permission")
            if isinstance(permission, Mapping):
                fingerprint = str(permission.get("fingerprint") or "").strip()
                option_labels = {
                    str(item.get("label") or "").strip()
                    for item in pending.get("options") or ()
                    if isinstance(item, Mapping)
                }
                negative = normalized_answer.strip().lower() in {
                    "拒绝", "否", "不允许", "no", "n", "deny", "cancel",
                }
                if fingerprint and normalized_answer in option_labels and not negative:
                    with self._state_lock:
                        if "本次会话" in normalized_answer or "始终" in normalized_answer:
                            self._permission_session_grants.add(fingerprint)
                            self._persist_session_permission_grant(fingerprint)
                        else:
                            self._permission_once_grants.add(fingerprint)
            elif str(pending.get("kind") or "") == "clarification":
                self._append_clarification_authorization(run_id, normalized_answer)

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
                raise RuntimeError(_l(
                    "The pending tool result is no longer available.",
                    "待处理的工具结果已不可用。",
                ))

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
                _l(
                    "User answer mounted; waiting for model",
                    "用户答复已挂载，正在等待模型",
                ),
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
        run_id = self._node_run_id(source)
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
        details = {
            "run_id": run_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "user_request": str(value.get("content") or ""),
            "user_node_id": source.id,
            "metadata": deepcopy(dict(metadata)),
        }
        if value.get("turn_start_complete") is True:
            mounts = self._stored_context_mounts(value.get("context_mounts"))
        elif value.get("session_start_complete") is True:
            # Compatibility with turns frozen by builds where SessionStart was
            # incorrectly used as TurnStart. Finish that exact turn without
            # rerunning any provider; the next turn adopts the new lifecycle.
            mounts = self._stored_context_mounts(value.get("session_start_mounts"))
            if not mounts:
                session_context = str(value.get("session_start_context") or "").strip()
                if session_context:
                    mounts = [{
                        "kind": "plugin_session",
                        "content": session_context,
                        "source": "SessionStart",
                    }]
        else:
            stable_mounts = await self.build_session_mounts(details)
            turn_mounts = await self.build_turn_mounts(details)
            # Stable mounts always lead. A changing per-turn suffix therefore
            # cannot invalidate the provider cache for the stable prefix.
            mounts = self._unique_context_mounts([*stable_mounts, *turn_mounts])
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            source_value = dict(value)
            if value.get("turn_start_complete") is not True:
                source_value["turn_start_complete"] = True
                source_value["context_mounts"] = deepcopy(mounts)
                self.store.update_node(self.tree.id, source.id, source_value)
            if not mounts:
                # A malformed provider payload must not strand the user turn.
                source_value["trigger_model"] = True
                self.store.update_node(self.tree.id, source.id, source_value)
                self._leaf_id = source.id
                return

            self._mount_context_nodes(source, run_id, mounts)

    def _mount_context_nodes(
        self,
        source: Any,
        run_id: str,
        mounts: list[dict[str, str]],
    ) -> None:
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
                        "context_source": mount["source"],
                        "context_lifecycle": str(mount.get("lifecycle") or ""),
                        "source_node_id": source.id,
                        "context_index": index,
                        "metadata": {"source": mount["source"]},
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
                changed = False
                if child_value.get("trigger_model") is not should_trigger:
                    child_value["trigger_model"] = should_trigger
                    changed = True
                lifecycle = str(mount.get("lifecycle") or "")
                if str(child_value.get("context_lifecycle") or "") != lifecycle:
                    child_value["context_lifecycle"] = lifecycle
                    changed = True
                if changed:
                    child = self.store.update_node(
                        self.tree.id,
                        child.id,
                        child_value,
                    )
            parent = child
        self._leaf_id = parent.id

    def _configured_compaction_limit(self) -> int:
        resolver = self._plugin_service_values.get("model_context_limit")
        if not callable(resolver):
            return 0
        try:
            return max(
                0,
                int(
                    resolver(
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

    def _direct_model_tool_definitions(self) -> tuple[dict[str, Any], ...]:
        """Return ordinary direct tools plus explicit session-local tools.

        Session-local tools let a product workflow expose a hidden control
        protocol without adding it to every Agent conversation or Toolbox.
        Activation and Agent-scope checks still go through the registry.
        """

        current_run_id = str(getattr(self, "_current_run_id", "") or "")
        runtime = getattr(self, "runtime", None)

        def circuit_is_open(name: str) -> bool:
            return bool(
                current_run_id
                and runtime is not None
                and runtime.circuit_failure(
                    name,
                    current_run_id,
                    agent_id=self.agent_id,
                )
                is not None
            )

        definitions = list(
            self.registry.direct_tool_definitions(
                agent_id=self.agent_id,
                read_only=self._read_only,
            )
        )
        if current_run_id:
            definitions = [
                definition
                for definition in definitions
                if not (
                    isinstance(definition, Mapping)
                    and isinstance(definition.get("function"), Mapping)
                    and circuit_is_open(
                        str(definition["function"].get("name") or "")
                    )
                )
            ]
        seen = {
            str((definition.get("function") or {}).get("name") or "")
            for definition in definitions
            if isinstance(definition, Mapping)
            and isinstance(definition.get("function"), Mapping)
        }
        for name in self._extra_direct_tool_names:
            if name in seen or circuit_is_open(name):
                continue
            plugin = self.registry.resolve(name, agent_id=self.agent_id)
            if plugin.kind != "tool":
                raise ValueError(f"Session direct Plugin is not a tool: {name}")
            if self._read_only and not plugin.permits_read_only():
                raise ValueError(
                    f"Session direct Plugin is unavailable in read-only mode: {name}"
                )
            definitions.append(
                plugin.tool_definition(
                    allow_resource_reveal=self.agent_id == "main",
                )
            )
            seen.add(name)
        return tuple(definitions)

    def _model_tool_tokens(self) -> int:
        self.reconcile_plugins()
        self._ensure_required_session_packs()
        self._model_tools = self._direct_model_tool_definitions()
        if not self._model_tools:
            return 0
        return message_token_estimate(
            {
                "role": "system",
                "tools": list(self._model_tools),
            }
        )

    def _prepare_model_input(self, trigger: ContextNode) -> _PreparedModelInput:
        """Build the sole messages/tools/token snapshot for one transition."""

        with operation(
            logger,
            "cyrene.core.session",
            "prepare_model_input",
            tree_id=self.tree.id,
            node_id=trigger.id,
        ) as op:
            self.reconcile_plugins()
            self._ensure_required_session_packs()
            self._model_tools = self._direct_model_tool_definitions()
            tools = deepcopy(list(self._model_tools))
            messages = self._messages(trigger.id)
            message_tokens = messages_token_estimate(messages)
            tool_tokens = (
                message_token_estimate({"role": "system", "tools": tools})
                if tools
                else 0
            )
            prepared = _PreparedModelInput(
                trigger_id=trigger.id,
                registry_sync_token=self.registry.sync_token,
                messages=messages,
                tools=tools,
                message_tokens=message_tokens,
                tool_tokens=tool_tokens,
                compaction_tokens=message_tokens + tool_tokens,
                routing_tokens=message_tokens + tool_tokens,
                services=dict(self._plugin_service_values),
            )
            op.finish(
                message_count=len(messages),
                tool_count=len(tools),
                message_tokens=message_tokens,
                tool_tokens=tool_tokens,
                routing_tokens=prepared.routing_tokens,
                registry_sync_token=prepared.registry_sync_token,
            )
            return prepared

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
        prepared: _PreparedModelInput | None = None,
    ) -> tuple[ContextNode | None, dict[str, Any]]:
        limit = max(0, int(context_limit or 0))
        messages, reserved_tokens, before = self._compaction_input(trigger, prepared)
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

    def _compaction_input(
        self,
        trigger: ContextNode,
        prepared: _PreparedModelInput | None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        model_input = prepared or self._prepare_model_input(trigger)
        if model_input.trigger_id != trigger.id:
            raise ValueError("prepared model input does not match compaction trigger")
        return model_input.messages, model_input.tool_tokens, model_input.compaction_tokens

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
                raise RuntimeError(_l("The Agent session is closed.", "Agent 会话已关闭。"))
            if self._status == "awaiting_user":
                raise RuntimeError(_l(
                    "Cannot compact while awaiting a user answer.",
                    "等待用户答复时无法压缩上下文。",
                ))
            if self._status != "idle" or transitions_pending:
                raise RuntimeError(_l(
                    "Manual context compaction requires an idle Agent.",
                    "手动压缩上下文要求 Agent 处于空闲状态。",
                ))
            leaf = self.store.get_node(self.tree.id, self._leaf_id)
            compacting_state = self._set_state_locked(
                "compacting",
                _l("Compacting durable context", "正在压缩持久上下文"),
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
                    _l("Context compacted", "上下文已压缩")
                    if node
                    else _l("Ready", "就绪"),
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
                raise RuntimeError(_l("The Agent session is closed.", "Agent 会话已关闭。"))
            if self._status == "awaiting_user":
                raise RuntimeError(_l(
                    "Cannot retry while awaiting a user answer.",
                    "等待用户答复时无法重试。",
                ))
            if self._status != "idle" or transitions_pending:
                raise RuntimeError(_l(
                    "Retry requires an idle Agent.",
                    "重试要求 Agent 处于空闲状态。",
                ))
            path = self.store.get_path(self.tree.id, self._leaf_id)
            latest_user = next(
                (
                    node
                    for node in reversed(path)
                    if isinstance(node.value, Mapping)
                    and node.value.get("role") == "user"
                    and node.value.get("runtime_guidance") is not True
                ),
                None,
            )
            if latest_user is None or latest_user.parent_id is None:
                raise RuntimeError(_l(
                    "The conversation has no user turn to retry.",
                    "会话中没有可重试的用户轮次。",
                ))
            previous_run_id = str(latest_user.value.get("run_id") or "")
            parent_id = str(latest_user.parent_id)
            committed_leaf_id, _ = self.store.committed_state(self.tree.id)
            if not committed_leaf_id and self._leaf_id != self.tree.root_id:
                # Trees created before the commit-pointer migration still have
                # one public branch.  Capture it before creating the first
                # post-migration retry sibling.
                current_run_id = self._node_run_id(path[-1])
                if current_run_id:
                    self.store.commit_state(
                        self.tree.id,
                        self._leaf_id,
                        current_run_id,
                    )
            state = self._set_state_locked(
                "idle",
                _l("Ready to retry", "已准备重试"),
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

    def commit_result(self, leaf_id: str, run_id: str) -> None:
        """Mark a terminal/pending branch as accepted by the public host."""

        self.store.commit_state(self.tree.id, leaf_id, run_id)

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

    async def _automatic_compaction(
        self,
        trigger: ContextNode,
        prepared: _PreparedModelInput,
        run_id: str,
    ) -> tuple[ContextNode, _PreparedModelInput, str]:
        with operation(
            logger,
            "cyrene.core.session",
            "compaction_gate",
            tree_id=self.tree.id,
            run_id=run_id,
            node_id=trigger.id,
            before_tokens=prepared.compaction_tokens,
        ) as op:
            compacted_node, result = await self._compact_at_node(
                trigger,
                context_limit=self._configured_compaction_limit(),
                force=False,
                reason="automatic_60_percent",
                resume_model=True,
                prepared=prepared,
            )
            op.finish(
                compacted=compacted_node is not None,
                reason=str(result.get("reason") or ""),
                limit=int(result.get("limit") or 0),
            )
        if compacted_node is None:
            return trigger, prepared, run_id
        return compacted_node, self._prepare_model_input(compacted_node), self._node_run_id(compacted_node)

    async def _finish_model_failure(
        self,
        trigger: ContextNode,
        result: PluginCallResult,
        run_id: str,
    ) -> None:
        logger.error("Model Plugin call failed: %s", result.error)
        public_message, failure_metadata = _model_failure_projection(
            result.error_details
        )
        failure = self._mount_assistant(
            trigger.id,
            public_message,
            error=True,
            caused_by=self._transition_key(trigger),
            run_id=run_id,
            metadata=failure_metadata,
        )
        if failure is not None:
            await self._finish_terminal(failure, status="failed")

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
        guidance_service = self._guidance_service()
        if (
            guidance_service is not None
            and bool(guidance_service.has_pending)
        ):
            guidance_events = await self._collect_guidance()
            if guidance_events:
                mounted_guidance = await self._mount_guidance(
                    trigger,
                    guidance_events,
                    run_id=run_id,
                )
                if mounted_guidance is not None:
                    return
        prepared = self._prepare_model_input(trigger)
        trigger_value = trigger.value if isinstance(trigger.value, Mapping) else {}
        if trigger_value.get("role") not in {
            "context_compaction",
            "context_reflection",
        }:
            trigger, prepared, run_id = await self._automatic_compaction(
                trigger, prepared, run_id
            )
        if self._is_cancelled(run_id):
            return
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
            self._model_calls += 1
            count = self._model_calls
        if self._max_model_calls is not None and count > self._max_model_calls:
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
            if self._max_model_calls is None:
                detail = _l(
                    "Calling {model} (call {count})",
                    "正在调用 {model}（第 {count} 次）",
                    model=self.model_plugin,
                    count=count,
                )
            else:
                detail = _l(
                    "Calling {model} ({count}/{limit})",
                    "正在调用 {model}（{count}/{limit}）",
                    model=self.model_plugin,
                    count=count,
                    limit=self._max_model_calls,
                )
            model_state = self._set_state_locked("model", detail)
        self._emit_state_snapshot(model_state)
        arguments = {
            "messages": prepared.messages,
            "tools": prepared.tools,
        }
        transition_key = self._transition_key(trigger)
        model_services = {
            **prepared.services,
            "model_stream": self._model_stream_sink(
                run_id=run_id,
                trigger_id=trigger.id,
                transition_key=transition_key,
            ),
        }
        model_context = PluginContext(
            workspace=self.workspace,
            tree=self.store,
            tree_id=self.tree.id,
            node_id=trigger.id,
            data=self._plugin_data(
                run_id=run_id,
                model_call_kind="agent",
                user_request=self.current_user_request,
                prepared_request_tokens=prepared.routing_tokens,
            ),
            services=model_services,
        )
        model_task = asyncio.create_task(
            self.runtime.call(self.model_plugin, arguments, model_context)
        )
        wait_task: asyncio.Task[bool] | None = None
        try:
            if guidance_service is not None:
                wait_task = asyncio.create_task(guidance_service.wait())
                await asyncio.wait(
                    (model_task, wait_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_task.done() and bool(wait_task.result()) and not model_task.done():
                    model_task.cancel()
                    await asyncio.gather(model_task, return_exceptions=True)
                    with self._state_lock:
                        self._streamed_transition_keys.discard(transition_key)
                    guidance_events = await self._collect_guidance()
                    if guidance_events:
                        mounted_guidance = await self._mount_guidance(
                            trigger,
                            guidance_events,
                            run_id=run_id,
                        )
                        if mounted_guidance is not None:
                            return
                    model_task = asyncio.create_task(
                        self.runtime.call(self.model_plugin, arguments, model_context)
                    )
                if wait_task is not None and not wait_task.done():
                    wait_task.cancel()
                    await asyncio.gather(wait_task, return_exceptions=True)
            result = await model_task
        except BaseException:
            pending_tasks = [
                task
                for task in (model_task, wait_task)
                if task is not None and not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            with self._state_lock:
                self._streamed_transition_keys.discard(transition_key)
            raise
        if self._is_cancelled(run_id):
            return
        if not result.success or not isinstance(result.value, Mapping):
            await self._finish_model_failure(trigger, result, run_id)
            return
        output = dict(result.value)
        calls = output.get("tool_calls")
        calls = self._prepare_resource_tool_calls(
            calls if isinstance(calls, list) else []
        )
        with self._state_lock:
            streamed = transition_key in self._streamed_transition_keys
            self._streamed_transition_keys.discard(transition_key)
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
                    "streamed": streamed,
                    "run_id": run_id,
                    "caused_by": transition_key,
                    "batch_key": batch_key,
                },
                node_id=self._stable_id("assistant", transition_key),
            )
            assistant_state = self._set_state_locked(
                "tools" if calls else "finalizing",
                _l("Executing tools", "正在执行工具")
                if calls
                else _l("Running SessionEnd hooks", "正在运行 SessionEnd Hook"),
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
        guidance_service = self._guidance_service()
        driver = self._session_driver if self._owns_session_driver else None
        driver_pending = bool(driver is not None and driver.has_pending_work)
        if guidance_service is not None and value.get("intermediate") is not True:
            guidance_events: list[dict[str, Any]] = []
            if driver_pending:
                if bool(guidance_service.has_pending):
                    guidance_events = await self._collect_guidance()
            else:
                guidance_events = await self._collect_guidance(terminal=True)
            if guidance_events:
                mounted_guidance = await self._mount_guidance(
                    assistant,
                    guidance_events,
                    run_id=run_id,
                )
                if mounted_guidance is not None:
                    self._mark_assistant_intermediate(assistant)
                    return
                if not driver_pending:
                    await self._finish_terminal(assistant, status=status)
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
                    and candidate.get("runtime_guidance") is not True
                ):
                    user_value = candidate
                    user_node_id = node.id
                    break
                if (
                    candidate.get("role") == "context_reflection"
                    and str(candidate.get("run_id") or "") == run_id
                ):
                    model_context = candidate.get("model_context")
                    model_context = (
                        model_context
                        if isinstance(model_context, Mapping)
                        else {}
                    )
                    reflected_users = model_context.get("user_messages")
                    reflected_users = (
                        reflected_users
                        if isinstance(reflected_users, list)
                        else []
                    )
                    reflected_user = next(
                        (
                            item
                            for item in reversed(reflected_users)
                            if isinstance(item, Mapping)
                            and str(item.get("round_id") or "") == run_id
                        ),
                        None,
                    )
                    if isinstance(reflected_user, Mapping):
                        public_content = str(reflected_user.get("content") or "")
                        user_value = {
                            "content": public_content,
                            "metadata": {
                                "public_user_message": public_content,
                            },
                        }
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
                    "user_request": self.current_user_request
                    or str(user_value.get("content") or ""),
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
                _l("Complete", "已完成")
                if terminal_status == "completed"
                else _l("Failed", "失败"),
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
            **(
                {"failure": result.failure.as_dict()}
                if result.failure is not None
                else {}
            ),
        }

    @staticmethod
    def _restored_result(raw: Mapping[str, Any]) -> PluginCallResult:
        raw_failure = raw.get("failure")
        return PluginCallResult(
            str(raw.get("call_id") or ""),
            str(raw.get("name") or ""),
            bool(raw.get("success")),
            raw.get("value"),
            str(raw.get("error") or ""),
            datetime.fromisoformat(str(raw.get("time"))),
            (
                PluginFailure.from_dict(raw_failure)
                if isinstance(raw_failure, Mapping)
                else None
            ),
        )

    def _resource_presentation(
        self,
        call: Mapping[str, Any] | None,
        *,
        phase: Literal["started", "completed"],
    ) -> dict[str, Any]:
        if not isinstance(call, Mapping):
            return {}
        try:
            plugin = self.registry.resolve(
                str(call.get("resource_plugin_name") or call.get("name") or ""),
                agent_id=self.agent_id,
            )
        except Exception:
            return {}
        project_id = str(self._plugin_context_data.get("project_id") or "")
        if not plugin.resource_effects or not project_id:
            return {}
        locations = workspace_resource_locations(
            plugin.resource_effects,
            dict(call.get("resource_arguments") or call.get("arguments") or {}),
            workspace=self.workspace,
            project_id=project_id,
            phase=phase,
        )
        if not locations:
            return {}
        return {
            "locations": list(locations),
            "reveal": bool(call.get("resource_reveal")) and self.agent_id == "main",
            "phase": phase,
        }

    def _prepare_resource_tool_calls(self, calls: Sequence[Any]) -> list[Any]:
        """Persist the Runtime's canonical call and host-only resource metadata."""

        prepared: list[Any] = []
        for raw in calls:
            if not isinstance(raw, Mapping):
                prepared.append(raw)
                continue
            call = dict(raw)
            provider_arguments_normalized = bool(
                call.pop("arguments_normalized", False)
            )
            provider_nested_arguments_normalized = bool(
                call.pop("nested_arguments_normalized", False)
            )
            call.pop("_arguments_normalized", None)
            call.pop("_nested_arguments_normalized", None)
            call.pop("argument_repairs", None)
            try:
                normalized = self.runtime.normalize_call(
                    PluginCall(
                        name=str(call.get("name") or ""),
                        arguments=dict(call.get("arguments") or {}),
                        id=str(call.get("id") or f"call_{uuid4().hex}"),
                        arguments_normalized=provider_arguments_normalized,
                        nested_arguments_normalized=(
                            provider_nested_arguments_normalized
                        ),
                    ),
                    PluginContext(
                        workspace=self.workspace,
                        tree=self.store,
                        tree_id=self.tree.id,
                        data=self._plugin_data(
                            model_call_kind="tool_prepare",
                            user_request=self.current_user_request,
                        ),
                        services=self._plugin_services(),
                    ),
                )
                arguments = dict(normalized.arguments)
                argument_repairs = list(normalized.argument_repairs)
                resource_plugin = normalized.effective_plugin
                resource_arguments = dict(normalized.effective_arguments)
                if (
                    normalized.plugin.name == TOOLBOX_PLUGIN_NAME
                    and str(arguments.get("operation") or "") == "invoke"
                ):
                    resource_arguments, reveal = split_resource_reveal(
                        resource_arguments,
                        effects=resource_plugin.resource_effects,
                        allow_reveal=self.agent_id == "main",
                    )
                    arguments["arguments"] = resource_arguments
                else:
                    arguments, reveal = split_resource_reveal(
                        arguments,
                        effects=normalized.plugin.resource_effects,
                        allow_reveal=self.agent_id == "main",
                    )
                    resource_arguments = arguments
            except Exception:
                prepared.append(call)
                continue
            call["arguments"] = arguments
            call["_arguments_normalized"] = True
            call["_nested_arguments_normalized"] = (
                normalized.call.nested_arguments_normalized
            )
            if argument_repairs:
                call["argument_repairs"] = argument_repairs
            if resource_plugin.resource_effects:
                call["resource_plugin_name"] = resource_plugin.name
                call["resource_arguments"] = resource_arguments
                call["resource_reveal"] = reveal
                presentation = self._resource_presentation(call, phase="started")
                if presentation:
                    call["presentation"] = presentation
            prepared.append(call)
        return prepared

    def _persist_effect_result(self, assistant_id: str, result: PluginCallResult) -> None:
        with self._state_lock:
            node = self.store.get_node(self.tree.id, assistant_id)
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            calls = value.get("tool_calls")
            calls = calls if isinstance(calls, list) else []
            source_call = next((
                call for call in calls
                if isinstance(call, Mapping)
                and str(call.get("id") or "") == result.call_id
            ), None)
            stored_result = self._stored_result(result)
            presentation = self._resource_presentation(
                source_call,
                phase="completed",
            )
            if presentation:
                stored_result["presentation"] = presentation
            self.store.save_effect_result(
                self.tree.id,
                assistant_id,
                result.call_id,
                stored_result,
            )
            run_id = str(value.get("run_id") or "")
        self._emit_event(
            "tool.completed",
            run_id=run_id,
            node_id=assistant_id,
            time=result.time,
            data=stored_result,
        )

    def _reflection_call(
        self,
        calls: list[Any],
    ) -> Mapping[str, Any] | None:
        """Return the first Plugin-declared context-reflection control call."""

        for raw_call in calls:
            if not isinstance(raw_call, Mapping):
                continue
            try:
                plugin = self.registry.resolve(
                    str(raw_call.get("name") or ""),
                    agent_id=self.agent_id,
                )
            except Exception:
                continue
            if str(plugin.metadata.get("session_transition") or "") == (
                "deep_reflection"
            ):
                return raw_call
        return None

    async def _continue_reflection(
        self,
        assistant: ContextNode,
        call: Mapping[str, Any],
    ) -> None:
        """Build a Reflect Pack asynchronously, then atomically rewrite the tree."""

        run_id = self._node_run_id(assistant)
        path = self.store.get_path(self.tree.id, assistant.id)
        start_index = next(
            (
                index
                for index, node in enumerate(path[1:], start=1)
                if isinstance(node.value, Mapping)
                and node.value.get("role") in {"user", "context_reflection"}
            ),
            -1,
        )
        if start_index < 0:
            raise RuntimeError("deep reflection has no replaceable conversation path")
        source_path = path[start_index:]
        start = source_path[0]
        expected_node_ids = tuple(
            node.id for node in self.store.get_subtree(self.tree.id, start.id)
        )
        services = self._plugin_services()
        service = services.get("deep_reflection")
        reflect = getattr(service, "reflect", None)
        if not callable(reflect):
            raise RuntimeError("DeepReflect Plugin service is unavailable")

        effective_messages = self._messages(assistant.id)
        stable_system_messages = [
            deepcopy(dict(message))
            for message in effective_messages
            if isinstance(message, Mapping)
            and str(message.get("role") or "") == "system"
        ]
        with self._state_lock:
            if self._closed or run_id in self._cancelled_run_ids:
                return
            reflecting_state = self._set_state_locked(
                "reflecting",
                _l("Rewriting conversation context", "正在重写对话上下文"),
                leaf_id=assistant.id,
            )
        self._emit_state_snapshot(reflecting_state)

        plugin_context = PluginContext(
            workspace=self.workspace,
            tree=self.store,
            tree_id=self.tree.id,
            node_id=assistant.id,
            hooks=self.hooks,
            data=self._plugin_data(
                run_id=run_id,
                model_call_kind="deep_reflection",
                user_request=self.current_user_request,
            ),
            services=services,
        )
        pack = await reflect(
            source_path,
            dict(call.get("arguments") or {}),
            plugin_context,
        )
        if self._is_cancelled(run_id):
            return
        batch_key = str(
            (assistant.value if isinstance(assistant.value, Mapping) else {}).get(
                "batch_key"
            )
            or self._stable_id("batch", assistant.id)
        )
        payload = {
            "role": "context_reflection",
            "run_id": run_id,
            "authorization_request": self.permission_user_request,
            "caused_by": batch_key,
            "trigger_model": True,
            "resume_model": True,
            "reflection_tool_call_id": str(call.get("id") or ""),
            **dict(pack),
            "messages": [
                *stable_system_messages,
                {
                    "role": "user",
                    "content": str(pack.get("rendered_model_context") or ""),
                    "reflect_pack": True,
                },
            ],
        }
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            rewritten, _deleted = self.store.replace_subtree(
                self.tree.id,
                start.id,
                payload,
                expected_node_ids=expected_node_ids,
            )
            rewritten_state = self._set_state_locked(
                "model",
                _l(
                    "Reflect Pack committed; waiting for model",
                    "Reflect Pack 已提交，正在等待模型",
                ),
                leaf_id=rewritten.id,
            )
        self._emit_state_snapshot(rewritten_state)

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
                        str(
                            pending.get("text")
                            or _l("Waiting for user answer", "正在等待用户答复")
                        ),
                        leaf_id=existing_result.id,
                    )
                else:
                    restored_state = self._set_state_locked(
                        "model",
                        _l(
                            "Tool results restored; waiting for model",
                            "工具结果已恢复，正在等待模型",
                        ),
                        leaf_id=existing_result.id,
                    )
            self._emit_state_snapshot(restored_state)
            if pending is None and self._transition_assistant(existing_result) is None:
                self._enqueue_transition("advance", existing_result)
            return

        value = assistant.value if isinstance(assistant.value, Mapping) else {}
        calls = value.get("tool_calls")
        calls = calls if isinstance(calls, list) else []

        reflection_call = self._reflection_call(calls)
        if reflection_call is not None:
            await self._continue_reflection(assistant, reflection_call)
            return

        plugin_calls = tuple(
            PluginCall(
                name=str(call.get("name") or ""),
                arguments=dict(call.get("arguments") or {}),
                id=str(call.get("id") or f"call_{uuid4().hex}"),
                arguments_normalized=bool(call.get("_arguments_normalized")),
                nested_arguments_normalized=bool(
                    call.get("_nested_arguments_normalized")
                ),
                argument_repairs=tuple(
                    dict(repair)
                    for repair in (call.get("argument_repairs") or ())
                    if isinstance(repair, Mapping)
                ),
            )
            for call in calls
            if isinstance(call, Mapping)
        )
        if not plugin_calls:
            failure = self._mount_assistant(
                assistant.id,
                _l(
                    "The model returned no valid tool calls.",
                    "模型未返回有效的工具调用。",
                ),
                error=True,
                caused_by=str(value.get("batch_key") or ""),
                run_id=run_id,
            )
            if failure is not None:
                await self._finish_terminal(failure, status="failed")
            return
        completed: dict[str, PluginCallResult] = {}
        persisted_effects = dict(value.get("effect_results") or {})
        persisted_effects.update(
            self.store.effect_results(self.tree.id, assistant.id)
        )
        for call_id, raw in persisted_effects.items():
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
                _l("Reviewing and executing tools", "正在审核并执行工具"),
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
        guidance_service = self._guidance_service()
        guidance_events = (
            await self._collect_guidance()
            if guidance_service is not None and bool(guidance_service.has_pending)
            else []
        )
        pending_question = (
            None
            if guidance_events
            else self._pending_question_from_results(
                calls,
                results,
                run_id=run_id,
            )
        )
        with self._linearized_context_commit():
            if self._closed or run_id in self._cancelled_run_ids:
                return
            call_by_id = {
                str(call.get("id") or ""): call
                for call in calls
                if isinstance(call, Mapping)
            }
            result_values = self._tool_result_values(call_by_id, results)
            tool_node = self.store.mount(
                self.tree.id,
                assistant.id,
                {
                    "role": "tool_results",
                    "trigger_model": pending_question is None and not guidance_events,
                    "run_id": run_id,
                    "caused_by": batch_key,
                    "results": result_values,
                    **(
                        {"pending_question": pending_question}
                        if pending_question is not None
                        else {}
                    ),
                },
                node_id=self._stable_id("tool_results", batch_key),
            )
            self.store.clear_effect_results(self.tree.id, assistant.id)
            if pending_question is not None:
                tool_state = self._set_state_locked(
                    "awaiting_user",
                    str(
                        pending_question.get("text")
                        or _l("Waiting for user answer", "正在等待用户答复")
                    ),
                    leaf_id=tool_node.id,
                )
            else:
                tool_state = self._set_state_locked(
                    "model",
                    _l(
                        "Tool results mounted; waiting for model",
                        "工具结果已挂载，正在等待模型",
                    ),
                    leaf_id=tool_node.id,
                )
        self._emit_state_snapshot(tool_state)
        if guidance_events:
            await self._mount_guidance(
                tool_node,
                guidance_events,
                run_id=run_id,
            )

    def _tool_result_values(
        self,
        call_by_id: Mapping[str, Mapping[str, Any]],
        results: Sequence[PluginCallResult],
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for item in results:
            stored = {
                "call_id": item.call_id,
                "name": item.name,
                "success": item.success,
                "value": self._json_value(item.value),
                "error": item.error,
                **(
                    {"failure": item.failure.as_dict()}
                    if item.failure is not None
                    else {}
                ),
            }
            presentation = self._resource_presentation(
                call_by_id.get(item.call_id),
                phase="completed",
            )
            if presentation:
                stored["presentation"] = presentation
            values.append(stored)
        return values

    def _mount_assistant(
        self,
        parent_id: str,
        content: str,
        *,
        error: bool,
        caused_by: str = "",
        run_id: str = "",
        metadata: Mapping[str, Any] | None = None,
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
                value = {
                    "role": "assistant",
                    "content": str(content),
                    "error": bool(error),
                    "run_id": effective_run_id,
                    "caused_by": caused_by,
                }
                if metadata:
                    value.update(deepcopy(dict(metadata)))
                node = self.store.mount(
                    self.tree.id,
                    parent_id,
                    value,
                    node_id=node_id,
                )
            else:
                node = existing
            terminal_state = self._set_state_locked(
                "finalizing",
                _l("Running SessionEnd hooks", "正在运行 SessionEnd Hook"),
                leaf_id=node.id,
            )
        self._emit_state_snapshot(terminal_state)
        return node

    def _mark_leaf_waiting_for_driver(self) -> None:
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
        metadata = getattr(self._session_driver, "waiting_metadata", {})
        if isinstance(metadata, Mapping):
            value.update(deepcopy(dict(metadata)))
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
        active_context_ids = selected_context_node_ids(path, current_run_id)
        root_value = (
            path[0].value
            if path and isinstance(path[0].value, Mapping)
            else {}
        )
        base_system_content = str(root_value.get("content") or "")
        for node in path:
            value = node.value if isinstance(node.value, Mapping) else {}
            role = str(value.get("role") or "")
            if role in {"context_compaction", "context_reflection"}:
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
                messages.append({"role": role, "content": content})
            elif role == "context":
                if node.id not in active_context_ids:
                    continue
                content = str(value.get("content") or "").strip()
                if not content:
                    continue
                project_context_message(messages, value)
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
                                    **(
                                        {"failure": result.get("failure")}
                                        if isinstance(result.get("failure"), Mapping)
                                        else {}
                                    ),
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )
                    mcp_service = self._plugin_services().get("mcp")
                    builder = getattr(
                        mcp_service,
                        "build_observation_content",
                        None,
                    )
                    observation = (
                        builder(
                            result.get("value"),
                            tool_name=str(result.get("name") or ""),
                        )
                        if callable(builder)
                        else None
                    )
                    materialize = getattr(
                        mcp_service,
                        "materialize_content_block",
                        None,
                    )
                    if observation and callable(materialize):
                        observation = [materialize(block) for block in observation]
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

    def _record_permission_review(
        self,
        events: Sequence[HookEvent],
        decisions: Sequence[PermissionDecision],
    ) -> None:
        reviewed_events = tuple(events)
        reviewed_decisions = tuple(decisions)
        if not reviewed_events or len(reviewed_events) != len(reviewed_decisions):
            return
        reviewed_at = datetime.now(timezone.utc)
        with self._state_lock:
            assistant_id = self._leaf_id
            try:
                node = self.store.get_node(self.tree.id, assistant_id)
            except NodeNotFoundError:
                return
            value = dict(node.value) if isinstance(node.value, Mapping) else {}
            if value.get("role") != "assistant":
                return
            calls = value.get("tool_calls")
            calls = calls if isinstance(calls, list) else []
            matched_call_indices: set[int] = set()
            recorded_decisions: list[dict[str, Any]] = []
            for index, (hook_event, decision) in enumerate(
                zip(reviewed_events, reviewed_decisions)
            ):
                payload = (
                    hook_event.payload
                    if isinstance(hook_event.payload, Mapping)
                    else {}
                )
                tool = payload.get("tool") if isinstance(payload, Mapping) else None
                tool = tool if isinstance(tool, Mapping) else {}
                tool_name = str(tool.get("name") or "")
                tool_arguments = dict(tool.get("arguments") or {})
                matched_call: Mapping[str, Any] | None = None
                for call_index, raw_call in enumerate(calls):
                    if (
                        call_index in matched_call_indices
                        or not isinstance(raw_call, Mapping)
                    ):
                        continue
                    if (
                        str(raw_call.get("name") or "") == tool_name
                        and dict(raw_call.get("arguments") or {}) == tool_arguments
                    ):
                        matched_call = raw_call
                        matched_call_indices.add(call_index)
                        break
                if matched_call is None:
                    for call_index, raw_call in enumerate(calls):
                        if (
                            call_index in matched_call_indices
                            or not isinstance(raw_call, Mapping)
                            or str(raw_call.get("name") or "") != tool_name
                        ):
                            continue
                        matched_call = raw_call
                        matched_call_indices.add(call_index)
                        break
                recorded_decisions.append({
                    "index": index,
                    "tool": tool_name,
                    "tool_call_id": str(
                        (matched_call.get("id") or "")
                        if matched_call is not None
                        else ""
                    ),
                    "approved": bool(decision.approve),
                    "rationale": str(decision.rationale),
                })
            stored = value.get("permission_reviews")
            reviews = list(stored) if isinstance(stored, list) else []
            approved_count = sum(
                1 for decision in recorded_decisions if decision["approved"]
            )
            review = {
                "id": self._stable_id(
                    "permission_review",
                    f"{assistant_id}:{len(reviews)}",
                ),
                "approved": approved_count == len(recorded_decisions),
                "approved_count": approved_count,
                "denied_count": len(recorded_decisions) - approved_count,
                "decisions": recorded_decisions,
                "created_at": reviewed_at.isoformat(),
            }
            reviews.append(review)
            value["permission_reviews"] = reviews
            self.store.update_node(self.tree.id, assistant_id, value)
            run_id = str(value.get("run_id") or self._current_run_id)
        self._emit_event(
            "permission.reviewed",
            run_id=run_id,
            node_id=assistant_id,
            time=reviewed_at,
            data=review,
        )

    async def _permission_model(
        self,
        system_prompt: str,
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._state_lock:
            assistant_id = self._leaf_id
            run_id = self._current_run_id
            user_request = self._current_user_request
        decision_tool = (
            PERMISSION_BATCH_DECIDE_TOOL
            if isinstance(request.get("tools"), list)
            else PERMISSION_DECIDE_TOOL
        )
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
                "tools": [decision_tool],
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
            logger.error("Permission model failed: %s", result.error)
            raise RuntimeError(_l(
                "The permission model failed.",
                "权限模型调用失败。",
            ))
        self._persist_auxiliary_model_usage(assistant_id, result.value)
        decisions = [
            call
            for call in result.value.get("tool_calls") or ()
            if isinstance(call, Mapping) and call.get("name") == "decide"
        ]
        if len(decisions) != 1:
            raise RuntimeError(_l(
                "The permission model returned an invalid decision count.",
                "权限模型返回了无效的决策数量。",
            ))
        arguments = decisions[0].get("arguments")
        if not isinstance(arguments, Mapping):
            raise RuntimeError(_l(
                "The permission model returned invalid decision arguments.",
                "权限模型返回了无效的决策参数。",
            ))
        return dict(arguments)

    def request_cancel(self, reason: str = "user_cancelled") -> bool:
        """Request cancellation from any thread and persist a terminal marker."""

        normalized_reason = str(reason or "user_cancelled")
        with self._state_lock:
            if self._closed:
                log_operation(
                    logger,
                    "cyrene.core.session",
                    "request_cancel",
                    phase="skipped",
                    tree_id=self.tree.id,
                    run_id=self._current_run_id,
                    status=self._status,
                    closed=True,
                    reason=normalized_reason,
                )
                return False
        driver = self._session_driver if self._owns_session_driver else None
        children_active = bool(driver is not None and driver.has_active)
        if children_active:
            driver.request_cancel_all(normalized_reason)
        with self._linearized_context_commit():
            if (
                self._closed
                or (self._status == "idle" and not children_active)
                or not self._current_run_id
            ):
                log_operation(
                    logger,
                    "cyrene.core.session",
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
                _l("Cancelling", "正在取消"),
                leaf_id=cancelled.id,
            )
            self._current_user_request = ""
        log_operation(
            logger,
            "cyrene.core.session",
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
            self._set_state("idle", _l("Cancelled", "已取消"), leaf_id=cancelled.id)
            active_task_cancelled = False
        else:
            active_task_cancelled = False
        log_operation(
            logger,
            "cyrene.core.session",
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
            "cyrene.core.session",
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
            driver = self._session_driver if self._owns_session_driver else None
            changed = self.request_cancel(reason)
            if not changed:
                op.finish(changed=False)
                return False

            if driver is not None:
                await driver.cancel_all(reason)

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
                self._set_state("idle", _l("Cancelled", "已取消"))
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
                and value.get("intermediate") is not True
            ):
                candidates.append(node)
        if not candidates:
            log_operation(
                logger,
                "cyrene.core.session",
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
            "cyrene.core.session",
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
        pending_driver = bool(
            self._owns_session_driver
            and self._session_driver is not None
            and self._session_driver.has_pending_work
        )
        public_status = "running" if status == "idle" and pending_driver else status
        public_detail = (
            str(
                getattr(
                    self._session_driver,
                    "pending_detail",
                    _l("Background work", "后台工作"),
                )
            )
            if pending_driver
            else detail
        )
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
        if self._owns_session_driver and self._session_driver is not None:
            snapshot = self._session_driver.session_snapshot()
            if isinstance(snapshot, Mapping):
                result.update(deepcopy(dict(snapshot)))
        log_operation(
            logger,
            "cyrene.core.session",
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

    async def _drive_session_driver_with_guidance(
        self,
        guidance_service: Any | None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Race child coordination against a loop-neutral guidance wakeup."""

        driver = self._session_driver
        if driver is None:
            return False, []
        drive_task = asyncio.create_task(driver.drive())
        wait_task: asyncio.Task[bool] | None = None
        try:
            if guidance_service is not None:
                wait_task = asyncio.create_task(guidance_service.wait())
                await asyncio.wait(
                    (drive_task, wait_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if (
                    wait_task.done()
                    and bool(wait_task.result())
                    and not drive_task.done()
                ):
                    drive_task.cancel()
                    await asyncio.gather(drive_task, return_exceptions=True)
                    return False, await self._collect_guidance()
                if not wait_task.done():
                    wait_task.cancel()
                    await asyncio.gather(wait_task, return_exceptions=True)
            drove = bool(await drive_task)
            if (
                guidance_service is not None
                and bool(guidance_service.has_pending)
            ):
                return drove, await self._collect_guidance()
            return drove, []
        finally:
            pending_tasks = [
                task
                for task in (drive_task, wait_task)
                if task is not None and not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait until queued Hooks and all resulting transitions are idle."""

        with operation(
            logger,
            "cyrene.core.session",
            "drain",
            tree_id=self.tree.id,
            run_id=self.current_run_id,
        ) as op:
            attempt = 0
            while True:
                attempt += 1
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
                        self._owns_session_driver
                        and self._session_driver is not None
                        and self._session_driver.has_pending_work
                    ):
                        self._mark_leaf_waiting_for_driver()
                        guidance_service = self._guidance_service()
                        if (
                            guidance_service is not None
                            and bool(guidance_service.has_pending)
                        ):
                            guidance_events = await self._collect_guidance()
                            if guidance_events:
                                with self._state_lock:
                                    guidance_parent_id = self._leaf_id
                                    guidance_run_id = self._current_run_id
                                guidance_parent = self.store.get_node(
                                    self.tree.id, guidance_parent_id
                                )
                                await self._mount_guidance(
                                    guidance_parent,
                                    guidance_events,
                                    run_id=guidance_run_id,
                                )
                                continue
                        drove, guidance_events = (
                            await self._drive_session_driver_with_guidance(
                                guidance_service
                            )
                        )
                        if guidance_events:
                            with self._state_lock:
                                guidance_parent_id = self._leaf_id
                                guidance_run_id = self._current_run_id
                            guidance_parent = self.store.get_node(
                                self.tree.id, guidance_parent_id
                            )
                            mounted_guidance = await self._mount_guidance(
                                guidance_parent,
                                guidance_events,
                                run_id=guidance_run_id,
                            )
                            if mounted_guidance is not None:
                                continue
                        if drove:
                            continue
                    op.finish(attempts=attempt, status=self._status, leaf_id=self._leaf_id)
                    return

    def close(self) -> None:
        """Stop process-local workers while leaving unfinished tree state recoverable."""

        with self._transition_condition:
            if self._closed:
                log_operation(
                    logger,
                    "cyrene.core.session",
                    "close",
                    phase="skipped",
                    tree_id=self.tree.id,
                    reason="already_closed",
                )
                return
            log_operation(
                logger,
                "cyrene.core.session",
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
        if self._owns_session_driver and self._session_driver is not None:
            self._session_driver.close()
        self._unsubscribe_context_events()
        self.store.close()
        log_operation(
            logger,
            "cyrene.core.session",
            "close",
            phase="completed",
            tree_id=self.tree.id,
            run_id=self._current_run_id,
            status=self._status,
        )

__all__ = [
    "AgentEventListener",
    "AgentSession",
    "AgentSessionEvent",
]
