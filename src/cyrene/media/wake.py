"""Durable media completion wake bridge for the Workbench Agent."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from cyrene.media.manager import MediaJobManager, get_media_job_manager

logger = logging.getLogger(__name__)

DispatchResult = Literal["started", "busy", "missing", "error", "skipped"]
Dispatcher = Callable[[dict[str, Any]], Awaitable[DispatchResult]]
BusyChecker = Callable[[str], bool]


class MediaWakeBridge:
    def __init__(self, manager: MediaJobManager | None = None) -> None:
        self.manager = manager or get_media_job_manager()
        self._dispatcher: Dispatcher | None = None
        self._is_busy: BusyChecker | None = None
        self._poll_task: asyncio.Task[Any] | None = None
        self._consumer_id = f"media-web-{os.getpid()}-{uuid4().hex[:8]}"

    def configure(
        self,
        *,
        dispatcher: Dispatcher | None = None,
        is_busy: BusyChecker | None = None,
    ) -> None:
        if dispatcher is not None:
            self._dispatcher = dispatcher
        if is_busy is not None:
            self._is_busy = is_busy

    async def start(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop(), name="cyrene-media-wake")

    async def stop(self) -> None:
        task, self._poll_task = self._poll_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def reset_for_tests(self) -> None:
        self._dispatcher = None
        self._is_busy = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                if self._dispatcher is None:
                    await asyncio.sleep(0.5)
                    continue
                wake = await asyncio.to_thread(
                    self.manager.claim_wake,
                    self._consumer_id,
                    lease_seconds=45.0,
                )
                if not wake:
                    await asyncio.sleep(0.8)
                    continue
                payload = {
                    "wake_id": str(wake.get("wake_id") or ""),
                    "batch_id": str(wake.get("batch_id") or ""),
                    "chat_id": str(wake.get("chat_id") or ""),
                    "project_id": str(wake.get("project_id") or ""),
                    "prompt": str(wake.get("prompt") or ""),
                    "note": str(wake.get("note") or ""),
                    "summary": dict(wake.get("summary") or {}),
                    "source": "media_job",
                }
                if self._is_busy and self._is_busy(payload["chat_id"]):
                    outcome: DispatchResult = "busy"
                else:
                    outcome = await self._dispatch_with_heartbeat(
                        payload,
                        wake_id=payload["wake_id"],
                        lease_token=str(wake.get("lease_token") or ""),
                    )
                settle = "delivered" if outcome in {"started", "skipped"} else "release"
                if outcome == "missing":
                    settle = "cancelled"
                await asyncio.to_thread(
                    self.manager.settle_wake,
                    payload["wake_id"],
                    str(wake.get("lease_token") or ""),
                    settle,
                )
                if outcome in {"busy", "error"}:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("media wake bridge iteration failed")
                await asyncio.sleep(1.5)

    async def _dispatch_with_heartbeat(
        self,
        payload: dict[str, Any],
        *,
        wake_id: str,
        lease_token: str,
    ) -> DispatchResult:
        if self._dispatcher is None:
            return "error"
        dispatch = asyncio.create_task(
            self._dispatcher(payload),
            name=f"cyrene-media-wake-dispatch-{wake_id}",
        )
        heartbeat = asyncio.create_task(
            self._heartbeat_claim(wake_id, lease_token),
            name=f"cyrene-media-wake-heartbeat-{wake_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {dispatch, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch in done:
                return await dispatch
            # Another bridge now owns the durable wake (or its lease could not
            # be renewed). Do not continue a potentially duplicate dispatch.
            dispatch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatch
            return "error"
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat_claim(self, wake_id: str, lease_token: str) -> None:
        while True:
            await asyncio.sleep(12.0)
            owned = await asyncio.to_thread(
                self.manager.heartbeat_wake,
                wake_id,
                lease_token,
                lease_seconds=45.0,
            )
            if not owned:
                return


_BRIDGE: MediaWakeBridge | None = None


def get_media_wake_bridge() -> MediaWakeBridge:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = MediaWakeBridge()
    return _BRIDGE


__all__ = ["MediaWakeBridge", "get_media_wake_bridge"]
