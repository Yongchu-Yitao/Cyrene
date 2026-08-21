"""Web-runtime bridge for durable Terminal Daemon exit wakes.

The daemon owns both PTYs and wake records. This process only leases ready
records and starts the corresponding Workbench turn; restarting Electron or
the Web backend cannot discard a pending wake.
"""

from __future__ import annotations

import asyncio
import contextlib
from importlib import import_module
import logging
import os
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

DispatchResult = Literal["started", "busy", "missing", "error", "skipped"]
Dispatcher = Callable[[dict[str, Any]], Awaitable[DispatchResult]]
BusyChecker = Callable[[str], bool]


class TerminalWakeBridge:
    def __init__(self) -> None:
        self._dispatcher: Dispatcher | None = None
        self._is_busy: BusyChecker | None = None
        self._daemon_poll_task: asyncio.Task[Any] | None = None
        self._consumer_id = f"web-{os.getpid()}-{uuid4().hex[:8]}"

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

    async def try_dispatch(self, chat_id: str = "") -> list[dict[str, Any]]:
        """Compatibility hook; the durable poll loop performs dispatch."""
        return []

    def clear_pending(self) -> None:
        """Pending wakes live in the daemon and intentionally survive this call."""

    def reset_for_tests(self) -> None:
        self._dispatcher = None
        self._is_busy = None

    async def start_daemon_bridge(self) -> None:
        if self._daemon_poll_task and not self._daemon_poll_task.done():
            return
        self._daemon_poll_task = asyncio.create_task(self._daemon_poll_loop())

    async def stop_daemon_bridge(self) -> None:
        task, self._daemon_poll_task = self._daemon_poll_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _daemon_poll_loop(self) -> None:
        client = import_module(
            "cyrene.terminal.client"
        ).get_terminal_daemon_client()
        while True:
            try:
                if self._dispatcher is None:
                    await asyncio.sleep(0.5)
                    continue
                response = await client.claim_wake(self._consumer_id, lease_seconds=45)
                wake = response.get("wake")
                if not isinstance(wake, dict) or not wake:
                    await asyncio.sleep(0.8)
                    continue
                payload = {
                    "wake_id": str(wake.get("wake_id") or ""),
                    "shell_id": str(wake.get("terminal_id") or ""),
                    "terminal_id": str(wake.get("terminal_id") or ""),
                    "project_id": str(wake.get("project_id") or ""),
                    "chat_id": str(wake.get("chat_id") or ""),
                    "prompt": str(wake.get("prompt") or ""),
                    "exit_status": str(wake.get("exit_status") or ""),
                    "exit_code": wake.get("exit_code"),
                    "title": str(wake.get("title") or ""),
                    "note": str(wake.get("note") or ""),
                }
                if self._is_busy and self._is_busy(payload["chat_id"]):
                    outcome: DispatchResult = "busy"
                else:
                    outcome = await self._dispatcher(payload)
                settle = "delivered" if outcome in {"started", "skipped"} else "release"
                if outcome == "missing":
                    settle = "cancelled"
                await client.settle_wake(
                    payload["wake_id"], str(wake.get("lease_token") or ""), settle
                )
                if outcome in {"busy", "error"}:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("terminal wake bridge iteration failed")
                await asyncio.sleep(1.5)


_SERVICE = TerminalWakeBridge()


def get_shell_wake_service() -> TerminalWakeBridge:
    """Keep the historical accessor while returning the daemon-only bridge."""
    return _SERVICE


__all__ = ["TerminalWakeBridge", "get_shell_wake_service"]
