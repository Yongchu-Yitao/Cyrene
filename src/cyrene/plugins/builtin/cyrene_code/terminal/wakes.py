"""Terminal wake notifications and durable lease waits."""

from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import TerminalSession


async def wait_and_claim_wake(
    manager, consumer_id: str, lease_seconds: float = 30.0, wait_seconds: float = 30.0,
) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + max(0, min(wait_seconds, 30))
    while True:
        # Clear before reading durable state so exits cannot be lost
        # between the empty query and registering the event waiter.
        manager._wake_changed.clear()
        wake = manager.claim_wake(consumer_id, lease_seconds)
        if wake is not None or manager._db is None:
            return wake
        timeout = deadline - asyncio.get_running_loop().time()
        if timeout <= 0:
            return None
        lease = manager._db.execute(
            "SELECT min(lease_until) FROM terminal_wakes WHERE status='claimed'"
        ).fetchone()[0]
        if lease is not None:
            timeout = min(timeout, max(0.001, lease - time.time()))
        try:
            await asyncio.wait_for(manager._wake_changed.wait(), timeout)
        except TimeoutError:
            pass  # Reclaim an expired lease, or finish this long poll.


def terminal_wake_prompt(
    *, session: TerminalSession, status: str, exit_code: int | None,
    note: str,
) -> str:
    blocks = [
        "[Terminal exited — automatic wake]",
        f"terminal_id: {session.id}",
        f"status: {status}",
        f"exit_code: {exit_code if exit_code is not None else 'unknown'}",
        f"title: {session.title}",
        f"cwd: {session.cwd}",
    ]
    if note.strip():
        blocks.append(f"wake_note: {note.strip()}")
    blocks.extend([
        "",
        "This is an internal wake notification, not a user message. "
        "Use code.shell.read with the terminal_id above to inspect the terminal, "
        "then continue the prior work. Do not wait for this process again.",
    ])
    return "\n".join(blocks)
