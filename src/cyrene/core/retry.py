"""Bounded recovery for unexpected runtime exceptions at resumable boundaries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
RUNTIME_RETRY_LIMIT = 3
logger = logging.getLogger(__name__)


async def retry_runtime_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    should_retry: Callable[[], bool] = lambda: True,
    on_retry: Callable[[int], Awaitable[None]] | None = None,
) -> T:
    """Retry the same operation, never cancellation or an exhausted inner retry.

    Callers must preserve operation identity and reuse committed results. Typed
    operational errors retain their own model/permission recovery policy.
    """
    for attempt in range(RUNTIME_RETRY_LIMIT + 1):
        try:
            return await operation()
        except Exception as exc:
            if (
                callable(getattr(exc, "as_error_details", None))
                or getattr(exc, "retryable", None) is False
                or getattr(exc, "_cyrene_runtime_retry_exhausted", False)
                or not should_retry()
            ):
                raise
            if attempt == RUNTIME_RETRY_LIMIT:
                # Preserve the exception type and traceback across nested owners.
                exc._cyrene_runtime_retry_exhausted = True
                raise
            logger.warning("Runtime operation failed; retrying (%s/%s)",
                           attempt + 1, RUNTIME_RETRY_LIMIT, exc_info=True)
            if on_retry is not None:
                try:
                    await on_retry(attempt + 1)
                except Exception:
                    logger.exception("Could not publish runtime retry status")
            # Yield to cancellation between attempts, even for synchronous faults.
            await asyncio.sleep(0)
    raise AssertionError("unreachable")
