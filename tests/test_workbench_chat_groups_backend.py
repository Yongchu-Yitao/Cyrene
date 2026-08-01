import json
from types import SimpleNamespace

import pytest

from cyrene.agent import state as agent_state
from cyrene.model_runtime.compaction import compact_messages_for_storage
from cyrene.runtime.io import atomic_write_json, read_json_safe
from cyrene.tooling.catalog import get_capability
from cyrene.tooling.wire import get_main_wire_tool_defs
from cyrene.workbench import chat, chat_groups


@pytest.fixture
def group_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    chats_path = data_dir / "workbench_chats.json"
    groups_path = data_dir / "workbench_chat_groups.json"
    monkeypatch.setattr(chat, "_CHATS_STORE", chats_path)
    monkeypatch.setattr(chat, "_CONFIGURED_CHATS_STORE", None)
    monkeypatch.setattr(chat, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_groups, "_GROUPS_STORE", groups_path)
    monkeypatch.setattr(chat_groups, "_CONFIGURED_STORE", None)
    monkeypatch.setattr(chat_groups, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_groups, "_workspace_path", lambda _project_id: "/workspace/project-one")
    monkeypatch.setattr(agent_state, "_DATA_DIR", data_dir)
    monkeypatch.setattr(agent_state, "DATA_DIR", data_dir)
    agent_state._sessions.clear()

    chats = []
    for session_id in ("chat_a", "chat_b", "chat_c"):
        chats.append({
            "id": session_id,
            "projectId": "project_1",
            "kind": "chat",
            "title": session_id,
            "status": "idle",
            "updatedAt": "2026-08-01T08:00:00+00:00",
            "messages": [],
        })
    atomic_write_json(chats_path, {"chats": chats})
    yield {"data_dir": data_dir, "chats_path": chats_path, "groups_path": groups_path}
    agent_state._sessions.clear()


def _events(data_dir, session_id):
    state = read_json_safe(data_dir / "sessions" / session_id / "state.json") or {}
    return [
        message["chat_group_event"]
        for message in state.get("messages", [])
        if isinstance(message, dict) and message.get("chat_group_context_event")
    ]


@pytest.mark.asyncio
async def test_membership_changes_append_active_and_revocation_events(group_env):
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "chatIds": ["chat_a", "chat_b"],
    }])

    first_a = _events(group_env["data_dir"], "chat_a")[-1]
    assert first_a["access"] == "active"
    assert [item["sessionId"] for item in first_a["members"]] == ["chat_a", "chat_b"]
    assert first_a["stateLogicalPath"] == "data/sessions/chat_a/state.json"
    assert first_a["workspacePath"] == "/workspace/project-one"

    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "chatIds": ["chat_a", "chat_c"],
    }])

    assert _events(group_env["data_dir"], "chat_b")[-1]["eventType"] == "membership_revoked"
    assert _events(group_env["data_dir"], "chat_c")[-1]["access"] == "active"
    assert _events(group_env["data_dir"], "chat_a")[-1]["eventType"] == "membership_updated"

    # Removing one member leaves a singleton, so normalization dissolves the
    # group and both sessions receive revocation events.
    await chat_groups.replace_project_groups("project_1", [])
    assert _events(group_env["data_dir"], "chat_a")[-1]["access"] == "revoked"
    assert _events(group_env["data_dir"], "chat_c")[-1]["access"] == "revoked"
    assert chat_groups.get_project_groups("project_1")["groups"] == []


@pytest.mark.asyncio
async def test_committed_event_outbox_repairs_append_failure(group_env, monkeypatch):
    original_append = chat_groups._append_event
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated crash window")
        return await original_append(*args, **kwargs)

    monkeypatch.setattr(chat_groups, "_append_event", fail_once)
    desired = [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b"]}]
    with pytest.raises(OSError, match="crash window"):
        await chat_groups.replace_project_groups("project_1", desired)

    persisted = json.loads(group_env["groups_path"].read_text(encoding="utf-8"))
    assert persisted["projects"][0]["eventOutbox"]

    monkeypatch.setattr(chat_groups, "_append_event", original_append)
    await chat_groups.replace_project_groups("project_1", desired)
    assert _events(group_env["data_dir"], "chat_a")[-1]["access"] == "active"
    assert _events(group_env["data_dir"], "chat_b")[-1]["access"] == "active"
    repaired = json.loads(group_env["groups_path"].read_text(encoding="utf-8"))
    assert repaired["projects"][0]["eventOutbox"] == []


@pytest.mark.asyncio
async def test_stale_member_removal_rebases_without_dissolving_remote_addition(group_env):
    initial = [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b"]}]
    await chat_groups.replace_project_groups("project_1", initial)
    # Another window adds chat_c after this client's base snapshot.
    await chat_groups.replace_project_groups(
        "project_1",
        [{"id": "group_one", "title": "Group", "chatIds": ["chat_a", "chat_b", "chat_c"]}],
        base_groups=initial,
        mutation_intent={"type": "move", "sessionId": "chat_c", "targetGroupId": "group_one"},
    )
    # The stale window dragged chat_b out. Its local normalized projection is
    # empty (a,b would have become a singleton), but the exact intent preserves
    # the concurrently added member and leaves a,c grouped.
    result = await chat_groups.replace_project_groups(
        "project_1",
        [],
        base_groups=initial,
        mutation_intent={"type": "remove_member", "sessionId": "chat_b"},
    )
    assert result["groups"][0]["chatIds"] == ["chat_a", "chat_c"]


@pytest.mark.asyncio
async def test_generated_group_metadata_is_persisted_authoritatively(group_env):
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "新对话组",
        "summary": "",
        "chatIds": ["chat_a", "chat_b"],
    }])

    result = await chat_groups.update_group_metadata(
        "project_1",
        "group_one",
        signature="chat_a|chat_b",
        metadata={
            "title": "浏览器操作",
            "summary": "整理网站访问与浏览结果。",
            "lang": "zh",
        },
    )

    assert result["groups"][0]["title"] == "浏览器操作"
    assert result["groups"][0]["summary"] == "整理网站访问与浏览结果。"
    reloaded = chat_groups.get_project_groups("project_1")["groups"][0]
    assert reloaded["title"] == "浏览器操作"
    assert reloaded["summary"] == "整理网站访问与浏览结果。"
    assert reloaded["metadataLang"] == "zh"
    assert reloaded["metadataChatIds"] == "chat_a|chat_b"


