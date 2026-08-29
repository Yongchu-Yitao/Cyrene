"""Persistent, tree-local Hook registration and ordered execution."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
import queue
import threading
import weakref
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..observability import log_operation, operation
from .errors import HookAwaitingUser, HookBlocked, HookError
from .hook import (
    CONTEXT_CHANGE,
    CONTEXT_USED,
    HOOK_EVENTS,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    TURN_START,
    Hook,
    HookEvent,
    HookPlugin,
)
from .plugin import PluginRegistry
from .storage import HookPersistence, validate_hook_config

logger = logging.getLogger(__name__)

HookOverrideProvider = Callable[[str, str], Mapping[str, Any] | None]
HookActionProvider = Callable[[Hook], HookPlugin | None]
_hook_override_provider: HookOverrideProvider | None = None
_hook_action_provider: HookActionProvider | None = None
_active_hook_sets: weakref.WeakSet[HookSet] = weakref.WeakSet()
_active_hook_sets_lock = threading.RLock()


def _hook_with_configured_override(hook: Hook) -> Hook:
    provider = _hook_override_provider
    if provider is None:
        return hook
    try:
        raw = provider(hook.id, hook.plugin_id)
        if not isinstance(raw, Mapping):
            return hook
        hook_id = str(raw.get("id", hook.id)).strip()
        event = str(raw.get("event", hook.event)).strip()
        plugin_id = str(raw.get("plugin_id", hook.plugin_id)).strip()
        if not hook_id or not plugin_id or event not in HOOK_EVENTS:
            return hook
        failure_policy = str(raw.get("failure_policy", hook.failure_policy))
        if failure_policy not in {"open", "block", "closed"}:
            return hook
        if failure_policy == "block" and event != PRE_TOOL_USE:
            return hook
        matcher = raw.get("matcher", hook.matcher)
        if matcher is not None and not isinstance(matcher, str):
            return hook
        config = raw.get("config", hook.config)
        if not isinstance(config, Mapping):
            return hook
        created_at = hook.created_at
        raw_created_at = raw.get("created_at")
        if raw_created_at is not None:
            created_at = datetime.fromisoformat(str(raw_created_at))
        return replace(
            hook,
            id=hook_id,
            event=event,
            plugin_id=plugin_id,
            root_only=bool(raw.get("root_only", hook.root_only)),
            matcher=matcher,
            failure_policy=failure_policy,  # type: ignore[arg-type]
            config=validate_hook_config(config),
            enabled=bool(raw.get("enabled", hook.enabled)),
            created_at=created_at,
        )
    except Exception:
        logger.warning(
            "Ignoring invalid configured Hook override for %s",
            hook.id,
            exc_info=True,
        )
        return hook


def configure_hook_override_provider(provider: HookOverrideProvider | None) -> None:
    """Install the product-level provider used by current and future HookSets."""

    global _hook_override_provider
    _hook_override_provider = provider
    refresh_active_hook_overrides()


def configure_hook_action_provider(provider: HookActionProvider | None) -> None:
    """Install the product-level resolver for user-defined Hook actions."""

    global _hook_action_provider
    _hook_action_provider = provider


def refresh_active_hook_overrides(hook_id: str = "", plugin_id: str = "") -> int:
    """Refresh matching in-memory bindings after a persisted override changes."""

    with _active_hook_sets_lock:
        hook_sets = tuple(_active_hook_sets)
    return sum(hooks.refresh_overrides(hook_id=hook_id, plugin_id=plugin_id) for hooks in hook_sets)


def _settle_future(
    future: Future[Any],
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Deliver a worker result unless its asyncio waiter was cancelled."""

    if future.done():
        return
    try:
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)
    except InvalidStateError:
        # Cancellation can race the check above from another event loop.
        pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class _DispatchRequest:
    event: HookEvent
    future: Future[tuple[Any, ...]]


@dataclass(slots=True)
class _PreToolRequest:
    name: str
    arguments: dict[str, Any]
    permission: dict[str, Any] | None
    time: datetime
    future: Future[dict[str, Any]]


@dataclass(slots=True)
class _PreToolBatchRequest:
    calls: tuple[
        tuple[str, dict[str, Any], datetime, dict[str, Any] | None], ...
    ]
    future: Future[tuple[dict[str, Any] | BaseException, ...]]


@dataclass(slots=True)
class _Barrier:
    future: Future[None]


WorkItem = str | _DispatchRequest | _PreToolRequest | _PreToolBatchRequest | _Barrier


