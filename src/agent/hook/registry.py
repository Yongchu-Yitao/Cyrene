"""Persistent, tree-local Hook registration and ordered execution."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .errors import HookBlocked, HookError
from .hook import (
    CONTEXT_CHANGE,
    CONTEXT_USED,
    HOOK_EVENTS,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    Hook,
    HookEvent,
    HookPlugin,
)
from .plugin import PluginRegistry
from .storage import HookPersistence, validate_hook_config

logger = logging.getLogger(__name__)


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
    time: datetime
    future: Future[dict[str, Any]]


@dataclass(slots=True)
class _PreToolBatchRequest:
    calls: tuple[tuple[str, dict[str, Any], datetime], ...]
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
        if event not in HOOK_EVENTS:
            raise HookError(f"unsupported Hook event: {event}")
        if not callable(plugin):
            raise TypeError("Hook plugin must be callable")
        if matcher is not None and not isinstance(matcher, str):
            raise TypeError("persistent Hook matcher must be a glob string")
        if failure_policy not in {"open", "block"}:
            raise ValueError("failure_policy must be 'open' or 'block'")
        if failure_policy == "block" and event != PRE_TOOL_USE:
            raise ValueError("only PreToolUse Hooks may block on failure")
        normalized_id = str(hook_id or self._new_hook_id()).strip()
        normalized_plugin_id = str(plugin_id or normalized_id).strip()
        if not normalized_id:
            raise ValueError("hook_id cannot be empty")
        if not normalized_plugin_id:
            raise ValueError("plugin_id cannot be empty")
        hook = Hook(
            id=normalized_id,
            event=event,
            plugin_id=normalized_plugin_id,
            root_only=bool(root_only),
            matcher=matcher,
            failure_policy=failure_policy,  # type: ignore[arg-type]
            config=validate_hook_config(config or {}),
            enabled=bool(enabled),
            created_at=_utc_now(),
        )
        with self._lock:
            self._ensure_open()
            if normalized_id in self._hooks:
                raise HookError(f"Hook id already exists in tree {self.tree_id}: {normalized_id}")
            self._plugins.register(normalized_plugin_id, plugin)
            self._persistence.save_hook(hook)
            self._hooks[normalized_id] = hook
            requeued = self._persistence.requeue_blocked(normalized_plugin_id)
        if requeued:
            self.wake()

        def unsubscribe() -> None:
            self.unregister(normalized_id)

        return unsubscribe

    def bind_plugin(
        self,
        plugin_id: str,
        plugin: HookPlugin,
        *,
        replace: bool = False,
    ) -> None:
        """Attach an implementation to bindings restored from storage."""

        self._plugins.register(plugin_id, plugin, replace=replace)
        if self._persistence.requeue_blocked(plugin_id):
            self.wake()

    def unregister(self, hook_id: str) -> bool:
        normalized_id = str(hook_id)
        with self._lock:
            removed = self._persistence.delete_hook(normalized_id)
            self._hooks.pop(normalized_id, None)
            return removed

    def list(self, event: str | None = None) -> tuple[Hook, ...]:
        with self._lock:
            hooks = tuple(self._hooks.values())
        if event is None:
            return hooks
        return tuple(hook for hook in hooks if hook.event == str(event))

    @staticmethod
    def _tool_name(event: HookEvent) -> str:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else None
        return str(tool.get("name") or "") if isinstance(tool, Mapping) else ""

    def _snapshot(self, event: HookEvent) -> tuple[Hook, ...]:
        with self._lock:
            hooks = tuple(self._hooks.values())
        return tuple(
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

    async def _call(self, hook: Hook, event: HookEvent) -> Any:
        plugin = self._plugins.resolve(hook.plugin_id)
        if plugin is None:
            raise HookError(f"Plugin is not registered: {hook.plugin_id}")
        value = plugin(event)
        if inspect.isawaitable(value):
            return await value
        return value

    async def _dispatch_snapshot(
        self,
        hooks: tuple[Hook, ...],
        event: HookEvent,
    ) -> tuple[Any, ...]:
        results: list[Any] = []
        for hook in hooks:
            try:
                results.append(await self._call(hook, event))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Hook Plugin failed (tree=%s, hook=%s, event=%s)",
                    self.tree_id,
                    hook.id,
                    event.name,
                )
        return tuple(results)

    async def _run_pre_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        time: datetime,
    ) -> dict[str, Any]:
        current = dict(arguments)
        probe = HookEvent(
            PRE_TOOL_USE,
            self.tree_id,
            time,
            payload={"tool": {"name": name, "arguments": dict(current)}},
        )
        for hook in self._snapshot(probe):
            event = HookEvent(
                PRE_TOOL_USE,
                self.tree_id,
                time,
                payload={"tool": {"name": name, "arguments": dict(current)}},
            )
            try:
                raw = await self._call(hook, event)
                output = raw if isinstance(raw, Mapping) else {}
                decision = str(output.get("decision") or "allow").strip().lower()
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
            except HookBlocked:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if hook.failure_policy == "block":
                    raise HookBlocked(f"{hook.id}: {exc}") from exc
                logger.exception(
                    "PreToolUse Hook failed open (tree=%s, hook=%s)",
                    self.tree_id,
                    hook.id,
                )
        return current

    async def _run_pre_tool_batch(
        self,
        calls: tuple[tuple[str, dict[str, Any], datetime], ...],
    ) -> tuple[dict[str, Any] | BaseException, ...]:
        """Review one model-produced batch concurrently inside one queue slot."""

        async def review(
            name: str,
            arguments: dict[str, Any],
            time: datetime,
        ) -> dict[str, Any] | BaseException:
            try:
                return await self._run_pre_tool(name, arguments, time)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                return exc

        return tuple(
            await asyncio.gather(
                *(review(name, arguments, time) for name, arguments, time in calls)
            )
        )

    async def _drain_persisted(self) -> None:
        while True:
            delivery = self._persistence.claim_next()
            if delivery is None:
                return
            if self._plugins.resolve(delivery.hook.plugin_id) is None:
                self._persistence.block(
                    delivery.sequence,
                    f"Plugin is not registered: {delivery.hook.plugin_id}",
                )
                continue
            try:
                await self._call(delivery.hook, delivery.event)
            except asyncio.CancelledError:
                self._persistence.release(delivery.sequence)
                raise
            except Exception as exc:
                self._persistence.fail(delivery.sequence, str(exc))
                logger.exception(
                    "Queued Hook Plugin failed (tree=%s, hook=%s, event=%s)",
                    self.tree_id,
                    delivery.hook.id,
                    delivery.event.name,
                )
            else:
                self._persistence.complete(delivery.sequence)

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
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
                            self._dispatch_snapshot(self._snapshot(item.event), item.event)
                        )
                    except BaseException as exc:
                        _settle_future(item.future, error=exc)
                    else:
                        _settle_future(item.future, result=result)
                elif isinstance(item, _PreToolRequest):
                    try:
                        result = loop.run_until_complete(
                            self._run_pre_tool(item.name, item.arguments, item.time)
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
        finally:
            loop.close()

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

    async def dispatch(self, event: HookEvent) -> tuple[Any, ...]:
        if event.tree_id != self.tree_id:
            raise HookError(
                f"Hook event tree mismatch: expected {self.tree_id}, got {event.tree_id}"
            )
        if self._in_worker_thread():
            return await self._dispatch_snapshot(self._snapshot(event), event)
        future: Future[tuple[Any, ...]] = Future()
        with self._lock:
            self._ensure_open()
            self._ensure_worker_locked()
            self._work.put(_DispatchRequest(event, future))
        return await asyncio.wrap_future(future)

    def dispatch_nowait(self, event: HookEvent) -> Future[tuple[Any, ...]] | None:
        if event.tree_id != self.tree_id:
            raise HookError(
                f"Hook event tree mismatch: expected {self.tree_id}, got {event.tree_id}"
            )
        future: Future[tuple[Any, ...]] = Future()
        with self._lock:
            if self._closed:
                return None
            self._ensure_worker_locked()
            self._work.put(_DispatchRequest(event, future))
        return future

    async def drain(self) -> None:
        future: Future[None] = Future()
        with self._lock:
            if self._closed:
                return
            self._ensure_worker_locked()
            self._work.put(_Barrier(future))
        await asyncio.wrap_future(future)

    def retry_failed(self) -> int:
        count = self._persistence.retry_failed()
        if count:
            self.wake()
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
        time: datetime | None = None,
    ) -> dict[str, Any]:
        if self._in_worker_thread():
            return await self._run_pre_tool(
                str(name),
                dict(arguments),
                time or _utc_now(),
            )
        future: Future[dict[str, Any]] = Future()
        with self._lock:
            self._ensure_open()
            self._ensure_worker_locked()
            self._work.put(
                _PreToolRequest(str(name), dict(arguments), time or _utc_now(), future)
            )
        return await asyncio.wrap_future(future)

    async def pre_tool_use_batch(
        self,
        calls: tuple[tuple[str, Mapping[str, Any]], ...],
        *,
        time: datetime | None = None,
    ) -> tuple[dict[str, Any] | BaseException, ...]:
        """Queue one batch, then run its independent reviews concurrently."""

        reviewed_at = time or _utc_now()
        normalized = tuple(
            (str(name), dict(arguments), reviewed_at) for name, arguments in calls
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
        event = HookEvent(
            SESSION_START,
            self.tree_id,
            time or _utc_now(),
            payload=dict(details or {}),
            node_id=self.root_id,
            is_root=True,
        )
        contexts: list[str] = []
        for result in await self.dispatch(event):
            if isinstance(result, Mapping):
                value = str(result.get("context") or "").strip()
            else:
                value = str(result or "").strip()
            if value:
                contexts.append(value)
        return "\n\n".join(contexts)

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
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is None:
                return
            if cancel_pending:
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
            self._work.put("stop")
        if wait and thread is not threading.current_thread():
            thread.join()
