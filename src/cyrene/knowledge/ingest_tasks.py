"""Shared registry for active knowledge indexing tasks."""

from __future__ import annotations

import asyncio


ACTIVE_INDEX_TASKS: set[asyncio.Task] = set()
ACTIVE_INDEX_DOCS: dict[asyncio.Task, str] = {}


async def cancel_pending_tasks(doc_id: str | None = None) -> None:
    """Cancel active knowledge indexing before destructive data operations."""
    current = asyncio.current_task()
    known_tasks = list(ACTIVE_INDEX_TASKS)
    tasks = [
        task for task in known_tasks
        if task is not current
        and not task.done()
        and (doc_id is None or ACTIVE_INDEX_DOCS.get(task) == doc_id)
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    finished = {task for task in known_tasks if task.done() or task in tasks}
    ACTIVE_INDEX_TASKS.difference_update(finished)
    for task in finished:
        ACTIVE_INDEX_DOCS.pop(task, None)
