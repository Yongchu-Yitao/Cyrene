"""Tests for subagent.wait_until_settled — the primitive the goal loop uses to
avoid marking a step complete while a subagent it spawned is still running.

No LLM calls; we drive the registry state machine directly so the tests are
fast and deterministic.

Run with: python -m pytest tests/test_subagent_wait_until_settled.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def test_returns_empty_without_scope():
    """Safety: with neither session_id nor round_id, never block on the whole
    registry — no-op and return immediately even with a RUNNING subagent."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("a1", "task A", session_id="s1")

    leftover = await subagent.wait_until_settled(timeout=5.0)
    assert leftover == []


async def test_returns_empty_when_already_settled():
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("a1", "task A", session_id="s1")
    await subagent.mark_done("a1", "done")

    leftover = await subagent.wait_until_settled(session_id="s1", timeout=5.0)
    assert leftover == []


async def test_blocks_until_subagent_done():
    """The core fix: a RUNNING subagent keeps the wait open; once it finishes,
    the wait returns cleanly."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("a1", "task A", session_id="s1")

    async def finish_soon():
        await asyncio.sleep(0.15)
        await subagent.mark_done("a1", "done")

    asyncio.create_task(finish_soon())
    leftover = await subagent.wait_until_settled(
        session_id="s1", timeout=5.0, poll_interval=0.02
    )
    assert leftover == []
    snap = await subagent.get_snapshot(session_id="s1")
    assert snap["a1"]["status"] == "done"


async def test_timeout_returns_active_ids():
    """A subagent that never settles should surface in the leftover list once
    the timeout elapses, rather than blocking forever."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("a1", "task A", session_id="s1")

    leftover = await subagent.wait_until_settled(
        session_id="s1", timeout=0.1, poll_interval=0.02
    )
    assert leftover == ["a1"]


async def test_on_poll_abort_stops_early():
    """on_poll returning False (e.g. the run was cancelled) ends the wait and
    reports the still-active subagents."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("a1", "task A", session_id="s1")

    calls = {"n": 0}

    async def on_poll() -> bool:
        calls["n"] += 1
        return False  # abort on the first cycle

    leftover = await subagent.wait_until_settled(
        session_id="s1", timeout=5.0, poll_interval=0.02, on_poll=on_poll
    )
    assert leftover == ["a1"]
    assert calls["n"] == 1


async def test_ignores_summary_subagents():
    """Summary subagents are awaited by the orchestrator itself and must not
    keep the goal-loop wait open."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register(
        f"{subagent._SUMMARY_AGENT_PREFIX}x", "summary", session_id="s1"
    )

    leftover = await subagent.wait_until_settled(session_id="s1", timeout=0.1)
    assert leftover == []


async def test_scoped_by_session():
    """A RUNNING subagent in another session must not block this session's
    wait."""
    from cyrene import subagent

    await subagent.clear()
    await subagent.register("other", "task", session_id="s2")

    leftover = await subagent.wait_until_settled(session_id="s1", timeout=0.1)
    assert leftover == []


async def main():
    await test_returns_empty_without_scope()
    await test_returns_empty_when_already_settled()
    await test_blocks_until_subagent_done()
    await test_timeout_returns_active_ids()
    await test_on_poll_abort_stops_early()
    await test_ignores_summary_subagents()
    await test_scoped_by_session()
    print("\nAll wait_until_settled tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
