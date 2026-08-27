"""Independent lifecycle owner for a pool of :class:`MediaWorker` objects."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any
from uuid import uuid4

from .manager import MediaJobManager
from .settings import get_media_settings
from .worker import MediaWorker


class MediaDaemon:
    def __init__(self, manager: MediaJobManager) -> None:
        self.manager = manager
        self._stop = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._workers: dict[str, MediaWorker] = {}
        self._supervisor: asyncio.Task[Any] | None = None
        self._instance_id = f"media-{os.getpid()}-{uuid4().hex[:8]}"

    async def start(self) -> None:
        if self._supervisor and not self._supervisor.done():
            return
        self._stop = asyncio.Event()
        await self._resize()
        self._supervisor = asyncio.create_task(
            self._supervise(),
            name="cyrene-media-daemon",
        )

    async def stop(self) -> None:
        supervisor, self._supervisor = self._supervisor, None
        tasks, self._tasks = list(self._tasks.values()), {}
        self._stop.set()
        if supervisor:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers = {}

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(1.5)
            await self._resize()

    async def _resize(self) -> None:
        for worker_id, task in list(self._tasks.items()):
            if task.done():
                self._tasks.pop(worker_id, None)
                self._workers.pop(worker_id, None)
        settings = await asyncio.to_thread(get_media_settings)
        target = max(
            1,
            min(int(settings.get("max_parallel_jobs") or 3), 8),
        )
        while len(self._workers) < target:
            worker_id = f"{self._instance_id}-{uuid4().hex[:6]}"
            worker = MediaWorker(self.manager, worker_id)
            self._workers[worker_id] = worker
            self._tasks[worker_id] = asyncio.create_task(
                worker.run(self._stop),
                name=f"cyrene-{worker_id}",
            )
        excess = len(self._workers) - target
        if excess <= 0:
            return
        idle = [worker_id for worker_id, worker in self._workers.items() if not worker.current_job_id]
        for worker_id in idle[:excess]:
            task = self._tasks.pop(worker_id)
            self._workers.pop(worker_id, None)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._supervisor and not self._supervisor.done()),
            "worker_count": len(self._workers),
            "active_job_ids": [worker.current_job_id for worker in self._workers.values() if worker.current_job_id],
            "counts": self.manager.counts(),
        }


__all__ = ["MediaDaemon"]
