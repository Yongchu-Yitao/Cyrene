"""Notification routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_notification_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Notification API ----

    @router.post("/api/notifications/send")
    async def api_notifications_send(request: Request):
        from cyrene.runtime.notifications import notify
        body = await request.json()
        title = str(body.get("title") or "Cyrene").strip()
        text = str(body.get("text") or "").strip()
        channel = str(body.get("channel") or "auto").strip()
        if not text:
            return {"ok": False, "error": "text is required"}
        result = await notify(title, text, channel=channel)
        return result
