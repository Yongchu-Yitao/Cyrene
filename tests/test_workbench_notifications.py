from __future__ import annotations

import json

from webui import workbench_notifications as notifications


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