@pytest.mark.asyncio
async def test_peer_snapshot_is_completed_public_data_and_access_revokes(group_env, monkeypatch):
    payload = json.loads(group_env["chats_path"].read_text(encoding="utf-8"))
    peer = next(item for item in payload["chats"] if item["id"] == "chat_b")
    peer["messages"] = [
        {"id": "u1", "role": "user", "content": "original request", "createdAt": "2026-08-01T08:00:00Z"},
        {
            "id": "a1",
            "role": "assistant",
            "content": "completed conclusion",
            "createdAt": "2026-08-01T08:01:00Z",
            "attachments": [{"id": "f1", "name": "result.md", "url": "/exports/result.md"}],
        },
        {"id": "u2", "role": "user", "content": "currently running request", "createdAt": "2026-08-01T08:02:00Z"},
    ]
    atomic_write_json(group_env["chats_path"], payload)
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "summary": "Browser research and completed results.",
        "chatIds": ["chat_a", "chat_b"],
    }])

    from cyrene.workbench import context as workbench_context

    monkeypatch.setattr(
        workbench_context,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project_1" if session_id in {"chat_a", "chat_b", "chat_c"} else None,
    )
    monkeypatch.setattr(
        chat,
        "_CHAT_RUN_MANAGER",
        SimpleNamespace(get=lambda session_id: object() if session_id == "chat_b" else None),
    )

    result = chat_groups.read_group_session_snapshots("chat_a")
    snapshot = result["sessions"][0]
    assert result["trust"] == "untrusted_peer_conversation_data"
    assert result["groupTitle"] == "Research"
    assert result["groupSummary"] == "Browser research and completed results."
    assert "group summary is orientation only" in result["instructionBoundary"]
    assert "groupSummary" not in _events(group_env["data_dir"], "chat_a")[-1]
    assert snapshot["sessionId"] == "chat_b"
    assert snapshot["running"] is True
    assert snapshot["finalConclusion"] == "completed conclusion"
    assert [item["messageId"] for item in snapshot["messages"]] == ["u1", "a1"]
    assert snapshot["artifacts"][0]["name"] == "result.md"
    assert snapshot["stateLogicalPath"] == "data/sessions/chat_b/state.json"

    # Summary changes stay in the authoritative group store and become visible
    # on the next explicit read without rewriting any session history prefix.
    event_count = len(_events(group_env["data_dir"], "chat_a"))
    await chat_groups.replace_project_groups("project_1", [{
        "id": "group_one",
        "title": "Research",
        "summary": "Updated browser research summary.",
        "chatIds": ["chat_a", "chat_b"],
    }])
    assert len(_events(group_env["data_dir"], "chat_a")) == event_count
    refreshed = chat_groups.read_group_session_snapshots("chat_a")
    assert refreshed["groupSummary"] == "Updated browser research summary."

    await chat_groups.replace_project_groups("project_1", [])
    with pytest.raises(PermissionError, match="not in an active chat group"):
        chat_groups.read_group_session_snapshots("chat_a")


def test_group_capability_is_main_only_and_does_not_expand_wire_schema():
    assert get_capability(
        "memory.group_sessions.read", actor="main", include_disabled=True
    ) is not None
    assert get_capability(
        "memory.group_sessions.read", actor="subagent", include_disabled=True
    ) is None
    wire_json = json.dumps(get_main_wire_tool_defs(), ensure_ascii=False)
    assert "memory.group_sessions.read" not in wire_json
    assert "ReadChatGroupSessions" not in wire_json


def test_compaction_preserves_exact_append_only_group_events():
    event = {
        "role": "system",
        "content": '[Chat group context event]\n{"members":["chat_a","chat_b"]}',
        "chat_group_context_event": True,
        "chat_group_event": {"access": "active", "members": ["chat_a", "chat_b"]},
        "message_id": "group_event_fixed",
    }
    messages = [
        {"role": "user", "content": "old request " * 50},
        {"role": "assistant", "content": "old answer " * 50},
        event,
        {"role": "user", "content": "recent request"},
    ]
    compacted = compact_messages_for_storage(messages, ctx_limit=80, force=True)
    preserved = next(item for item in compacted if item.get("chat_group_context_event"))
    assert preserved == event
