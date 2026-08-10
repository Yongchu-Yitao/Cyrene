"""Shared pytest fixtures and test-isolation helpers for the ``tests/`` suite.

The agent runtime keeps a handful of process-wide ``asyncio`` primitives in
``cyrene.agent.state`` (a global ``_agent_lock``, an ``_interrupt_event`` and a
few sets of fire-and-forget background tasks). The test suite runs with
``asyncio_mode = auto`` (see ``pytest.ini``), which gives every test its own
event loop and tears that loop down when the test finishes.

Several tests spawn detached background tasks (session-label refreshes, the
main-inbox worker, behavior-learning kicks, ...) via ``_run_chat_agent`` and
friends. Those tasks are never awaited or cancelled, so a test can finish while
a task is still parked inside ``async with _agent_lock:``. When that test's loop
is closed, the ``async with`` release never runs and the global lock is left
stale-locked (``_agent_lock._locked is True``) for the rest of the process.

A later test (e.g. ``test_interrupt_active_run_clears_after_locked_run_finishes``)
then does ``async with _agent_lock`` on its own fresh loop, blocks forever on the
stale lock, and the whole run hangs.

This is purely a cross-test isolation artifact, not a production bug: in a real
long-lived event loop the ``async with`` always releases (normally or via
cancellation). The autouse fixture below forcibly resets the shared state before
every test so one test can never poison the next.
"""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
import PIL as _REAL_PIL

# Tests import ``cyrene`` from the in-repo ``src/`` tree; make sure it is on the
# path before any cyrene import happens (mirrors the shim at the top of
# ``tests/test_runtime_fixes.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_REAL_PIL_IMAGE = importlib.import_module("PIL.Image")


@pytest.fixture
def real_pillow_modules():
    """Temporarily undo legacy module-level Pillow shims for image tests."""
    previous_modules = {name: sys.modules.get(name) for name in ("PIL", "PIL.Image")}
    previous_image_attr = getattr(_REAL_PIL, "Image", None)
    sys.modules["PIL"] = _REAL_PIL
    sys.modules["PIL.Image"] = _REAL_PIL_IMAGE
    _REAL_PIL.Image = _REAL_PIL_IMAGE
    from cyrene.tooling import mcp_content
    previous_mcp_image = mcp_content.Image
    mcp_content.Image = _REAL_PIL_IMAGE
    try:
        yield _REAL_PIL_IMAGE
    finally:
        mcp_content.Image = previous_mcp_image
        _REAL_PIL.Image = previous_image_attr
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.fixture(autouse=True)
def _reset_agent_global_state():
    """Force-reset process-wide agent state before each test (setup phase).

    Always access the globals through the module object (``_state._agent_lock``
    etc.) rather than ``from ... import _agent_lock``: they are reassigned/mutated
    at runtime, and a from-import would bind a stale local copy.
    """
    from cyrene.agent import state as _state

    def _cancel_pending_tasks(tasks) -> None:
        # Mirror ``session.clear_session_id._cancel_pending_tasks``: a task may
        # live on an already-closed loop, so guard both ``done()`` and the
        # loop's ``is_closed()`` and swallow the RuntimeError that a closed loop
        # can raise.
        for task in list(tasks):
            try:
                if not task.done() and not task.get_loop().is_closed():
                    task.cancel()
            except RuntimeError:
                pass
        tasks.clear()

    # 1. Drain any stale lock. ``asyncio.Lock`` is not owned by a task, so
    #    ``release()`` simply flips the internal ``_locked`` flag and may be
    #    called from any task. It only raises when ``_locked`` is already False,
    #    so gate every release on ``locked()``.
    while _state._agent_lock.locked():
        _state._agent_lock.release()

    # The session-state lock is far less likely to leak, but reset it too so a
    # parked ``clear_session_id`` cannot deadlock a later test.
    while _state._session_state_lock.locked():
        _state._session_state_lock.release()

    # 2. Clear the interrupt event so a leaked "interrupt requested" flag from a
    #    previous test does not bleed into this one.
    _state._interrupt_event.clear()

    # 2b. Reset the candidate failure-cooldown cache: a test that exercises a
    #     failing candidate must not make a later test silently skip it.
    from cyrene import call_llm as _call_llm_mod
    _call_llm_mod._candidate_cooldowns.clear()
    _call_llm_mod._published_fallback_notices.clear()
    _call_llm_mod._last_success_cache = {}
    _call_llm_mod._http_clients.clear()
    # Model-candidate tests use fake endpoints; never persist those into the
    # developer's real encrypted desktop settings.
    _call_llm_mod.set_setting = lambda _key, _value: None
    _call_llm_mod._record_latency_faf = lambda _event: None

    from cyrene.agent import coordinator as _coordinator

    _cancel_pending_tasks(_coordinator._BACKGROUND_BEHAVIOR_TASKS)
    _coordinator._DEFERRED_BEHAVIOR_TASK = None

    # Knowledge indexing has the same shape: routes may spawn detached indexing
    # tasks, and a closed per-test event loop can otherwise leave the module lock
    # stale-locked for a later test.
    from cyrene.knowledge import ingest as _knowledge_ingest

    _cancel_pending_tasks(_knowledge_ingest._ACTIVE_INDEX_TASKS)
    _knowledge_ingest._INDEX_LOCK = asyncio.Lock()

    # 3/4. Cancel + clear all fire-and-forget task registries.
    _cancel_pending_tasks(_state._pending_interrupt_clearers)
    _cancel_pending_tasks(_state._pending_label_refreshes)
    _cancel_pending_tasks(_state._pending_compressors)
    _cancel_pending_tasks(_state._pending_housekeeping)

    if _state._main_inbox_worker is not None:
        _cancel_pending_tasks({_state._main_inbox_worker})
        _state._main_inbox_worker = None

    yield


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item):
    """Drain runtime work immediately after a test's call phase.

    This must remain a hook instead of an async autouse fixture.  Several
    knowledge-base fixtures call ``asyncio.run()`` during setup.  An async
    autouse fixture makes pytest-asyncio install the test loop *before* those
    fixtures run, and ``asyncio.run()`` then clears that loop before the test
    coroutine starts.  The call hook runs after the coroutine but before
    pytest-asyncio tears its loop down, which is the safe window for exercising
    the production shutdown aggregator.
    """
    yield

    loop = item.funcargs.get("event_loop")
    if loop is None:
        return
    if loop.is_closed() or loop.is_running():
        return

    from cyrene.runtime.lifecycle import shutdown_background_work

    loop.run_until_complete(shutdown_background_work())
