"""Schedule remote desktop disconnect checks and file cleanup."""

import asyncio
import time


async def run_cleanup_loop(service) -> None:
    last_file_cleanup = time.monotonic()
    while True:
        service._host_sessions_changed.clear()
        timeout = 0.75 if service._host_sessions else max(0, 60 - (time.monotonic() - last_file_cleanup))
        try:
            await asyncio.wait_for(service._host_sessions_changed.wait(), timeout)
        except TimeoutError:
            pass
        if service._host_sessions:
            await service._consume_forced_disconnects()
        now = time.monotonic()
        if now - last_file_cleanup >= 60:
            last_file_cleanup = now
            service._cleanup_snapshot_directory()
            service._cleanup_clipboard_directory()
