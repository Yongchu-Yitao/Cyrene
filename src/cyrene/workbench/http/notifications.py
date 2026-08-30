"""Notification routes."""

from typing import Any

from fastapi import APIRouter, Request

from cyrene.workbench.http.errors import localized_error_payload


def register_notification_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    # ---- Notification API ----

    @router.post("/api/notifications/send")
    async def api_notifications_send(request: Request):
        from cyrene.platform.notifications import notify
        body = await request.json()
        title = str(body.get("title") or "Cyrene").strip()
        text = str(body.get("text") or "").strip()
        channel = str(body.get("channel") or "auto").strip()
        if not text:
            return {
                "ok": False,
                **localized_error_payload(
                    "Notification text is required.",
                    "请填写通知内容。",
                    "notification_text_required",
                ),
            }
        result = await notify(title, text, channel=channel)
        return result
