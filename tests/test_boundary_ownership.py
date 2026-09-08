"""Fault injection for worker, thread, review and publication ownership."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from cyrene.core.context import ContextStoreRouter
from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry, PluginRuntime
from cyrene.workbench.core_adapter.publication import PublicationQueue
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime


def registry_for(handler, *, timeout=None, boundary=None):
    registry = PluginRegistry()
    registry.register_pack(PluginPack("test", "test", (
        Plugin("Probe", "test", {"type": "object"}, handler,
               timeout_seconds=timeout, permission_boundary=boundary),
    )), source="test")
    return registry


@pytest.mark.asyncio
async def test_worker_failure_settles_active_and_queued_barriers_then_recovers(tmp_path, monkeypatch):
    store = ContextStoreRouter(tmp_path)
    tree = store.create_tree(tree_id="test", root_id="root")
    hooks = store.hooks_for(tree.id)
    await hooks.drain()
    original = hooks._drain_persisted
    entered, release = threading.Event(), threading.Event()
    async def fail():
        entered.set()
        await asyncio.to_thread(release.wait)
        raise OSError("storage unavailable")
    monkeypatch.setattr(hooks, "_drain_persisted", fail)
    try:
        first = asyncio.create_task(hooks.drain())
        assert await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(hooks.drain())
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        assert all(isinstance(result, OSError) for result in results)
        monkeypatch.setattr(hooks, "_drain_persisted", original)
        await asyncio.wait_for(hooks.drain(), 1)
    finally:
        release.set()
        store.close()


@pytest.mark.asyncio
async def test_sync_timeout_is_unknown_and_blocks_other_runtime_until_thread_finishes(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def handler(arguments, context):
        calls.append(True)
        entered.set()
        release.wait(2)
        finished.set()
        return "late effect"
    registry = registry_for(handler, timeout=.01)
    context = PluginContext(workspace=tmp_path)
    try:
        result = await PluginRuntime(registry).call("Probe", {}, context)
        assert entered.is_set() and not finished.is_set()
        assert result.failure.error_code == "plugin_execution_unknown"
        assert result.failure.retryable is False
        assert result.failure.details["replay_safe"] is False
        other = await PluginRuntime(registry).call("Probe", {}, context)
        assert other.failure.error_code == "plugin_execution_unknown"
        assert calls == [True]
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)


@pytest.mark.asyncio
async def test_cancelled_sync_waiter_does_not_claim_thread_stopped(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def handler(arguments, context):
        entered.set()
        release.wait(2)
        finished.set()
        return "done"
    registry = registry_for(handler)
    context = PluginContext(workspace=tmp_path)
    task = asyncio.create_task(PluginRuntime(registry).call("Probe", {}, context))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        result = await PluginRuntime(registry).call("Probe", {}, context)
        assert result.failure.error_code == "plugin_execution_unknown"
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["boundary", "revalidation", "batch_review"])
async def test_local_review_cancellation_rejects_tool_without_cancelling_parent(phase):
    executed = []
    boundary_calls = []
    async def boundary(arguments, context):
        boundary_calls.append(True)
        if phase == "boundary" or (phase == "revalidation" and len(boundary_calls) == 2):
            raise asyncio.CancelledError("local review cancellation")
        return None
    async def batch_review(*args, **kwargs):
        raise asyncio.CancelledError("local batch cancellation")
    registry = registry_for(lambda *args: executed.append(True), boundary=boundary)
    context = PluginContext(hooks=SimpleNamespace(pre_tool_use_batch=batch_review)
                            if phase == "batch_review" else None)
    result = await PluginRuntime(registry).call("Probe", {}, context)
    assert not result.success
    assert result.failure.error_code == "plugin_review_cancelled"
    assert not executed
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_actual_review_cancellation_still_propagates():
    entered = asyncio.Event()
    async def boundary(arguments, context):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(PluginRuntime(registry_for(lambda *args: None, boundary=boundary)).call("Probe", {}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_publication_close_is_bounded_even_if_sink_delays_cancellation(monkeypatch):
    monkeypatch.setattr(PublicationQueue, "CLOSE_TIMEOUT", .01)
    entered, release, cancelled, finished = (asyncio.Event() for _ in range(4))
    async def publish(payload):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        finally:
            finished.set()
    queue = PublicationQueue(publish)
    queue.submit({"type": "test"})
    await entered.wait()
    try:
        await asyncio.wait_for(queue.close(), .5)
        await asyncio.wait_for(cancelled.wait(), .5)
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), .5)


@pytest.mark.asyncio
async def test_publication_capacity_and_close_reject_late_events(monkeypatch):
    monkeypatch.setattr(PublicationQueue, "MAX_PENDING", 2)
    release = asyncio.Event()
    seen = []
    async def publish(payload):
        seen.append(payload["id"])
        await release.wait()
    queue = PublicationQueue(publish)
    for i in range(10):
        queue.submit({"id": i})
    assert len(queue._pending) == 2
    release.set()
    await queue.close()
    queue.submit({"id": 100})
    assert seen == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["agent_bridge_open", "agent_bridge_close"])
async def test_timing_failure_cannot_override_business_result(tmp_path, monkeypatch, failure_stage):
    runtime = ConversationRuntime()
    closed = []
    monkeypatch.setattr(runtime, "_open_bridge", lambda *a, **k:
                        SimpleNamespace(close=lambda: closed.append(True)))
    async def publish(payload):
        if payload["stage"] == failure_stage:
            raise OSError("telemetry offline")
    async def operation(bridge):
        return "correct answer"
    config = ConversationConfig(session_id="test", workspace_dir=str(tmp_path), db_path="")
    result = await runtime._with_bridge(config, operation, publish=publish)
    assert result == "correct answer"
    assert closed == [True]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_blocking_sync_publisher_cannot_block_owner_loop(monkeypatch):
    monkeypatch.setattr(PublicationQueue, "CLOSE_TIMEOUT", .01)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def publish(payload):
        entered.set()
        release.wait(2)
        finished.set()
    queue = PublicationQueue(publish)
    queue.submit({"type": "test"})
    try:
        assert await asyncio.to_thread(entered.wait, .5)
        await asyncio.wait_for(queue.close(), .5)
        assert not finished.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, .5)
