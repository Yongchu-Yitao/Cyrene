"""Helpers for owning and shutting down background asyncio tasks.

Detached tasks must keep a strong reference, consume their exceptions, and be
awaited during shutdown.  Keeping those rules in one place prevents event-loop
teardown from leaving SQLite worker threads and ContextVars in half-closed
states.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any


class TaskShutdownTimeout(TimeoutError):
    """An owner still has live tasks and must retain its resources."""

    def __init__(self, pending: Iterable[asyncio.Task[Any]]) -> None:
        self.pending = frozenset(pending)
        super().__init__(
            "Tasks did not finish shutdown: "
            + ", ".join(sorted(task.get_name() for task in self.pending))
        )


def track_task(
    task: asyncio.Task[Any],
    registry: set[asyncio.Task[Any]],
    *,
    logger: logging.Logger | None = None,
    label: str = "background task",
) -> asyncio.Task[Any]:
    """Keep *task* alive and consume/log its terminal exception."""
    registry.add(task)

    def _done(completed: asyncio.Task[Any]) -> None:
        registry.discard(completed)
        try:
            error = completed.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            if logger is not None:
                logger.exception("Failed to inspect %s", label)
            return
        if error is not None and logger is not None:
            logger.error(
                "%s failed",
                label,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(_done)
    return task


async def cancel_and_wait(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    timeout: float = 5.0,
) -> None:
    """Cancel tasks owned by the current loop and wait for finalizers.

    Cancellation is requested once: a second cancel can interrupt a finalizer.
    A missed deadline raises with the outstanding tasks, rather than reporting
    success and allowing the caller to close resources still in use.
    """
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    owned: list[asyncio.Task[Any]] = []
    for task in set(tasks):
        if task is current or task.done():
            continue
        try:
            task_loop = task.get_loop()
        except RuntimeError:
            continue
        if task_loop is loop:
            if not task.cancelling():
                task.cancel()
            owned.append(task)
        elif not task_loop.is_closed():
            task_loop.call_soon_threadsafe(task.cancel)
    if not owned:
        return
    await wait_for_tasks(owned, timeout=timeout)


async def wait_for_tasks(
    tasks: Iterable[asyncio.Task[Any]], *, timeout: float = 5.0,
) -> None:
    """Await an owner's finalizers without injecting additional cancellation."""
    owned = set(tasks) - {asyncio.current_task()}
    if not owned:
        return
    done, pending = await asyncio.wait(owned, timeout=max(0.0, float(timeout)))
    for task in done:
        if not task.cancelled():
            task.exception()
    if pending:
        raise TaskShutdownTimeout(pending)


async def drain_or_cancel(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    grace_seconds: float = 1.0,
) -> None:
    """Give short telemetry tasks time to finish, then cancel leftovers."""
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()
    owned = [
        task
        for task in set(tasks)
        if task is not current and not task.done() and task.get_loop() is loop
    ]
    if not owned:
        return
    _done, pending = await asyncio.wait(owned, timeout=max(0.0, float(grace_seconds)))
    if pending:
        await cancel_and_wait(pending)
