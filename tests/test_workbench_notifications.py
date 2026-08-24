from __future__ import annotations

import json
from pathlib import Path

from cyrene.workbench import notifications as notifications


ROOT = Path(__file__).resolve().parents[1]


def _css_rule(styles: str, selector: str) -> str:
    return styles.split(f"{selector} {{", 1)[1].split("}", 1)[0]


def test_notification_panel_wraps_long_content_without_horizontal_scroll() -> None:
    styles = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")
    notification_styles = styles.split(".workbench-notif-popover {", 1)[1].split(
        "/* ── Help & Support center", 1
    )[0]

    notification_list = _css_rule(styles, ".workbench-notif-list")
    notification_body = _css_rule(styles, ".workbench-notif-item-body")
    notification_tab = _css_rule(styles, ".workbench-notif-tab")

    assert "overflow-x: hidden;" in notification_list
    assert "overflow-y: auto;" in notification_list
    assert "touch-action: pan-y;" in notification_list
    assert "overflow-wrap: anywhere;" in notification_body
    assert "flex: 1 1 0;" in notification_tab
    assert "min-width: 0;" in notification_tab
    assert "overflow-x: auto;" not in notification_styles


def test_visible_session_notification_is_not_returned_or_counted(tmp_path, monkeypatch) -> None:
    store = tmp_path / "workbench_notifications.json"
    monkeypatch.setattr(notifications, "_NOTIFICATIONS_STORE", store)
    monkeypatch.setattr(notifications, "DATA_DIR", tmp_path)

    notifications.append_notification(
        title="Agent 回复完成",
        tab="comment",
        meta={"sessionId": "session-visible"},
    )
    notifications.append_notification(
        title="另一个任务回复完成",
        tab="comment",
        meta={"sessionId": "session-other"},
    )

    payload = notifications.list_notifications(visible_session_id="session-visible")

    assert [item["title"] for item in payload["items"]] == ["另一个任务回复完成"]
    assert payload["unreadCount"] == 1
    assert payload["unreadByTab"]["comment"] == 1
    persisted = json.loads(store.read_text(encoding="utf-8"))
    assert [item["title"] for item in persisted["items"]] == ["另一个任务回复完成"]


def test_visible_chat_only_removes_unread_notification(tmp_path, monkeypatch) -> None:
    store = tmp_path / "workbench_notifications.json"
    monkeypatch.setattr(notifications, "_NOTIFICATIONS_STORE", store)
    monkeypatch.setattr(notifications, "DATA_DIR", tmp_path)

    visible = notifications.append_notification(
        title="当前对话回复",
        tab="mention",
        meta={"chatId": "chat-visible"},
    )
    notifications.mark_notifications_read([visible["id"]])
    notifications.append_notification(
        title="当前对话的新回复",
        tab="mention",
        meta={"chatId": "chat-visible"},
    )

    payload = notifications.list_notifications(visible_chat_id="chat-visible")

    assert [item["title"] for item in payload["items"]] == ["当前对话回复"]
    assert payload["items"][0]["read"] is True
    assert payload["unreadCount"] == 0


def test_notification_routes_keep_sqlite_work_off_the_event_loop() -> None:
    source = (
        ROOT / "src/route/workbench/project_routes/notifications.py"
    ).read_text(encoding="utf-8")

    assert "return await asyncio.to_thread(\n            projects.notifications," in source
    assert "return await asyncio.to_thread(\n            projects.mark_notifications_read," in source