class HookSet:
    """Persistent Hooks owned and serially executed by one context tree.

    A dedicated worker keeps Plugin calls ordered across application threads
    and asyncio loops. Context notifications are claimed from the tree's
    durable SQLite queue; direct decision and lifecycle calls share that worker.
    """

    def __init__(
        self,
        tree_id: str,
        root_id: str,
        persistence: HookPersistence,
        plugins: PluginRegistry,
    ) -> None:
        self.tree_id = str(tree_id)
        self.root_id = str(root_id)
        self._persistence = persistence
        self._plugins = plugins
        self._lock = threading.RLock()
        persistence.recover()
        self._hooks: dict[str, Hook] = {
            hook.id: hook for hook in persistence.list_hooks()
        }
        for plugin_id in {
            hook.plugin_id
            for hook in self._hooks.values()
            if plugins.resolve(hook.plugin_id) is not None
        }:
            persistence.requeue_blocked(plugin_id)
        self._work: queue.Queue[WorkItem] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._wake_enqueued = False
        self._closed = False
        self._before_dispatch: Callable[[], Any] | None = None
        with _active_hook_sets_lock:
            _active_hook_sets.add(self)
        log_operation(
            logger,
            "hook.set",
            "initialize",
            phase="completed",
            tree_id=self.tree_id,
            root_id=self.root_id,
            restored_hooks=[
                {"hook_id": hook.id, "event": hook.event, "plugin_id": hook.plugin_id}
                for hook in self._hooks.values()
            ],
        )

    @staticmethod
    def _new_hook_id() -> str:
        return f"hook_{uuid4().hex}"

    def _ensure_open(self) -> None:
        if self._closed:
            raise HookError(f"HookSet is closed for tree {self.tree_id}")

    def _ensure_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker_main,
            name=f"agent-hook-{self.tree_id}",
            daemon=True,
        )
        self._thread.start()
        log_operation(
            logger,
            "hook.set",
            "start_worker",
            phase="completed",
            tree_id=self.tree_id,
            thread=self._thread.name,
        )

    def _in_worker_thread(self) -> bool:
        return self._thread is threading.current_thread()

    def register(
        self,
        event: str,
        plugin: HookPlugin,
        *,
        plugin_id: str | None = None,
        hook_id: str | None = None,
        root_only: bool = False,
        matcher: str | None = None,
        failure_policy: str = "open",
        config: Mapping[str, Any] | None = None,
        enabled: bool = True,
    ) -> Callable[[], None]:
        """Persist a Plugin binding and return an idempotent unsubscribe callback."""

        event = str(event)
        log_operation(
            logger,
            "hook.set",
            "register",
            phase="requested",
            tree_id=self.tree_id,
            hook_id=hook_id,
            event=event,
            plugin_id=plugin_id,
            root_only=root_only,
            matcher=matcher,
            failure_policy=failure_policy,
            config=dict(config or {}),
            enabled=enabled,
        )
        if event not in HOOK_EVENTS:
            raise HookError(f"unsupported Hook event: {event}")
        if not callable(plugin):
            raise TypeError("Hook plugin must be callable")
        if matcher is not None and not isinstance(matcher, str):
            raise TypeError("persistent Hook matcher must be a glob string")
        if failure_policy not in {"open", "block", "closed"}:
            raise ValueError("failure_policy must be 'open', 'block', or 'closed'")
        if failure_policy == "block" and event != PRE_TOOL_USE:
            raise ValueError("only PreToolUse Hooks may block on failure")
        normalized_id = str(hook_id or self._new_hook_id()).strip()
        normalized_plugin_id = str(plugin_id or normalized_id).strip()
        if not normalized_id:
            raise ValueError("hook_id cannot be empty")
        if not normalized_plugin_id:
            raise ValueError("plugin_id cannot be empty")
        hook = _hook_with_configured_override(Hook(
            id=normalized_id,
            event=event,
            plugin_id=normalized_plugin_id,
            root_only=bool(root_only),
            matcher=matcher,
            failure_policy=failure_policy,  # type: ignore[arg-type]
            config=validate_hook_config(config or {}),
            enabled=bool(enabled),
            created_at=_utc_now(),
        ))
        with self._lock:
            self._ensure_open()
            if hook.id in self._hooks:
                raise HookError(f"Hook id already exists in tree {self.tree_id}: {hook.id}")
            self._plugins.register(normalized_plugin_id, plugin)
            self._persistence.save_hook(hook)
            self._hooks[hook.id] = hook
            requeued = self._persistence.requeue_blocked(hook.plugin_id)
        if requeued:
            self.wake()
        log_operation(
            logger,
            "hook.set",
            "register",
            phase="completed",
            tree_id=self.tree_id,
            hook_id=hook.id,
            event=hook.event,
            plugin_id=hook.plugin_id,
            root_only=hook.root_only,
            matcher=hook.matcher,
            failure_policy=hook.failure_policy,
            config=dict(hook.config),
            enabled=hook.enabled,
            requeued=requeued,
        )

        def unsubscribe() -> None:
            self.unregister(hook.id)

        return unsubscribe

    def bind_plugin(
        self,
        plugin_id: str,
        plugin: HookPlugin,
        *,
        replace: bool = False,
    ) -> None:
        """Attach an implementation to bindings restored from storage."""

        log_operation(
            logger,
            "hook.set",
            "bind_plugin",
            phase="requested",
            tree_id=self.tree_id,
            plugin_id=plugin_id,
            replace=replace,
        )
        self._plugins.register(plugin_id, plugin, replace=replace)
        requeued = self._persistence.requeue_blocked(plugin_id)
        if requeued:
            self.wake()
        log_operation(
            logger,
            "hook.set",
            "bind_plugin",
            phase="completed",
            tree_id=self.tree_id,
            plugin_id=plugin_id,
            replace=replace,
            requeued=requeued,
        )

    def unregister(self, hook_id: str) -> bool:
        normalized_id = str(hook_id)
        with self._lock:
            hook = self._hooks.get(normalized_id)
            removed = self._persistence.delete_hook(normalized_id)
            self._hooks.pop(normalized_id, None)
            release_plugin = bool(
                hook is not None
                and all(
                    item.plugin_id != hook.plugin_id
                    for item in self._hooks.values()
                )
            )
        if release_plugin and hook is not None:
            self._plugins.unregister(hook.plugin_id)
        log_operation(
            logger,
            "hook.set",
            "unregister",
            phase="completed",
            tree_id=self.tree_id,
            hook_id=normalized_id,
            removed=removed,
        )
        return removed

    def set_before_dispatch(self, callback: Callable[[], Any] | None) -> None:
        """Install a session-owned freshness barrier before Hook snapshots."""

        if callback is not None and not callable(callback):
            raise TypeError("before-dispatch callback must be callable or None")
        with self._lock:
            self._before_dispatch = callback

    async def _prepare_dispatch(self) -> None:
        with self._lock:
            callback = self._before_dispatch
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    def list(self, event: str | None = None) -> tuple[Hook, ...]:
        with self._lock:
            hooks = tuple(self._hooks.values())
        if event is None:
            result = hooks
        else:
            result = tuple(hook for hook in hooks if hook.event == str(event))
        log_operation(
            logger,
            "hook.set",
            "list",
            phase="completed",
            tree_id=self.tree_id,
            event=event,
            count=len(result),
            hooks=[
                {"hook_id": hook.id, "event": hook.event, "plugin_id": hook.plugin_id}
                for hook in result
            ],
        )
        return result

    def refresh_overrides(self, *, hook_id: str = "", plugin_id: str = "") -> int:
        """Apply product-level overrides to live bindings without restarting sessions."""

        changed = 0
        with self._lock:
            for key, hook in tuple(self._hooks.items()):
                if hook_id and hook.id != str(hook_id):
                    continue
                if plugin_id and hook.plugin_id != str(plugin_id):
                    continue
                updated = _hook_with_configured_override(hook)
                if updated != hook:
                    if updated.id != key and updated.id in self._hooks:
                        logger.warning(
                            "Ignoring Hook override id collision in tree %s: %s",
                            self.tree_id,
                            updated.id,
                        )
                        continue
                    if updated.id != key:
                        self._hooks.pop(key, None)
                    self._hooks[updated.id] = updated
                    changed += 1
        return changed

    @staticmethod
    def _tool_name(event: HookEvent) -> str:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else None
        return str(tool.get("name") or "") if isinstance(tool, Mapping) else ""

    def _snapshot(self, event: HookEvent) -> tuple[Hook, ...]:
        with self._lock:
            hooks = tuple(self._hooks.values())
        selected = tuple(
            hook
            for hook in hooks
            if hook.enabled
            and hook.event == event.name
            and (not hook.root_only or event.is_root)
            and (
                hook.matcher is None
                or fnmatch.fnmatchcase(self._tool_name(event), hook.matcher)
            )
        )
        if event.name == PRE_TOOL_USE:
            # Restrictive/argument-normalizing Hooks run first. The fixed core
            # permission reviewer must see the final arguments and therefore
            # remains the last semantic approval step regardless of persistent
            # registration order.
            return tuple(sorted(
                selected,
                key=lambda hook: hook.plugin_id == "core.permission",
            ))
        return selected

    def _plugin_for_hook(self, hook: Hook) -> HookPlugin:
        plugin = None
        action_provider = _hook_action_provider
        if action_provider is not None:
            try:
                plugin = action_provider(hook)
            except Exception:
                logger.warning(
                    "Ignoring invalid configured action for Hook %s",
                    hook.id,
                    exc_info=True,
                )
        if plugin is None:
            plugin = self._plugins.resolve(hook.plugin_id)
        if plugin is None:
            raise HookError(f"Plugin is not registered: {hook.plugin_id}")
        return plugin

    async def _call(self, hook: Hook, event: HookEvent) -> Any:
        with operation(
            logger,
            "hook.set",
            "invoke",
            tree_id=self.tree_id,
            hook_id=hook.id,
            plugin_id=hook.plugin_id,
            event=event.name,
            node_id=event.node_id,
            is_root=event.is_root,
            payload=event.payload,
            failure_policy=hook.failure_policy,
        ) as op:
            plugin = self._plugin_for_hook(hook)
            value = plugin(event)
            if inspect.isawaitable(value):
                value = await value
            op.finish(result=value)
            return value

    async def _call_batch(
        self,
        hook: Hook,
        events: Sequence[HookEvent],
    ) -> tuple[Any, ...]:
        normalized = tuple(events)
        plugin = self._plugin_for_hook(hook)
        batch = getattr(plugin, "review_batch", None)
        if not callable(batch):
            return tuple([await self._call(hook, event) for event in normalized])
        with operation(
            logger,
            "hook.set",
            "invoke_batch",
            tree_id=self.tree_id,
            hook_id=hook.id,
            plugin_id=hook.plugin_id,
            event=PRE_TOOL_USE,
            count=len(normalized),
            failure_policy=hook.failure_policy,
        ) as op:
            values = batch(normalized)
            if inspect.isawaitable(values):
                values = await values
            if (
                not isinstance(values, Sequence)
                or isinstance(values, (str, bytes, bytearray))
                or len(values) != len(normalized)
            ):
                raise HookError("Batch Hook must return one result per event")
            result = tuple(values)
            op.finish(result=result)
            return result

    async def _dispatch_snapshot(
        self,
        hooks: tuple[Hook, ...],
        event: HookEvent,
    ) -> tuple[Any, ...]:
        with operation(
            logger,
            "hook.set",
            "dispatch",
            tree_id=self.tree_id,
            event=event.name,
            node_id=event.node_id,
            is_root=event.is_root,
            payload=event.payload,
            hooks=[hook.id for hook in hooks],
        ) as op:
            results: list[Any] = []
            failed: list[str] = []
            for hook in hooks:
                try:
                    results.append(await self._call(hook, event))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if hook.failure_policy == "closed":
                        log_operation(
                            logger,
                            "hook.set",
                            "dispatch_hook",
                            phase="failed_closed",
                            level=logging.ERROR,
                            exc_info=True,
                            tree_id=self.tree_id,
                            hook_id=hook.id,
                            plugin_id=hook.plugin_id,
                            event=event.name,
                            error=exc,
                        )
                        raise HookError(f"{hook.id}: {exc}") from exc
                    failed.append(hook.id)
                    log_operation(
                        logger,
                        "hook.set",
                        "dispatch_hook",
                        phase="failed_open",
                        level=logging.ERROR,
                        exc_info=True,
                        tree_id=self.tree_id,
                        hook_id=hook.id,
                        plugin_id=hook.plugin_id,
                        event=event.name,
                        error=exc,
                    )
            op.finish(result_count=len(results), failed_hooks=failed, results=results)
            return tuple(results)

    async def _run_pre_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        time: datetime,
        *,
        permission: Mapping[str, Any] | None = None,
        skip_plugin_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        await self._prepare_dispatch()
        with operation(
            logger,
            "hook.set",
            "pre_tool_use",
            tree_id=self.tree_id,
            tool=name,
            arguments=arguments,
            event_time=time,
        ) as op:
            current = dict(arguments)
            event_payload = {
                "tool": {"name": name, "arguments": dict(current)},
                "permission": dict(permission) if permission is not None else None,
            }
            probe = HookEvent(
                PRE_TOOL_USE,
                self.tree_id,
                time,
                payload=event_payload,
            )
            hooks = tuple(
                hook
                for hook in self._snapshot(probe)
                if hook.plugin_id not in skip_plugin_ids
            )
            decisions: list[dict[str, Any]] = []
            for hook in hooks:
                event = HookEvent(
                    PRE_TOOL_USE,
                    self.tree_id,
                    time,
                    payload={
                        "tool": {"name": name, "arguments": dict(current)},
                        "permission": (
                            dict(permission) if permission is not None else None
                        ),
                    },
                )
                try:
                    raw = await self._call(hook, event)
                    output = raw if isinstance(raw, Mapping) else {}
                    decision = str(output.get("decision") or "allow").strip().lower()
                    if decision == "ask":
                        raise HookAwaitingUser(output.get("question") or {})
                    if decision == "block":
                        raise HookBlocked(
                            str(output.get("reason") or hook.id or "tool call blocked")
                        )
                    if decision not in {"allow", "modify"}:
                        raise HookError("PreToolUse decision must be allow, modify, or block")
                    if "arguments" in output:
                        modified = output.get("arguments")
                        if not isinstance(modified, Mapping):
                            raise HookError("PreToolUse arguments must be an object")
                        current = dict(modified)
                    decisions.append(
                        {
                            "hook_id": hook.id,
                            "plugin_id": hook.plugin_id,
                            "decision": decision,
                            "arguments": dict(current),
                        }
                    )
                except (HookAwaitingUser, HookBlocked) as exc:
                    log_operation(
                        logger,
                        "hook.set",
                        "pre_tool_decision",
                        phase="blocked",
                        level=logging.WARNING,
                        tree_id=self.tree_id,
                        hook_id=hook.id,
                        plugin_id=hook.plugin_id,
                        tool=name,
                        arguments=current,
                        reason=exc,
                    )
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if hook.failure_policy == "block":
                        log_operation(
                            logger,
                            "hook.set",
                            "pre_tool_decision",
                            phase="failed_closed",
                            level=logging.ERROR,
                            exc_info=True,
                            tree_id=self.tree_id,
                            hook_id=hook.id,
                            plugin_id=hook.plugin_id,
                            tool=name,
                            error=exc,
                        )
                        raise HookBlocked(f"{hook.id}: {exc}") from exc
                    decisions.append(
                        {
                            "hook_id": hook.id,
                            "plugin_id": hook.plugin_id,
                            "decision": "failed_open",
                            "error": str(exc),
                        }
                    )
                    log_operation(
                        logger,
                        "hook.set",
                        "pre_tool_decision",
                        phase="failed_open",
                        level=logging.ERROR,
                        exc_info=True,
                        tree_id=self.tree_id,
                        hook_id=hook.id,
                        plugin_id=hook.plugin_id,
                        tool=name,
                        error=exc,
                    )
            op.finish(arguments=current, decisions=decisions, hook_count=len(hooks))
            return current

    async def _run_pre_tool_batch(
        self,
        calls: tuple[
            tuple[str, dict[str, Any], datetime, dict[str, Any] | None], ...
        ],
    ) -> tuple[dict[str, Any] | BaseException, ...]:
        """Run ordinary Hooks per call, then review all calls in one model batch."""

        with operation(
            logger,
            "hook.set",
            "pre_tool_use_batch",
            tree_id=self.tree_id,
            calls=[
                {"tool": name, "arguments": arguments, "permission": permission}
                for name, arguments, _time, permission in calls
            ],
        ) as op:
            results: list[dict[str, Any] | BaseException] = []
            for name, arguments, time, permission in calls:
                try:
                    results.append(await self._run_pre_tool(
                        name,
                        arguments,
                        time,
                        permission=permission,
                        skip_plugin_ids=frozenset({"core.permission"}),
                    ))
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    results.append(exc)

            grouped: dict[str, tuple[Hook, list[int]]] = {}
            for index, ((name, _arguments, time, permission), current) in enumerate(
                zip(calls, results)
            ):
                if isinstance(current, BaseException):
                    continue
                probe = HookEvent(
                    PRE_TOOL_USE,
                    self.tree_id,
                    time,
                    payload={
                        "tool": {"name": name, "arguments": dict(current)},
                        "permission": (
                            dict(permission) if permission is not None else None
                        ),
                    },
                )
                for hook in self._snapshot(probe):
                    if hook.plugin_id != "core.permission":
                        continue
                    group = grouped.get(hook.id)
                    if group is None:
                        grouped[hook.id] = (hook, [index])
                    else:
                        group[1].append(index)

            for hook, indices in grouped.values():
                events = tuple(
                    HookEvent(
                        PRE_TOOL_USE,
                        self.tree_id,
                        calls[index][2],
                        payload={
                            "tool": {
                                "name": calls[index][0],
                                "arguments": dict(results[index]),
                            },
                            "permission": (
                                dict(calls[index][3])
                                if calls[index][3] is not None
                                else None
                            ),
                        },
                    )
                    for index in indices
                    if not isinstance(results[index], BaseException)
                )
                try:
                    outputs = await self._call_batch(hook, events)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    for index in indices:
                        if hook.failure_policy == "block":
                            results[index] = HookBlocked(f"{hook.id}: {exc}")
                    log_operation(
                        logger,
                        "hook.set",
                        "pre_tool_batch_decision",
                        phase="failed_closed" if hook.failure_policy == "block" else "failed_open",
                        level=logging.ERROR,
                        exc_info=True,
                        tree_id=self.tree_id,
                        hook_id=hook.id,
                        plugin_id=hook.plugin_id,
                        error=exc,
                    )
                    continue

                for index, raw in zip(indices, outputs):
                    current = results[index]
                    if isinstance(current, BaseException):
                        continue
                    try:
                        output = raw if isinstance(raw, Mapping) else {}
                        decision = str(output.get("decision") or "allow").strip().lower()
                        if decision == "ask":
                            raise HookAwaitingUser(output.get("question") or {})
                        if decision == "block":
                            raise HookBlocked(
                                str(output.get("reason") or hook.id or "tool call blocked")
                            )
                        if decision not in {"allow", "modify"}:
                            raise HookError(
                                "PreToolUse decision must be allow, modify, or block"
                            )
                        if "arguments" in output:
                            modified = output.get("arguments")
                            if not isinstance(modified, Mapping):
                                raise HookError("PreToolUse arguments must be an object")
                            results[index] = dict(modified)
                    except (HookAwaitingUser, HookBlocked) as exc:
                        results[index] = exc
                        log_operation(
                            logger,
                            "hook.set",
                            "pre_tool_batch_decision",
                            phase="blocked",
                            level=logging.WARNING,
                            tree_id=self.tree_id,
                            hook_id=hook.id,
                            plugin_id=hook.plugin_id,
                            tool=calls[index][0],
                            reason=exc,
                        )
                    except Exception as exc:
                        if hook.failure_policy == "block":
                            results[index] = HookBlocked(f"{hook.id}: {exc}")
                        log_operation(
                            logger,
                            "hook.set",
                            "pre_tool_batch_decision",
                            phase=(
                                "failed_closed"
                                if hook.failure_policy == "block"
                                else "failed_open"
                            ),
                            level=logging.ERROR,
                            exc_info=True,
                            tree_id=self.tree_id,
                            hook_id=hook.id,
                            plugin_id=hook.plugin_id,
                            tool=calls[index][0],
                            error=exc,
                        )

            normalized_results = tuple(results)
            op.finish(
                result_count=len(normalized_results),
                rejected=sum(
                    isinstance(result, BaseException)
                    for result in normalized_results
                ),
                results=normalized_results,
            )
            return normalized_results

    async def _drain_persisted(self) -> None:
        await self._prepare_dispatch()
        processed = 0
        failed = 0
        blocked = 0
        log_operation(
            logger,
            "hook.set",
            "drain_persisted",
            phase="started",
            tree_id=self.tree_id,
        )
        while True:
            delivery = self._persistence.claim_next()
            if delivery is None:
                log_operation(
                    logger,
                    "hook.set",
                    "drain_persisted",
                    phase="completed",
                    tree_id=self.tree_id,
                    processed=processed,
                    failed=failed,
                    blocked=blocked,
                )
                return
            if self._plugins.resolve(delivery.hook.plugin_id) is None:
                blocked += 1
                self._persistence.block(
                    delivery.sequence,
                    f"Plugin is not registered: {delivery.hook.plugin_id}",
                )
                log_operation(
                    logger,
                    "hook.set",
                    "deliver_persisted",
                    phase="blocked",
                    level=logging.WARNING,
                    tree_id=self.tree_id,
                    sequence=delivery.sequence,
                    hook_id=delivery.hook.id,
                    plugin_id=delivery.hook.plugin_id,
                    event=delivery.event.name,
                    reason="plugin_not_registered",
                )
                continue
            try:
                await self._call(delivery.hook, delivery.event)
            except asyncio.CancelledError:
                self._persistence.release(delivery.sequence)
                log_operation(
                    logger,
                    "hook.set",
                    "deliver_persisted",
                    phase="cancelled",
                    level=logging.WARNING,
                    tree_id=self.tree_id,
                    sequence=delivery.sequence,
                    hook_id=delivery.hook.id,
                    plugin_id=delivery.hook.plugin_id,
                    event=delivery.event.name,
                    released=True,
                )
                raise
            except Exception as exc:
                failed += 1
                self._persistence.fail(delivery.sequence, str(exc))
                log_operation(
                    logger,
                    "hook.set",
                    "deliver_persisted",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    tree_id=self.tree_id,
                    sequence=delivery.sequence,
                    hook_id=delivery.hook.id,
                    plugin_id=delivery.hook.plugin_id,
                    event=delivery.event.name,
                    message="Hook Plugin failed",
                    error=exc,
                )
            else:
                processed += 1
                self._persistence.complete(delivery.sequence)
                log_operation(
                    logger,
                    "hook.set",
                    "deliver_persisted",
                    phase="completed",
                    tree_id=self.tree_id,
                    sequence=delivery.sequence,
                    hook_id=delivery.hook.id,
                    plugin_id=delivery.hook.plugin_id,
                    event=delivery.event.name,
                    attempts=delivery.attempts,
                )

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        log_operation(
            logger,
            "hook.set",
            "worker",
            phase="started",
            tree_id=self.tree_id,
            thread=threading.current_thread().name,
        )
        try:
            while True:
                item = self._work.get()
                if item == "stop":
                    return
                if item == "wake":
                    with self._lock:
                        self._wake_enqueued = False
                    loop.run_until_complete(self._drain_persisted())
                elif isinstance(item, _DispatchRequest):
                    try:
                        result = loop.run_until_complete(
                            self._dispatch_current(item.event)
                        )
                    except BaseException as exc:
                        _settle_future(item.future, error=exc)
                    else:
                        _settle_future(item.future, result=result)
                elif isinstance(item, _PreToolRequest):
                    try:
                        result = loop.run_until_complete(
                            self._run_pre_tool(
                                item.name,
                                item.arguments,
                                item.time,
                                permission=item.permission,
                            )
                        )
                    except BaseException as exc:
                        _settle_future(item.future, error=exc)
                    else:
                        _settle_future(item.future, result=result)
                elif isinstance(item, _PreToolBatchRequest):
                    try:
                        result = loop.run_until_complete(
                            self._run_pre_tool_batch(item.calls)
                        )
                    except BaseException as exc:
                        _settle_future(item.future, error=exc)
                    else:
                        _settle_future(item.future, result=result)
                elif isinstance(item, _Barrier):
                    loop.run_until_complete(self._drain_persisted())
                    _settle_future(item.future)
        except BaseException as exc:
            log_operation(
                logger,
                "hook.set",
                "worker",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                tree_id=self.tree_id,
                thread=threading.current_thread().name,
                error=exc,
            )
            raise
        finally:
            loop.close()
            log_operation(
                logger,
                "hook.set",
                "worker",
                phase="stopped",
                tree_id=self.tree_id,
                thread=threading.current_thread().name,
            )

    def wake(self) -> None:
        """Schedule all pending persistent deliveries in sequence order."""

        with self._lock:
            if self._closed or self._wake_enqueued:
                return
            if not self._persistence.has_work():
                return
            self._ensure_worker_locked()
            self._wake_enqueued = True
            self._work.put("wake")
        log_operation(
            logger,
            "hook.set",
            "wake",
            phase="queued",
            tree_id=self.tree_id,
        )

    async def dispatch(self, event: HookEvent) -> tuple[Any, ...]:
        if event.tree_id != self.tree_id:
            raise HookError(
                f"Hook event tree mismatch: expected {self.tree_id}, got {event.tree_id}"
            )
        if self._in_worker_thread():
            return await self._dispatch_current(event)
        future: Future[tuple[Any, ...]] = Future()
        with self._lock:
            self._ensure_open()
            self._ensure_worker_locked()
            self._work.put(_DispatchRequest(event, future))
        log_operation(
            logger,
            "hook.set",
            "dispatch",
            phase="queued",
            tree_id=self.tree_id,
            event=event.name,
            node_id=event.node_id,
            payload=event.payload,
        )
        return await asyncio.wrap_future(future)

    async def _dispatch_current(self, event: HookEvent) -> tuple[Any, ...]:
        await self._prepare_dispatch()
        return await self._dispatch_snapshot(self._snapshot(event), event)

    def dispatch_nowait(self, event: HookEvent) -> Future[tuple[Any, ...]] | None:
        if event.tree_id != self.tree_id:
            raise HookError(
                f"Hook event tree mismatch: expected {self.tree_id}, got {event.tree_id}"
            )
        future: Future[tuple[Any, ...]] = Future()
        with self._lock:
            if self._closed:
                log_operation(
                    logger,
                    "hook.set",
                    "dispatch_nowait",
                    phase="skipped",
                    tree_id=self.tree_id,
                    event=event.name,
                    reason="closed",
                )
                return None
            self._ensure_worker_locked()
            self._work.put(_DispatchRequest(event, future))
        log_operation(
            logger,
            "hook.set",
            "dispatch_nowait",
            phase="queued",
            tree_id=self.tree_id,
            event=event.name,
            node_id=event.node_id,
            payload=event.payload,
        )
        return future

    async def drain(self) -> None:
        future: Future[None] = Future()
        with self._lock:
            if self._closed:
                return
            self._ensure_worker_locked()
            self._work.put(_Barrier(future))
        log_operation(
            logger,
            "hook.set",
            "drain",
            phase="started",
            tree_id=self.tree_id,
        )
        await asyncio.wrap_future(future)
        log_operation(
            logger,
            "hook.set",
            "drain",
            phase="completed",
            tree_id=self.tree_id,
        )

    def retry_failed(self) -> int:
        count = self._persistence.retry_failed()
        if count:
            self.wake()
        log_operation(
            logger,
            "hook.set",
            "retry_failed",
            phase="completed",
            tree_id=self.tree_id,
            count=count,
        )
        return count

    def context_changed(self, change: Any) -> Future[tuple[Any, ...]] | None:
        event = HookEvent(
            CONTEXT_CHANGE,
            self.tree_id,
            getattr(change, "time", _utc_now()),
            payload=change,
            node_id=str(getattr(change, "node_id", "") or "") or None,
            is_root=str(getattr(change, "node_id", "") or "") == self.root_id,
        )
        return self.dispatch_nowait(event)

    def context_used(self, usage: Any) -> Future[tuple[Any, ...]] | None:
        event = HookEvent(
            CONTEXT_USED,
            self.tree_id,
            getattr(usage, "time", _utc_now()),
            payload=usage,
            node_id=str(getattr(usage, "node_id", "") or "") or None,
            is_root=str(getattr(usage, "node_id", "") or "") == self.root_id,
        )
        return self.dispatch_nowait(event)

    async def pre_tool_use(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        permission: Mapping[str, Any] | None = None,
        time: datetime | None = None,
    ) -> dict[str, Any]:
        if self._in_worker_thread():
            return await self._run_pre_tool(
                str(name),
                dict(arguments),
                time or _utc_now(),
                permission=permission,
            )
        future: Future[dict[str, Any]] = Future()
        with self._lock:
            self._ensure_open()
            self._ensure_worker_locked()
            self._work.put(
                _PreToolRequest(
                    str(name),
                    dict(arguments),
                    dict(permission) if permission is not None else None,
                    time or _utc_now(),
                    future,
                )
            )
        return await asyncio.wrap_future(future)

    async def pre_tool_use_batch(
        self,
        calls: tuple[tuple[str, Mapping[str, Any]], ...],
        *,
        permissions: Sequence[Mapping[str, Any] | None] | None = None,
        time: datetime | None = None,
    ) -> tuple[dict[str, Any] | BaseException, ...]:
        """Queue one batch, then run its independent reviews concurrently."""

        reviewed_at = time or _utc_now()
        permission_values = tuple(permissions or (None for _call in calls))
        if len(permission_values) != len(calls):
            raise ValueError("permissions must match the tool call count")
        normalized = tuple(
            (
                str(name),
                dict(arguments),
                reviewed_at,
                dict(permission) if permission is not None else None,
            )
            for (name, arguments), permission in zip(calls, permission_values)
        )
        if self._in_worker_thread():
            return await self._run_pre_tool_batch(normalized)
        future: Future[tuple[dict[str, Any] | BaseException, ...]] = Future()
        with self._lock:
            self._ensure_open()
            self._ensure_worker_locked()
            self._work.put(_PreToolBatchRequest(normalized, future))
        return await asyncio.wrap_future(future)

    async def post_tool_use(
        self,
        name: str,
        arguments: Mapping[str, Any],
        result: Any,
        *,
        success: bool,
        error: str = "",
        time: datetime | None = None,
    ) -> tuple[Any, ...]:
        return await self.dispatch(
            HookEvent(
                POST_TOOL_USE,
                self.tree_id,
                time or _utc_now(),
                payload={
                    "tool": {"name": str(name), "arguments": dict(arguments)},
                    "result": {
                        "success": bool(success),
                        "value": result,
                        "error": str(error or ""),
                    },
                },
            )
        )

    async def session_start(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> str:
        mounts = await self.session_start_mounts(details, time=time)
        return "\n\n".join(str(mount["context"]) for mount in mounts)

    async def session_start_mounts(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Return ordered context contributions with their mount positions."""

        event = HookEvent(
            SESSION_START,
            self.tree_id,
            time or _utc_now(),
            payload=dict(details or {}),
            node_id=self.root_id,
            is_root=True,
        )
        contexts: list[tuple[int, int, dict[str, str]]] = []
        for index, result in enumerate(await self.dispatch(event)):
            if isinstance(result, Mapping):
                value = str(result.get("context") or "").strip()
                position = str(result.get("context_position") or "").strip()
                context_kind = str(result.get("context_kind") or "").strip()
                context_source = str(result.get("context_source") or "").strip()
            else:
                value = str(result or "").strip()
                position = ""
                context_kind = ""
                context_source = ""
            if value:
                priority = (
                    -1
                    if position == "system"
                    else (0 if position == "top" else 1)
                )
                contexts.append((
                    priority,
                    index,
                    {
                        "context": value,
                        "position": position,
                        "context_kind": context_kind,
                        "context_source": context_source,
                    },
                ))
        return tuple(value for _priority, _index, value in sorted(contexts))

    async def session_start_fingerprints(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Collect optional opaque cache dependencies from SessionStart Hooks."""

        await self._prepare_dispatch()
        event = HookEvent(
            SESSION_START,
            self.tree_id,
            time or _utc_now(),
            payload=dict(details or {}),
            node_id=self.root_id,
            is_root=True,
        )
        values: list[dict[str, Any]] = []
        for hook in self._snapshot(event):
            plugin = self._plugins.resolve(hook.plugin_id)
            provider = getattr(plugin, "session_start_cache_fingerprint", None)
            if not callable(provider):
                owner = getattr(plugin, "__self__", None)
                provider = getattr(owner, "session_start_cache_fingerprint", None)
            if not callable(provider):
                continue
            try:
                value = provider(event)
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:
                value = {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            values.append({"hook_id": hook.id, "value": value})
        return tuple(sorted(values, key=lambda item: str(item["hook_id"])))

    async def turn_start(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> str:
        mounts = await self.turn_start_mounts(details, time=time)
        return "\n\n".join(str(mount["context"]) for mount in mounts)

    async def turn_start_mounts(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Return ordered context contributions for the current user turn."""

        event = HookEvent(
            TURN_START,
            self.tree_id,
            time or _utc_now(),
            payload=dict(details or {}),
            node_id=self.root_id,
            is_root=True,
        )
        contexts: list[tuple[int, int, dict[str, str]]] = []
        for index, result in enumerate(await self.dispatch(event)):
            if isinstance(result, Mapping):
                value = str(result.get("context") or "").strip()
                position = str(result.get("context_position") or "").strip()
                context_kind = str(result.get("context_kind") or "").strip()
                context_source = str(result.get("context_source") or "").strip()
            else:
                value = str(result or "").strip()
                position = ""
                context_kind = ""
                context_source = ""
            if value:
                priority = -1 if position == "system" else (0 if position == "top" else 1)
                contexts.append((
                    priority,
                    index,
                    {
                        "context": value,
                        "position": position,
                        "context_kind": context_kind,
                        "context_source": context_source,
                    },
                ))
        return tuple(value for _priority, _index, value in sorted(contexts))

    async def session_end(
        self,
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> tuple[Any, ...]:
        return await self.dispatch(
            HookEvent(
                SESSION_END,
                self.tree_id,
                time or _utc_now(),
                payload=dict(details or {}),
                node_id=self.root_id,
                is_root=True,
            )
        )

    async def stop(
        self,
        reason: str = "",
        details: Mapping[str, Any] | None = None,
        *,
        time: datetime | None = None,
    ) -> tuple[Any, ...]:
        payload = dict(details or {})
        payload["reason"] = str(reason or "")
        return await self.dispatch(
            HookEvent(
                STOP,
                self.tree_id,
                time or _utc_now(),
                payload=payload,
                node_id=self.root_id,
                is_root=True,
            )
        )

    def close(self, *, cancel_pending: bool = False, wait: bool = True) -> None:
        cancelled = 0
        with self._lock:
            if self._closed:
                log_operation(
                    logger,
                    "hook.set",
                    "close",
                    phase="skipped",
                    tree_id=self.tree_id,
                    reason="already_closed",
                )
                return
            self._closed = True
            thread = self._thread
            if thread is not None and cancel_pending:
                while True:
                    try:
                        item = self._work.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(
                        item,
                        (_DispatchRequest, _PreToolRequest, _PreToolBatchRequest, _Barrier),
                    ):
                        item.future.cancel()
                        cancelled += 1
            if thread is not None:
                self._work.put("stop")
        if thread is not None and wait and thread is not threading.current_thread():
            thread.join()
        with _active_hook_sets_lock:
            _active_hook_sets.discard(self)
        log_operation(
            logger,
            "hook.set",
            "close",
            phase="completed",
            tree_id=self.tree_id,
            cancel_pending=cancel_pending,
            cancelled=cancelled,
            waited=bool(thread is not None and wait),
        )
