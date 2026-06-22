"""Tests for the Workbench chat fork (edit-and-branch) endpoint."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

pil_mock = MagicMock()
pil_mock.__version__ = "9.0.0"
sys.modules["PIL"] = pil_mock
pil_mock.Image = MagicMock()

from cyrene import config as cyrene_config
from cyrene import db
from webui.routes import register_routes


@pytest.fixture
def fork_env(monkeypatch, tmp_path):
    """Prepare isolated DATA_DIR / STORE_DIR / WORKSPACE_DIR for fork tests."""
    from cyrene import io_utils
    from webui import routes as routes_mod
    from webui import routes_workbench_chat as chat_mod

    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    data_dir.mkdir()
    store_dir.mkdir()
    workspace_dir.mkdir()

    monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(cyrene_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(cyrene_config, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(routes_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes_mod, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(chat_mod, "DATA_DIR", data_dir)
    # The agent state module captures DATA_DIR at import time as _DATA_DIR;
    # patch both aliases so _session_state_file resolves to the temp data_dir.
    from cyrene.agent import state as agent_state
    monkeypatch.setattr(agent_state, "_DATA_DIR", data_dir)
    monkeypatch.setattr(agent_state, "DATA_DIR", data_dir)
    # Clear cached SessionContext entries so they re-resolve against the new
    # DATA_DIR (otherwise stale paths from prior tests leak in).
    agent_state._sessions.clear()
    chat_mod._CHATS_STORE = data_dir / "workbench_chats.json"
    routes_mod._WORKBENCH_STORE = data_dir / "workbench_projects.json"

    with TemporaryDirectory() as db_tmp:
        db_path = str(Path(db_tmp) / "test.db")
        import asyncio

        asyncio.run(db.init_db(db_path))
        cyrene_config.set_knowledge_db_path_override(db_path)
        routes_mod._db_path = db_path

        store = {
            "projects": [
                {
                    "id": "project_1",
                    "name": "Alpha Project",
                    "dataKey": "project_1",
                    "description": "The first project",
                    "workspacePath": str(workspace_dir),
                    "status": "active",
                    "model": "gpt-4",
                    "context": {"summary": "Alpha summary"},
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-02T00:00:00+00:00",
                    "sessions": [],
                }
            ],
            "activeProjectId": "project_1",
            "activeSessionId": "",
        }
        io_utils.atomic_write_json(routes_mod._WORKBENCH_STORE, store)

        yield {
            "db_path": db_path,
            "data_dir": data_dir,
            "store_dir": store_dir,
            "workspace_dir": workspace_dir,
            "routes_mod": routes_mod,
            "chat_mod": chat_mod,
        }
        cyrene_config.set_knowledge_db_path_override(None)


@pytest.fixture
def client(fork_env):
    app = FastAPI()
    register_routes(app, bot=None, db_path=fork_env["db_path"])
    return TestClient(app)


def _write_chat(fork_env, chat_id, messages, **extra):
    """Write a single chat into the workbench chats store."""
    from cyrene import io_utils

    chat = {
        "id": chat_id,
        "projectId": "project_1",
        "kind": "chat",
        "title": extra.get("title", "Test chat"),
        "status": "idle",
        "model": "gpt-4",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-02T00:00:00+00:00",
        "messages": messages,
    }
    chat.update(extra)
    io_utils.atomic_write_json(
        fork_env["data_dir"] / "workbench_chats.json",
        {"chats": [chat]},
    )
    return chat


def _write_state(fork_env, session_id, messages):
    """Write a raw agent state file for a session."""
    from cyrene import io_utils

    state_dir = fork_env["data_dir"] / "sessions" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"
    io_utils.atomic_write_json(state_path, {"messages": messages})
    return state_path


def test_fork_creates_new_chat_with_prefix_and_edited_message(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
        {"id": "a1", "role": "assistant", "content": "hi there"},
        {"id": "u2", "role": "user", "content": "tell me a joke"},
        {"id": "a2", "role": "assistant", "content": "why did the chicken..."},
        {"id": "u3", "role": "user", "content": "another one"},
        {"id": "a3", "role": "assistant", "content": "knock knock"},
    ])

    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u2", "content": "tell me a story instead"},
    )

    assert response.status_code == 200
    new_chat = response.json()["chat"]
    assert new_chat["id"] != "chat_src"
    assert new_chat["forkedFromChatId"] == "chat_src"
    assert new_chat["forkedAtMessageId"] == "u2"

    # The forked transcript should be [u1, a1, edited_u2] — u3/a3 dropped.
    contents = [(m["role"], m["content"]) for m in new_chat["messages"]]
    assert contents == [
        ("user", "hello"),
        ("assistant", "hi there"),
        ("user", "tell me a story instead"),
    ]
    # The edited user message gets a fresh id.
    assert new_chat["messages"][2]["id"] != "u2"


def test_fork_preserves_attachments_in_edited_message(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "look at this",
         "attachments": [{"id": "f1", "name": "pic.png", "url": "/files/f1.png"}],
         "agentAttachments": [{"id": "f1", "name": "pic.png", "path": "/data/uploads/f1.png"}]},
        {"id": "a1", "role": "assistant", "content": "nice"},
        {"id": "u2", "role": "user", "content": "describe it"},
        {"id": "a2", "role": "assistant", "content": "it's blue"},
    ])

    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u1", "content": "what is this image"},
    )

    assert response.status_code == 200
    new_chat = response.json()["chat"]
    edited = new_chat["messages"][0]
    assert edited["role"] == "user"
    assert edited["content"] == "what is this image"
    # Public attachments preserved on the edited entry.
    assert edited["attachments"] == [{"id": "f1", "name": "pic.png", "url": "/files/f1.png"}]
    # The public chat payload should NOT leak agentAttachments.
    assert "agentAttachments" not in edited


def test_fork_preserves_original_chat_untouched(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
        {"id": "a1", "role": "assistant", "content": "hi"},
        {"id": "u2", "role": "user", "content": "tell me a joke"},
        {"id": "a2", "role": "assistant", "content": "chicken crossed road"},
    ])

    client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u2", "content": "tell me a story"},
    )

    # Re-read the original chat — it should be unchanged.
    response = client.get("/api/workbench/chats/chat_src")
    assert response.status_code == 200
    original = response.json()["chat"]
    contents = [(m["role"], m["content"]) for m in original["messages"]]
    assert contents == [
        ("user", "hello"),
        ("assistant", "hi"),
        ("user", "tell me a joke"),
        ("assistant", "chicken crossed road"),
    ]


def test_fork_seeds_and_truncates_state_at_edit_boundary(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "reply1"},
        {"id": "u2", "role": "user", "content": "second"},
        {"id": "a2", "role": "assistant", "content": "reply2"},
        {"id": "u3", "role": "user", "content": "third"},
        {"id": "a3", "role": "assistant", "content": "reply3"},
    ])
    _write_state(fork_env, "chat_src", [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply1"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "reply2"},
        {"role": "user", "content": "third"},
        {"role": "assistant", "content": "reply3"},
    ])

    # Edit u2 (the 2nd user message) → state should truncate before the 2nd
    # visible user message, keeping [system, u1, a1].
    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u2", "content": "edited second"},
    )
    assert response.status_code == 200
    new_chat_id = response.json()["chat"]["id"]

    state_path = fork_env["data_dir"] / "sessions" / new_chat_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    roles = [m["role"] for m in state["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert state["messages"][1]["content"] == "first"
    assert state["messages"][2]["content"] == "reply1"


def test_fork_replay_send_does_not_retruncate_state(client, fork_env, monkeypatch):
    """A forkReplay send replays the last user message but skips state truncation."""
    from cyrene import agent

    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "reply1"},
        {"id": "u2", "role": "user", "content": "second"},
        {"id": "a2", "role": "assistant", "content": "reply2"},
    ])
    _write_state(fork_env, "chat_src", [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply1"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "reply2"},
    ])

    fork_response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u2", "content": "edited second"},
    )
    new_chat_id = fork_response.json()["chat"]["id"]

    # The forked state should be [system, u1, a1] (truncated before 2nd user msg).
    state_path = fork_env["data_dir"] / "sessions" / new_chat_id / "state.json"
    state_before = json.loads(state_path.read_text(encoding="utf-8"))
    assert [m["role"] for m in state_before["messages"]] == ["system", "user", "assistant"]

    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        # Simulate run_agent appending the user message + generating a reply.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["messages"].extend([
            {"role": "user", "content": str(kwargs.get("user_message") or "")},
            {"role": "assistant", "content": "new reply"},
        ])
        from cyrene import io_utils
        io_utils.atomic_write_json(state_path, state)
        return "new reply"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    # Send with retry + forkReplay — should replay the edited last user message
    # WITHOUT re-truncating the state.
    send_response = client.post(
        f"/api/workbench/chats/{new_chat_id}/messages",
        json={"retry": True, "forkReplay": True},
    )

    assert send_response.status_code == 200
    assert send_response.json()["assistantMessage"]["content"] == "new reply"
    # The state should now be [system, u1, a1, edited_u2, new_reply] — the
    # prefix was preserved (not re-truncated to before u1).
    state_after = json.loads(state_path.read_text(encoding="utf-8"))
    roles = [m["role"] for m in state_after["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert state_after["messages"][3]["content"] == "edited second"


def test_fork_rejects_legacy_chat(client, fork_env):
    response = client.post(
        "/api/workbench/chats/legacy:project_1:session_1/fork",
        json={"messageId": "u1", "content": "edited"},
    )
    assert response.status_code == 403


def test_fork_rejects_nonexistent_message(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
    ])
    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "nonexistent", "content": "edited"},
    )
    assert response.status_code == 404


def test_fork_rejects_editing_assistant_message(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
        {"id": "a1", "role": "assistant", "content": "hi"},
    ])
    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "a1", "content": "edited"},
    )
    assert response.status_code == 400


def test_fork_rejects_empty_content(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
    ])
    response = client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u1", "content": "   "},
    )
    assert response.status_code == 400


def test_fork_exposes_fork_fields_in_chat_listing(client, fork_env):
    _write_chat(fork_env, "chat_src", [
        {"id": "u1", "role": "user", "content": "hello"},
        {"id": "a1", "role": "assistant", "content": "hi"},
        {"id": "u2", "role": "user", "content": "bye"},
    ])

    client.post(
        "/api/workbench/chats/chat_src/fork",
        json={"messageId": "u2", "content": "stay"},
    )

    response = client.get("/api/workbench/chats?project=project_1")
    chats = response.json()["chats"]
    forked = [c for c in chats if c.get("forkedFromChatId") == "chat_src"]
    assert len(forked) == 1
    assert forked[0]["forkedAtMessageId"] == "u2"
