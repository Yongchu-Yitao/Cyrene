from __future__ import annotations

import json
import multiprocessing
import sqlite3
from pathlib import Path

from cyrene.workbench.persistence.store import (
    mutate_chat,
    patch_document_fields,
    read_chat_summaries,
    read_document,
    write_document,
)


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


def test_patch_project_fields_preserves_unrelated_sqlite_state(tmp_path: Path) -> None:
    db_path = tmp_path / "cyrene.runtime.database"
    original = {
        "projects": [{"id": "project_1", "description": "keep"}],
        "activeProjectId": "project_old",
        "unrelated": {"keep": True},
    }
    write_document(db_path, "projects", original, lambda: {"projects": []})

    changed = patch_document_fields(
        db_path,
        "projects",
        {"activeProjectId": "project_1"},
        lambda: {"projects": []},
    )

    assert changed == {"activeProjectId": "project_1"}
    persisted = read_document(db_path, "projects", lambda: {"projects": []})
    assert persisted["projects"] == original["projects"]
    assert persisted["unrelated"] == {"keep": True}
    assert persisted["activeProjectId"] == "project_1"


def test_concurrent_process_appends_are_merged_without_lost_updates(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
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
    db_path = str(tmp_path / "cyrene.runtime.database")
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


def test_point_chat_mutation_updates_projection_and_only_appends_tail(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index}"}
        for index in range(50)
    ]
    write_document(
        db_path,
        "chats",
        {
            "chats": [
                {"id": "chat_1", "title": "One", "messages": messages},
                {"id": "chat_2", "title": "Two", "messages": []},
            ]
        },
        lambda: {"chats": []},
    )

    mutate_chat(
        db_path,
        "chat_1",
        lambda chat: chat["messages"].append(
            {"id": "msg_tail", "role": "assistant", "content": "final answer"}
        ),
        lambda: {"chats": []},
    )

    summaries = read_chat_summaries(db_path, lambda: {"chats": []})
    first = summaries[0]
    assert "messages" not in first
    assert first["_messageProjection"]["messageCount"] == 51
    assert first["_messageProjection"]["preview"] == "final answer"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM workbench_chat_messages WHERE chat_id='chat_1'"
        ).fetchone()[0] == 51
        assert conn.execute(
            "SELECT COUNT(*) FROM workbench_chat_messages WHERE chat_id='chat_2'"
        ).fetchone()[0] == 0


def test_chat_summary_persists_and_migrates_exact_latest_request_usage(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
    latest_usage = {
        "prompt_tokens": 7986,
        "completion_tokens": 89,
        "total_tokens": 8075,
        "prompt_cache_hit_tokens": 7748,
        "prompt_cache_miss_tokens": 238,
    }
    write_document(
        db_path,
        "chats",
        {
            "chats": [
                {
                    "id": "chat_cache_restart",
                    "messages": [
                        {
                            "id": "msg_final",
                            "role": "assistant",
                            "content": "done",
                            "usage": {
                                "prompt_tokens": 15734,
                                "completion_tokens": 272,
                                "total_tokens": 16006,
                                "prompt_cache_hit_tokens": 15059,
                                "prompt_cache_miss_tokens": 675,
                            },
                            "latestRequestUsage": latest_usage,
                        }
                    ],
                }
            ]
        },
        lambda: {"chats": []},
    )

    summaries = read_chat_summaries(db_path, lambda: {"chats": []})
    assert summaries[0]["_messageProjection"]["latestUsage"] == latest_usage

    with sqlite3.connect(db_path) as conn:
        raw_summary = conn.execute(
            "SELECT summary_json FROM workbench_chats WHERE chat_id = ?",
            ("chat_cache_restart",),
        ).fetchone()[0]
        legacy_summary = json.loads(raw_summary)
        legacy_summary.pop("latestUsage")
        conn.execute(
            "UPDATE workbench_chats SET summary_json = ? WHERE chat_id = ?",
            (json.dumps(legacy_summary), "chat_cache_restart"),
        )

    migrated = read_chat_summaries(db_path, lambda: {"chats": []})
    assert migrated[0]["_messageProjection"]["latestUsage"] == latest_usage
    with sqlite3.connect(db_path) as conn:
        stored_summary = json.loads(
            conn.execute(
                "SELECT summary_json FROM workbench_chats WHERE chat_id = ?",
                ("chat_cache_restart",),
            ).fetchone()[0]
        )
    assert stored_summary["latestUsage"] == latest_usage


def test_notification_append_and_mark_read_do_not_overwrite_each_other(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
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


def test_remote_entity_deletion_wins_over_stale_local_edit(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
    initial = {"projects": [
        {"id": "project_1", "description": "original"},
        {"id": "project_2", "description": "keep"},
    ]}
    write_document(db_path, "projects", initial, lambda: {"projects": []})

    stale_worker = read_document(db_path, "projects", lambda: {"projects": []})
    deleting_request = read_document(db_path, "projects", lambda: {"projects": []})
    deleting_request["projects"] = [deleting_request["projects"][1]]
    write_document(db_path, "projects", deleting_request, lambda: {"projects": []})

    stale_worker["projects"][0]["description"] = "stale edit"
    write_document(db_path, "projects", stale_worker, lambda: {"projects": []})

    persisted = read_document(db_path, "projects", lambda: {"projects": []})
    assert [item["id"] for item in persisted["projects"]] == ["project_2"]


def test_concurrent_process_counter_increments_use_deltas(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cyrene.runtime.database")
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
    db_path = str(tmp_path / "cyrene.runtime.database")
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
