"""Workbench project notification HTTP adapters."""

from __future__ import annotations

from fastapi import APIRouter

from cyrene.workbench.project_services import ProjectApplicationService
from route import schemas as api_models


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
        return projects.notifications(
            tab=tab,
            limit=limit,
            visible_chat_id=visible_chat_id,
            visible_session_id=visible_session_id,
        )

    @router.post("/api/workbench/notifications/read")
    async def api_workbench_notifications_read(body: api_models.NotificationsReadBody):
        return projects.mark_notifications_read(body.ids, mark_all=body.markAll)


__all__ = ["register_project_notification_routes"]
