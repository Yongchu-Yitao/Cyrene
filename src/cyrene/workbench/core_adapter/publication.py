"""Bounded live delivery; durable conversation state remains authoritative."""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import threading
from concurrent.futures import Future

logger = logging.getLogger(__name__)
_sync_slots = threading.BoundedSemaphore(32)


async def _invoke_sync(publish, payload):
    # Arbitrary synchronous sinks must not block the owner loop, nor create an
    # unbounded number of hung threads across conversations.
    if not _sync_slots.acquire(blocking=False):
        raise RuntimeError("Synchronous publication capacity exhausted")
    result = Future()
    result.set_running_or_notify_cancel()
    copied_context = contextvars.copy_context()
    wrapped = asyncio.wrap_future(result)
    def run():
        try:
            value = copied_context.run(publish, payload)
        except BaseException as exc:
            result.set_exception(RuntimeError("Publisher exited") if isinstance(exc, SystemExit) else exc)
        else:
            result.set_result(value)
        finally:
            _sync_slots.release()
    try:
        threading.Thread(target=run, name="live-publication", daemon=True).start()
    except BaseException:
        _sync_slots.release()
        raise
    try:
        return await wrapped
    except asyncio.CancelledError:
        def discard(future):
            if future.exception() is None and inspect.iscoroutine(future.result()):
                future.result().close()
        result.add_done_callback(discard)
        raise


class PublicationQueue:
    CLOSE_TIMEOUT = 2.0
    MAX_PENDING = 256

    def __init__(self, publish):
        self._publish = publish
        self._loop = asyncio.get_running_loop()
        self._lock = threading.RLock()
        self._pending = set()
        self._closed = False
        self._overflow_reported = False

    async def _send(self, payload):
        try:
            if (inspect.iscoroutinefunction(self._publish)
                or inspect.iscoroutinefunction(getattr(self._publish, "__call__", None))):
                result = self._publish(payload)
            else:
                result = await _invoke_sync(self._publish, payload)
            if inspect.isawaitable(result):
                await result
        except SystemExit as exc:
            raise RuntimeError("Publisher exited") from exc

    def submit(self, payload) -> None:
        with self._lock:
            if self._closed:
                return
            if len(self._pending) >= self.MAX_PENDING:
                if not self._overflow_reported:
                    logger.warning("Live publication queue full; recover from durable state")
                    self._overflow_reported = True
                return
            future = asyncio.run_coroutine_threadsafe(self._send(dict(payload)), self._loop)
            self._pending.add(future)
            future.add_done_callback(self._settled)

    def _settled(self, future):
        with self._lock:
            self._pending.discard(future)
        if not future.cancelled():
            error = future.exception()
            if error is not None:
                logger.warning("Live publication failed; durable result remains valid",
                               exc_info=(type(error), error, error.__traceback__))

    async def close(self) -> None:
        with self._lock:
            self._closed = True
            pending = tuple(self._pending)
        if not pending:
            return
        wrapped = [asyncio.wrap_future(future) for future in pending]
        for future in wrapped:
            future.add_done_callback(lambda item: None if item.cancelled() else item.exception())
        try:
            _, unfinished = await asyncio.wait(wrapped, timeout=self.CLOSE_TIMEOUT)
            if unfinished:
                logger.warning("Live publication deadline exceeded; recover from durable state")
        finally:
            # Cancellation acknowledgement from a publisher is not allowed to
            # take ownership of conversation completion.
            for future in pending:
                if not future.done():
                    future.cancel()
