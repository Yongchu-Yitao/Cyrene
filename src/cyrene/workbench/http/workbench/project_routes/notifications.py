"""Workbench project notification HTTP adapters."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from cyrene.workbench.projects.project_services import ProjectApplicationService
from cyrene.workbench.http import schemas as api_models


def register_project_notification_routes(
    router: APIRouter,
    projects: ProjectApplicationService,
) -> None:
    @router.get("/api/workbench/notifications")
    async def api_workbench_notifications(
        tab: str = "all",
        limit: int = 80,
        visible_chat_id: str = "",
        visible_session_id: str = "",
    ):
        # Listing can also retire notifications for the currently visible
        # conversation, which is a synchronous SQLite write.  Keep its busy
        # wait off the shared ASGI event loop so one contended notification
        # poll cannot freeze chat streams and unrelated requests.
        return await asyncio.to_thread(
            projects.notifications,
            tab=tab,
            limit=limit,
            visible_chat_id=visible_chat_id,
            visible_session_id=visible_session_id,
        )

    @router.post("/api/workbench/notifications/read")
    async def api_workbench_notifications_read(body: api_models.NotificationsReadBody):
        return await asyncio.to_thread(
            projects.mark_notifications_read,
            body.ids,
            mark_all=body.markAll,
        )


__all__ = ["register_project_notification_routes"]
