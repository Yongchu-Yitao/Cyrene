from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pytest

from cyrene.workbench.chat import chat_groups
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.persistence.store import read_document


@pytest.fixture
def group_env(tmp_path, monkeypatch):
    db_path = tmp_path / "cyrene.db"
    legacy_path = tmp_path / "workbench_chat_groups.json"
    chat_groups.configure_store(str(db_path))
    monkeypatch.setattr(chat_groups, "_GROUPS_STORE", legacy_path)
    monkeypatch.setattr(chat_groups, "_workspace_path", lambda _project_id: "/workspace/project-one")

    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    latest: dict[tuple[str, str], dict[str, Any]] = {}

    async def append_event(session_id, event, *, event_id=""):
        copied = dict(event)
        copied["eventId"] = event_id
        events[str(session_id)].append(copied)
        latest[(str(session_id), str(event.get("projectId") or ""))] = copied

    monkeypatch.setattr(chat_groups, "_append_event", append_event)
    monkeypatch.setattr(
        chat_groups,
        "_latest_group_event",
        lambda session_id, project_id: latest.get((str(session_id), str(project_id))),
    )

    chats = []
    for session_id in ("chat_a", "chat_b", "chat_c"):
        chats.append({
            "id": session_id,
            "projectId": "project_1",
            "kind": "chat",
            "title": session_id,
            "preview": f"Preview for {session_id}",
            "status": "idle",
            "updatedAt": "2026-08-01T08:00:00+00:00",
            "messages": [],
        })
    ChatRepository(str(db_path)).write({"chats": chats})
    return {
        "db_path": db_path,
        "legacy_path": legacy_path,
        "events": events,
        "latest": latest,
        "append_event": append_event,
    }


@pytest.mark.asyncio
async def test_membership_changes_persist_and_emit_active_and_revoked_events(group_env):
    created = await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "chatIds": ["chat_a", "chat_b"],
    }])

    assert created["migrationRequired"] is False
    assert created["membershipRevision"] == 1
    assert group_env["events"]["chat_a"][-1]["access"] == "active"
    assert [
        member["sessionId"]
        for member in group_env["events"]["chat_a"][-1]["members"]
    ] == ["chat_a", "chat_b"]

    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "chatIds": ["chat_a", "chat_c"],
    }])
    assert group_env["events"]["chat_b"][-1]["eventType"] == "membership_revoked"
    assert group_env["events"]["chat_c"][-1]["access"] == "active"
    assert group_env["events"]["chat_a"][-1]["eventType"] == "membership_updated"

    await chat_groups.replace_project_groups("project_1", [])
    assert group_env["events"]["chat_a"][-1]["access"] == "revoked"
    assert group_env["events"]["chat_c"][-1]["access"] == "revoked"
    assert chat_groups.get_project_groups("project_1")["groups"] == []


@pytest.mark.asyncio
async def test_committed_event_outbox_retries_after_delivery_failure(group_env, monkeypatch):
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated delivery failure")
        await group_env["append_event"](*args, **kwargs)

    monkeypatch.setattr(chat_groups, "_append_event", fail_once)
    desired = [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b"]}]
    with pytest.raises(OSError, match="delivery failure"):
        await chat_groups.replace_project_groups("project_1", desired)

    persisted = read_document(
        group_env["db_path"],
        "chat_groups",
        lambda: {"version": 1, "projects": []},
    )
    assert persisted["projects"][0]["eventOutbox"]

    monkeypatch.setattr(chat_groups, "_append_event", group_env["append_event"])
    await chat_groups.replace_project_groups("project_1", desired)
    repaired = read_document(
        group_env["db_path"],
        "chat_groups",
        lambda: {"version": 1, "projects": []},
    )
    assert repaired["projects"][0]["eventOutbox"] == []
    assert group_env["events"]["chat_a"][-1]["access"] == "active"
    assert group_env["events"]["chat_b"][-1]["access"] == "active"


@pytest.mark.asyncio
async def test_stale_member_removal_preserves_a_concurrent_remote_addition(group_env):
    initial = [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b"]}]
    await chat_groups.replace_project_groups("project_1", initial)
    await chat_groups.replace_project_groups(
        "project_1",
        [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b", "chat_c"]}],
        base_groups=initial,
        mutation_intent={"type": "move", "sessionId": "chat_c", "targetGroupId": "group_one"},
    )

    result = await chat_groups.replace_project_groups(
        "project_1",
        [],
        base_groups=initial,
        mutation_intent={"type": "remove_member", "sessionId": "chat_b"},
    )
    assert result["groups"][0]["chatIds"] == ["chat_a", "chat_c"]


@pytest.mark.asyncio
async def test_generated_metadata_is_authoritative_and_respects_title_lock(group_env):
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "User title",
        "titleLocked": True,
        "chatIds": ["chat_a", "chat_b"],
    }])

    result = await chat_groups.update_group_metadata(
        "project_1",
        "group_one",
        signature="chat_a|chat_b",
        metadata={"title": "Generated title", "summary": "Shared result", "lang": "en"},
    )
    group = result["groups"][0]
    assert group["title"] == "User title"
    assert group["summary"] == "Shared result"
    assert group["metadataLang"] == "en"
    assert group["metadataChatIds"] == "chat_a|chat_b"


@pytest.mark.asyncio
async def test_peer_snapshot_exposes_only_completed_public_messages_and_revokes_access(group_env):
    repository = ChatRepository(str(group_env["db_path"]))
    peer = repository.get("chat_b")
    assert peer is not None
    peer["messages"] = [
        {"id": "u1", "role": "user", "content": "Original request", "createdAt": "2026-08-01T08:00:00Z"},
        {
            "id": "a1",
            "role": "assistant",
            "content": "Completed conclusion",
            "createdAt": "2026-08-01T08:01:00Z",
            "attachments": [{"id": "f1", "name": "result.md", "url": "/exports/result.md"}],
        },
        {"id": "u2", "role": "user", "content": "Unfinished follow-up", "createdAt": "2026-08-01T08:02:00Z"},
        {"role": "system", "content": "Private runtime context"},
    ]
    repository.write_one(peer)
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "summary": "Shared browser research.",
        "chatIds": ["chat_a", "chat_b"],
    }])

    result = chat_groups.read_group_session_snapshots("chat_a")
    snapshot = result["sessions"][0]
    assert result["trust"] == "untrusted_peer_conversation_data"
    assert result["groupSummary"] == "Shared browser research."
    assert [item["messageId"] for item in snapshot["messages"]] == ["u1", "a1"]
    assert snapshot["finalConclusion"] == "Completed conclusion"
    assert snapshot["artifacts"][0]["name"] == "result.md"

    await chat_groups.replace_project_groups("project_1", [])
    with pytest.raises(PermissionError, match="not in an active chat group"):
        chat_groups.read_group_session_snapshots("chat_a")


def test_legacy_json_is_imported_once_and_browser_migration_is_explicit(group_env):
    group_env["legacy_path"].write_text(json.dumps({
        "version": 1,
        "projects": [{
            "id": "project_1",
            "revision": 7,
            "membershipRevision": 3,
            "migrationVersion": 1,
            "groups": [{
                "id": "legacy_group",
                "title": "Legacy research",
                "chatIds": ["chat_a", "chat_b"],
            }],
        }],
    }), encoding="utf-8")

    imported = chat_groups.get_project_groups("project_1")
    assert imported["groups"][0]["id"] == "legacy_group"
    assert imported["migrationRequired"] is False

    missing = chat_groups.get_project_groups("project_new")
    assert missing["migrationRequired"] is True
