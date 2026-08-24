"""Visible chat projection for completed media jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from cyrene.media.manager import MediaJobManager


class MediaDelivery:
    def __init__(self, manager: MediaJobManager) -> None:
        self.manager = manager

    async def report(self, job: dict[str, Any]) -> bool:
        from cyrene.workbench.chat import append_media_job_message

        message = await append_media_job_message(job, job.get("attachments") or [])
        if message is None:
            await asyncio.to_thread(
                self.manager.mark_reported,
                str(job.get("job_id") or ""),
                delivery_error="owning chat no longer exists",
            )
            return False
        await asyncio.to_thread(
            self.manager.mark_reported,
            str(job.get("job_id") or ""),
        )
        return True


__all__ = ["MediaDelivery"]
