from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from cyrene.workbench_store import patch_document_fields, read_document, write_document


def _append_worker(db_path: str, barrier, item_id: str) -> None:
    payload = read_document(db_path, "projects", lambda: {"projects": []})
    barrier.wait()
    payload["projects"].append({"id": item_id, "name": item_id})
    write_document(db_path, "projects", payload, lambda: {"projects": []})


def _append_chat_message_worker(db_path: str, barrier, message_id: str) -> None:
    payload = read_document(db_path, "chats", lambda: {"chats": []})
    barrier.wait()
    payload["chats"][0]["messages"].append(
        {"id": message_id, "role": "assistant", "content": message_id}
    )
    write_document(db_path, "chats", payload, lambda: {"chats": []})


def _append_notification_worker(db_path: str, barrier) -> None:
    payload = read_document(db_path, "notifications", lambda: {"items": []})
    barrier.wait()
    payload["items"].insert(
        0,
        {"id": "notif_new", "title": "new", "createdAt": "2026-06-22T01:00:00+00:00", "read": False},
    )
    write_document(db_path, "notifications", payload, lambda: {"items": []})


def _mark_notification_read_worker(db_path: str, barrier) -> None:
    payload = read_document(db_path, "notifications", lambda: {"items": []})
    barrier.wait()
    payload["items"][0]["read"] = True
    write_document(db_path, "notifications", payload, lambda: {"items": []})


def _increment_worker(db_path: str, barrier) -> None:
    entries = read_document(
        db_path,
        "memory:project",
        list,
    )
    barrier.wait()
    entries[0]["mention_count"] += 1
    write_document(db_path, "memory:project", entries, list)


def test_sqlite_is_authoritative_after_one_time_json_import(tmp_path: Path) -> None:
    db_path = tmp_path / "cyrene.db"
    legacy = tmp_path / "workbench_chats.json"
    legacy.write_text(
        json.dumps({"chats": [{"id": "chat_1", "title": "Imported"}]}),
        encoding="utf-8",
    )

    imported = read_document(
        db_path,
        "chats",
        lambda: {"chats": []},
        legacy_path=legacy,
    )
    assert imported["chats"][0]["title"] == "Imported"

    legacy.write_text(
        json.dumps({"chats": [{"id": "chat_1", "title": "External overwrite"}]}),
        encoding="utf-8",
    )
    persisted = read_document(
        db_path,
        "chats",
        lambda: {"chats": []},
        legacy_path=legacy,
    )
    assert persisted["chats"][0]["title"] == "Imported"


def test_patch_document_fields_preserves_unrelated_state_and_updates_export(tmp_path: Path) -> None:
    db_path = tmp_path / "cyrene.db"
    export_path = tmp_path / "workbench_projects.json"
    original = {
        "projects": [{"id": "project_1", "sessions": [{"id": "session_1"}]}],
        "activeProjectId": "project_old",
        "activeSessionId": "session_old",
        "unrelated": {"keep": True},
    }
    write_document(db_path, "projects", original, lambda: {"projects": []})

    changed = patch_document_fields(
        db_path,
        "projects",
        {"activeProjectId": "project_1", "activeSessionId": ""},
        lambda: {"projects": []},
        export_path=export_path,
    )

    assert changed == {"activeProjectId": "project_1", "activeSessionId": ""}
    persisted = read_document(db_path, "projects", lambda: {"projects": []})
    assert persisted["projects"] == original["projects"]
    assert persisted["unrelated"] == {"keep": True}
    assert persisted["activeProjectId"] == "project_1"
    assert persisted["activeSessionId"] == ""
    assert json.loads(export_path.read_text(encoding="utf-8"))["activeSessionId"] == ""


def test_concurrent_process_appends_are_merged_without_lost_updates(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.db")
    write_document(db_path, "projects", {"projects": []}, lambda: {"projects": []})

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_append_worker, args=(db_path, barrier, "item_a")),
        context.Process(target=_append_worker, args=(db_path, barrier, "item_b")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    payload = read_document(db_path, "projects", lambda: {"projects": []})
    assert {item["id"] for item in payload["projects"]} == {"item_a", "item_b"}


def test_concurrent_chat_messages_are_both_preserved(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.db")
    write_document(
        db_path,
        "chats",
        {"chats": [{"id": "chat_1", "messages": []}]},
        lambda: {"chats": []},
    )

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_append_chat_message_worker, args=(db_path, barrier, "msg_a")),
        context.Process(target=_append_chat_message_worker, args=(db_path, barrier, "msg_b")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    payload = read_document(db_path, "chats", lambda: {"chats": []})
    assert {item["id"] for item in payload["chats"][0]["messages"]} == {"msg_a", "msg_b"}


def test_notification_append_and_mark_read_do_not_overwrite_each_other(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.db")
    write_document(
        db_path,
        "notifications",
        {
            "items": [
                {
                    "id": "notif_old",
                    "title": "old",
                    "createdAt": "2026-06-22T00:00:00+00:00",
                    "read": False,
                }
            ]
        },
        lambda: {"items": []},
    )

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_append_notification_worker, args=(db_path, barrier)),
        context.Process(target=_mark_notification_read_worker, args=(db_path, barrier)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    payload = read_document(db_path, "notifications", lambda: {"items": []})
    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["notif_old"]["read"] is True
    assert by_id["notif_new"]["read"] is False


def test_concurrent_process_counter_increments_use_deltas(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.db")
    write_document(
        db_path,
        "memory:project",
        [{"id": "mem_1", "mention_count": 1}],
        list,
    )

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_increment_worker, args=(db_path, barrier)),
        context.Process(target=_increment_worker, args=(db_path, barrier)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    entries = read_document(db_path, "memory:project", list)
    assert entries[0]["mention_count"] == 3


def test_eight_process_counter_burst_has_no_lost_updates_or_lock_failures(tmp_path: Path) -> None:
    """Bounded pressure check for the process-safe merge/write path."""
    db_path = str(tmp_path / "cyrene.db")
    write_document(
        db_path,
        "memory:project",
        [{"id": "mem_1", "mention_count": 0}],
        list,
    )

    worker_count = 8
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(worker_count)
    processes = [
        context.Process(target=_increment_worker, args=(db_path, barrier))
        for _ in range(worker_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    entries = read_document(db_path, "memory:project", list)
    assert entries[0]["mention_count"] == worker_count
