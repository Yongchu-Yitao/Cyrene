"""Exercise the extracted owner with real queue/thread/task boundaries."""
import asyncio
import threading
from types import SimpleNamespace

from cyrene.core.transition_driver import TransitionCallbacks, TransitionDriver
from cyrene.core.plugin.permission_grants import PermissionGrants


def test_transition_owner_deduplicates_pending_work_and_closes_active_task():
    entered = threading.Event()
    cancelled = threading.Event()
    calls = []

    async def execute(kind, node):
        calls.append((kind, node.id))
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    driver = TransitionDriver('test', TransitionCallbacks(
        key=lambda node: node.id, run_id=lambda node: 'run', cancelled=lambda _: False,
        coroutine=execute, failure=lambda *_: None, idle=lambda _: None, snapshot=lambda: {},
    ))
    node = SimpleNamespace(id='node')
    driver.enqueue('advance', node)
    driver.enqueue('advance', node)
    driver.thread.start()
    try:
        assert entered.wait(3)
    finally:
        with driver.condition:
            driver.stop_locked()
        driver.join()
    assert calls == [('advance', 'node')]
    assert cancelled.is_set()
    assert not driver.pending
    assert driver.active_task is None
    assert driver.loop is None
    assert not driver.thread.is_alive()


def test_exact_grants_consume_once_and_keep_session_grants():
    grants = PermissionGrants(threading.RLock())
    fingerprint = grants.fingerprint('write', {'path':'a'}, {'operation':'write'})
    assert fingerprint != grants.fingerprint('write', {'path':'b'}, {'operation':'write'})
    grants.once.add(fingerprint)
    assert grants.consume(fingerprint)
    assert not grants.consume(fingerprint)
    grants.session.add(fingerprint)
    assert grants.consume(fingerprint)
    assert grants.consume(fingerprint)
    assert grants.fingerprint('', {}, {'fingerprint':'exact'}) == 'exact'


def test_transition_failure_preserves_exception_and_stage():
    received = []
    async def execute(kind, node):
        raise ValueError('private exception detail')
    driver = TransitionDriver('test', TransitionCallbacks(
        key=lambda node: node.id, run_id=lambda node: 'run', cancelled=lambda _: False,
        coroutine=execute, failure=lambda node, run, exc, kind: received.append((run, type(exc).__name__, kind)),
        idle=lambda _: None, snapshot=lambda: {},
    ))
    driver.enqueue('tools', SimpleNamespace(id='node'))
    driver.thread.start()
    try:
        driver.wait()
    finally:
        with driver.condition:
            driver.stop_locked()
        driver.join()
    assert received == [('run', 'ValueError', 'tools')]
