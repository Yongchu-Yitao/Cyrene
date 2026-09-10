"""Wait for an owned transaction operation to reach a known outcome."""

from __future__ import annotations

import asyncio
from typing import TypeVar

_T = TypeVar("_T")


async def transaction_outcome(task: asyncio.Task[_T]) -> tuple[_T, bool]:
    """Return the result and whether the caller was cancelled while waiting.

    Cancellation cannot stop a SQLite worker thread. Owners must first observe
    its outcome and acknowledge the commit (or release an unconsumed lease),
    then propagate cancellation. Keep shielding through repeated cancellations.
    The supplied task must include any commit acknowledgement it owns.
    """
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
