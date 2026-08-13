"""Chat persistence tests for agent binding / modelAccess / capabilities.

Verifies that chats store the handoff §14 snapshot (agent, modelAccess,
capabilities, capabilitiesRevision), that legacy chats and legacy create
requests normalize to the built-in Cyrene Agent, and that the binding-lock /
external-session / capabilities-update storage helpers persist correctly.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cyrene.workbench import chat as chat_service
from cyrene.workbench.chat import (
    _find_chat,
    _new_chat,
    _public_chat_full,
    _public_chat_light,
    _read_chats_store,
    apply_chat_agent_binding,
    lock_chat_agent_binding,
    set_chat_external_session_id,
    update_chat_agent_context_report,
    update_chat_capabilities,
)


@pytest.fixture
def chats_store(monkeypatch, tmp_path):
    """Point the chat store at an isolated JSON file."""
    store_path = tmp_path / "data" / "workbench_chats.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps({"chats": []}), encoding="utf-8")
    monkeypatch.setattr(chat_service, "_CHATS_STORE", store_path)
    monkeypatch.setattr(chat_service, "_STORE_DB_PATH", "")
    monkeypatch.setattr(chat_service, "_CONFIGURED_CHATS_STORE", None)
    return store_path


def _seed_chat(store_path: Path, chat: dict) -> None:
    payload = _read_chats_store()
    payload.setdefault("chats", []).insert(0, chat)
    chat_service._write_chats_store(payload)
    store_path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# _new_chat snapshot
# ---------------------------------------------------------------------------

def test_new_chat_defaults_to_builtin_agent():
    chat = _new_chat("project_1", "Hello", "gpt-5")
    assert chat["agent"]["installationId"] == "agent_cyrene_builtin"
    assert chat["agent"]["agentId"] == "cyrene"
    assert chat["agent"]["bindingLocked"] is False
    assert chat["modelAccess"]["mode"] == "cyrene_managed"
    assert chat["modelAccess"]["model"] == "gpt-5"
    assert chat["capabilities"]["input"]["text"] == "supported"
    assert chat["capabilitiesRevision"] == 1


def test_new_chat_stores_external_binding_and_model_access():
    chat = _new_chat(
        "project_1",
        "External",
        "",
        agent={
            "installationId": "agent_opencode_default",
            "agentId": "opencode",
            "displayName": "OpenCode",
            "version": "1.2.3",
            "driver": "acp_stdio",
        },
        model_access={"mode": "cyrene_managed", "profileId": "primary", "model": "gpt-5"},
    )
    assert chat["agent"]["installationId"] == "agent_opencode_default"
    assert chat["agent"]["displayName"] == "OpenCode"
    assert chat["agent"]["driver"] == "acp_stdio"
    assert chat["modelAccess"]["profileId"] == "primary"
    assert chat["modelAccess"]["model"] == "gpt-5"
    assert chat["capabilities"] == {}
    assert chat["capabilitiesRevision"] == 1


# ---------------------------------------------------------------------------
# Public snapshots
# ---------------------------------------------------------------------------

def test_public_chat_includes_agent_block():
    chat = _new_chat(
        "project_1",
        "External",
        "gpt-5",
        agent={"installationId": "agent_opencode_default", "agentId": "opencode", "displayName": "OpenCode"},
    )
    light = _public_chat_light(chat)
    assert light["agent"]["installationId"] == "agent_opencode_default"
    assert light["modelAccess"]["mode"] == "cyrene_managed"
    assert "capabilities" in light
    assert "capabilitiesRevision" in light
    full = _public_chat_full(chat)
    assert full["agent"]["installationId"] == "agent_opencode_default"
    assert full["capabilitiesRevision"] == 1


def test_public_chat_legacy_chat_normalizes_to_builtin_without_store_write(chats_store):
    legacy = {
        "id": "wbchat_legacy",
        "projectId": "project_1",
        "kind": "chat",
        "title": "Old",
        "status": "idle",
        "model": "gpt-4",
        "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-01T00:00:00+00:00",
        "messages": [],
    }
    _seed_chat(chats_store, legacy)
    light = _public_chat_light(legacy)
    assert light["agent"]["installationId"] == "agent_cyrene_builtin"
    assert light["agent"]["agentId"] == "cyrene"
    assert light["modelAccess"]["model"] == "gpt-4"
    assert "agent" not in legacy  # read-only: store untouched
    persisted = _read_chats_store()["chats"][0]
    assert "agent" not in persisted


def test_capabilities_revision_ignores_non_int_stored_values(chats_store):
    chat = _new_chat("project_1", "T", "gpt-5")
    chat["capabilitiesRevision"] = True  # corrupt legacy value
    _seed_chat(chats_store, chat)
    light = _public_chat_light(chat)
    assert isinstance(light["capabilitiesRevision"], int)
    assert not isinstance(light["capabilitiesRevision"], bool)
    updated = update_chat_capabilities(chat["id"], {"output": {"streaming": "supported"}})
    assert updated["capabilitiesRevision"] == 1


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def test_lock_chat_agent_binding_persists(chats_store):
    chat = _new_chat("project_1", "T", "gpt-5")
    _seed_chat(chats_store, chat)
    updated = lock_chat_agent_binding(chat["id"])
    assert updated["agent"]["bindingLocked"] is True
    persisted = _find_chat(_read_chats_store(), chat["id"])
    assert persisted["agent"]["bindingLocked"] is True


def test_set_chat_external_session_id_persists(chats_store):
    chat = _new_chat("project_1", "T", "gpt-5")
    _seed_chat(chats_store, chat)
    updated = set_chat_external_session_id(chat["id"], "ses_opencode_123")
    assert updated["agent"]["externalSessionId"] == "ses_opencode_123"
    persisted = _find_chat(_read_chats_store(), chat["id"])
    assert persisted["agent"]["externalSessionId"] == "ses_opencode_123"


def test_update_chat_capabilities_normalizes_and_bumps_revision(chats_store):
    chat = _new_chat("project_1", "T", "gpt-5")
    _seed_chat(chats_store, chat)
    updated = update_chat_capabilities(
        chat["id"],
        {"output": {"streaming": "Supported"}, "input": {"junk": 1}},
    )
    assert updated["capabilities"] == {"output": {"streaming": "supported"}}
    assert updated["capabilitiesRevision"] == 2
    updated_again = update_chat_capabilities(
        chat["id"],
        {"output": {"streaming": "supported"}},
        revision=7,
    )
    assert updated_again["capabilitiesRevision"] == 7
    persisted = _find_chat(_read_chats_store(), chat["id"])
    assert persisted["capabilitiesRevision"] == 7


def test_apply_chat_agent_binding_on_empty_chat(chats_store):
    chat = _new_chat("project_1", "T", "gpt-5")
    _seed_chat(chats_store, chat)
    updated = apply_chat_agent_binding(
        chat["id"],
        agent={"installationId": "agent_opencode_default", "agentId": "opencode", "displayName": "OpenCode"},
        model_access={"mode": "cyrene_managed", "profileId": "primary"},
    )
    assert updated["agent"]["installationId"] == "agent_opencode_default"
    persisted = _find_chat(_read_chats_store(), chat["id"])
    assert persisted["modelAccess"]["profileId"] == "primary"


def test_apply_chat_agent_binding_refuses_locked_or_nonempty_chat(chats_store):
    locked = _new_chat("project_1", "T", "gpt-5")
    locked["agent"]["bindingLocked"] = True
    _seed_chat(chats_store, locked)
    assert apply_chat_agent_binding(
        locked["id"], agent={"installationId": "agent_opencode_default"}
    ) is None

    nonempty = _new_chat("project_1", "T", "gpt-5")
    nonempty["messages"] = [{"id": "m1", "role": "user", "content": "hi"}]
    _seed_chat(chats_store, nonempty)
    assert apply_chat_agent_binding(
        nonempty["id"], agent={"installationId": "agent_opencode_default"}
    ) is None


def test_storage_helpers_missing_chat_return_none(chats_store):
    assert lock_chat_agent_binding("wbchat_missing") is None
    assert set_chat_external_session_id("wbchat_missing", "ses_x") is None
    assert update_chat_capabilities("wbchat_missing", {}) is None
    assert apply_chat_agent_binding("wbchat_missing") is None


# ---------------------------------------------------------------------------
# HTTP create contract
# ---------------------------------------------------------------------------

@pytest.fixture
def http_env(monkeypatch, tmp_path):
    """Isolated DATA_DIR / workbench stores for the create-chat route."""
    from cyrene import config as cyrene_config
    from cyrene.runtime import database as db
    from cyrene.runtime import io as io_utils
    from cyrene.workbench import runtime as routes_mod
    from cyrene.extensions import agent_runtime

    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    data_dir.mkdir()
    store_dir.mkdir()
    workspace_dir.mkdir()

    # Agent installations belong to the encrypted global settings store, not
    # the workbench DB. Route tests must replace that boundary as well or their
    # fake Agents leak into the user's real Settings UI.
    isolated_agent_settings = {}
    monkeypatch.setattr(
        agent_runtime,
        "get_setting",
        lambda key, default=None: isolated_agent_settings.get(key, default),
    )
    monkeypatch.setattr(
        agent_runtime,
        "set_setting",
        lambda key, value: isolated_agent_settings.__setitem__(key, value),
    )

    monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(cyrene_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(cyrene_config, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(routes_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes_mod, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(chat_service, "DATA_DIR", data_dir)
    chat_service._CHATS_STORE = data_dir / "workbench_chats.json"

    workbench_store = data_dir / "workbench_projects.json"
    routes_mod._WORKBENCH_STORE = workbench_store
    io_utils.atomic_write_json(
        workbench_store,
        {
            "projects": [
                {
                    "id": "project_1",
                    "name": "Alpha Project",
                    "dataKey": "project_1",
                    "description": "The first project",
                    "workspacePath": str(workspace_dir),
                    "status": "active",
                    "model": "gpt-4",
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-02T00:00:00+00:00",
                    "sessions": [],
                }
            ],
            "activeProjectId": "project_1",
            "activeSessionId": "",
        },
    )

    import asyncio

    db_path = str(tmp_path / "db.sqlite")
    asyncio.run(db.init_db(db_path))
    routes_mod._db_path = db_path

    return {"db_path": db_path, "data_dir": data_dir}


@pytest.fixture
def client(http_env):
    from route.registry import register_routes

    app = FastAPI()
    register_routes(app, bot=None, db_path=http_env["db_path"])
    return TestClient(app)


def test_create_chat_with_agent_binding_and_model_access(client):
    from cyrene.extensions import agent_runtime

    manifest = agent_runtime.validate_agent_manifest({
        "manifestApi": "cyrene.agent/v1",
        "agentId": "opencode",
        "displayName": "OpenCode",
        "version": "1.2.3",
        "driver": "acp_stdio",
        "command": "opencode",
        "protocolVersion": 1,
        "modelAccess": {"mode": "cyrene_managed", "profileId": "primary"},
    })
    agent_runtime.register_agent_installation(
        agent_id="opencode",
        manifest=manifest,
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )
    response = client.post(
        "/api/workbench/chats",
        json={
            "project": "project_1",
            "title": "With agent",
            "agent": {
                "installationId": "agent_opencode_default",
                "agentId": "opencode",
                "displayName": "OpenCode",
                "version": "1.2.3",
                "driver": "acp_stdio",
            },
            "modelAccess": {"mode": "cyrene_managed", "profileId": "primary", "model": "gpt-5"},
        },
    )
    assert response.status_code == 200
    chat = response.json()["chat"]
    assert chat["agent"]["installationId"] == "agent_opencode_default"
    assert chat["agent"]["displayName"] == "OpenCode"
    assert chat["agent"]["bindingLocked"] is False
    assert chat["modelAccess"]["profileId"] == "primary"
    assert chat["modelAccess"]["profileId"] == "primary"
    installed = agent_runtime.get_agent_installation("agent_opencode_default")
    assert chat["capabilities"] == (installed.get("capabilities") or {})
    assert chat["capabilitiesRevision"] == 1
    persisted = _read_chats_store()["chats"][0]
    assert persisted["agent"]["installationId"] == "agent_opencode_default"


def test_create_chat_without_agent_falls_back_to_builtin(client):
    response = client.post(
        "/api/workbench/chats",
        json={"project": "project_1", "title": "Legacy create"},
    )
    assert response.status_code == 200
    chat = response.json()["chat"]
    assert chat["agent"]["installationId"] == "agent_cyrene_builtin"
    assert chat["agent"]["agentId"] == "cyrene"
    assert chat["modelAccess"]["mode"] == "cyrene_managed"
    assert chat["capabilities"]["input"]["text"] == "supported"


def test_empty_chat_rebinds_in_place_but_nonempty_chat_is_locked(client):
    from cyrene.extensions import agent_runtime

    manifest = agent_runtime.validate_agent_manifest({
        "manifestApi": "cyrene.agent/v1",
        "agentId": "rebind-agent",
        "displayName": "Rebind Agent",
        "version": "1.0.0",
        "driver": "acp_stdio",
        "command": "rebind-agent",
        "protocolVersion": 1,
        "modelAccess": {"mode": "agent_managed"},
    })
    agent_runtime.register_agent_installation(
        agent_id="rebind-agent",
        manifest=manifest,
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )
    created = client.post("/api/workbench/chats", json={"project": "project_1"}).json()["chat"]
    response = client.patch(
        f"/api/workbench/chats/{created['id']}",
        json={"agent": {"installationId": "agent_rebind-agent_default"}},
    )
    assert response.status_code == 200
    rebound = response.json()["chat"]
    assert rebound["id"] == created["id"]
    assert rebound["agent"]["installationId"] == "agent_rebind-agent_default"
    assert rebound["modelAccess"]["mode"] == "agent_managed"

    payload = _read_chats_store()
    stored = _find_chat(payload, created["id"])
    stored["messages"] = [{"id": "m1", "role": "user", "content": "hi"}]
    chat_service._write_chats_store(payload)
    locked = client.patch(
        f"/api/workbench/chats/{created['id']}",
        json={"agent": {"installationId": "agent_cyrene_builtin"}},
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "agent_binding_locked"


def test_public_chat_includes_agent_config_options():
    chat = _new_chat(
        "project_1",
        "External",
        "",
        agent={"installationId": "agent_opencode_default", "agentId": "opencode"},
        model_access={"mode": "agent_managed"},
    )
    chat["agentConfigOptions"] = [{
        "id": "model", "name": "Model", "category": "model", "type": "select",
        "currentValue": "fast", "options": [{"value": "fast", "name": "Fast"}],
    }]
    chat["agentConfigValues"] = {"model": "fast"}
    public = _public_chat_full(chat)
    assert public["agentConfigOptions"][0]["category"] == "model"
    assert public["agentConfigValues"] == {"model": "fast"}


def test_external_agent_context_report_is_bounded_and_persisted(chats_store):
    chat = _new_chat(
        "project_1",
        "External",
        "",
        agent={"installationId": "agent_opencode_default", "agentId": "opencode"},
        model_access={"mode": "agent_managed"},
    )
    _seed_chat(chats_store, chat)

    updated = update_chat_agent_context_report(chat["id"], {
        "used": 120,
        "size": 1000,
        "segments": [
            {"key": "memory", "label": "Agent memory", "tokens": 40},
            {"key": "messages", "label": "Messages", "tokens": 80},
        ],
    })

    report = updated["agentContextReport"]
    assert report["used"] == 120
    assert report["size"] == 1000
    assert [item["key"] for item in report["segments"]] == ["memory", "messages"]


def test_agent_model_catalog_and_selection_are_persisted(client, monkeypatch):
    from cyrene.extensions import agent_runtime

    manifest = agent_runtime.validate_agent_manifest({
        "manifestApi": "cyrene.agent/v1",
        "agentId": "models-agent",
        "displayName": "Models Agent",
        "version": "1.0.0",
        "driver": "acp_stdio",
        "command": "models-agent",
        "protocolVersion": 1,
        "modelAccess": {"mode": "agent_managed"},
    })
    agent_runtime.register_agent_installation(
        agent_id="models-agent",
        manifest=manifest,
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )
    created = client.post(
        "/api/workbench/chats",
        json={
            "project": "project_1",
            "agent": {"installationId": "agent_models-agent_default"},
        },
    ).json()["chat"]

    async def fake_options(**_kwargs):
        return [{
            "id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "model-a",
            "options": [
                {"value": "model-a", "name": "Model A"},
                {"value": "model-b", "name": "Model B"},
            ],
        }]

    monkeypatch.setattr("cyrene.agent_runtime.discover_external_agent_config_options", fake_options)
    catalog = client.get(f"/api/workbench/chats/{created['id']}/agent-config-options")
    assert catalog.status_code == 200
    assert catalog.json()["values"] == {"model": "model-a"}

    selected = client.patch(
        f"/api/workbench/chats/{created['id']}",
        json={"agentConfigValues": {"model": "model-b"}},
    )
    assert selected.status_code == 200
    chat = selected.json()["chat"]
    assert chat["agentConfigValues"] == {"model": "model-b"}
    assert chat["modelSelectionId"] == "model-b"
    assert chat["model"] == "Model B"
