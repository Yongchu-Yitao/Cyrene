import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginContext
from agent.plugin.plugin_impl.cyrene_extensions.extension_plugin_center import (
    register_plugin_center_extension_routes,
    register_plugin_center_routes,
)


class _FakeTasks:
    def __init__(self):
        self.values = {
            "task-cli": {
                "id": "task-cli",
                "kind": "cli",
                "status": "running",
            },
            "task-mcp": {
                "id": "task-mcp",
                "kind": "mcp",
                "status": "completed",
            },
        }

    def list(self):
        return list(self.values.values())

    def get(self, task_id):
        task = self.values.get(task_id)
        return dict(task) if task else None

    def cancel(self, task_id):
        task = self.values.get(task_id)
        if not task:
            return False
        task["status"] = "cancelling"
        return True


class _FakeExtensionService:
    def __init__(self):
        self.tasks = _FakeTasks()
        self.calls = []

    def list_extensions(self):
        return {
            "skills": [],
            "mcp": [],
            "cli": [
                {
                    "id": "ripgrep",
                    "name": "ripgrep",
                    "description": "Fast search",
                    "kind": "cli",
                    "observed_state": "installed",
                    "enabled": True,
                },
                {
                    "id": "fd",
                    "name": "fd",
                    "kind": "cli",
                    "observed_state": "missing",
                    "enabled": True,
                },
            ],
        }

    async def search(self, kind, query, *, advanced, cursor):
        self.calls.append(("search", kind, query, advanced, cursor))
        return {
            "results": [
                {
                    "id": "fd",
                    "name": "fd",
                    "description": "Friendly find",
                    "kind": "cli",
                    "manager": "mise",
                    "ref": "aqua:sharkdp/fd",
                    "version": "10.2.0",
                    "verified": True,
                }
            ],
            "source": "mise-registry",
            "next_cursor": "",
        }

    def start_install(self, kind, extension_id, request, *, actor):
        self.calls.append(("install", kind, extension_id, request, actor))
        return {
            "id": "task-new",
            "kind": kind,
            "extension_id": extension_id,
            "status": "queued",
        }

    async def set_extension_enabled(self, kind, extension_id, enabled, *, actor):
        return {"ok": True, "kind": kind, "id": extension_id, "enabled": enabled}

    async def uninstall(self, kind, extension_id, *, version, actor):
        return {"ok": True, "kind": kind, "id": extension_id, "version": version}

    def bind_system_executable(self, extension_id, path):
        self.calls.append(("bind", extension_id, path))
        return {"ok": True, "path": path, "version": "1.0.0"}

    def unbind_system_executable(self, extension_id):
        self.calls.append(("unbind", extension_id))
        return {"ok": True}

    def install_local_skill(self, source_path, *, actor):
        source = Path(source_path)
        self.calls.append(("import", source.name, source.read_bytes(), actor))
        return {"ok": True, "skill": {"id": source.stem}}


class _FakeUnifiedExtensionService(_FakeExtensionService):
    def __init__(self):
        super().__init__()
        self.tasks.values.update({
            "task-toolchain": {
                "id": "task-toolchain",
                "kind": "toolchain",
                "status": "running",
            },
            "task-agent": {
                "id": "task-agent",
                "kind": "agent",
                "status": "queued",
            },
        })

    def list_extensions(self):
        agents = self.agent_listing()
        toolchains = [
            {
                "id": "python",
                "kind": "toolchain",
                "name": "Python",
                "observed_state": "installed",
                "enabled": True,
            },
            {
                "id": "node",
                "kind": "toolchain",
                "name": "Node.js",
                "observed_state": "missing",
                "enabled": False,
            },
        ]
        return {
            "recommended": [toolchains[0]],
            "skills": [],
            "mcp": [],
            "cli": [],
            "toolchains": toolchains,
            "agents": agents,
            "tasks": self.tasks.list(),
        }

    async def search(self, kind, query, *, advanced, cursor):
        self.calls.append(("search", kind, query, advanced, cursor))
        return {
            "results": [{
                "id": "node",
                "name": "Node.js",
                "description": "JavaScript runtime",
                "kind": "toolchain",
                "manager": "mise",
                "ref": "core:node",
                "version": "22.5.0",
                "verified": True,
            }],
            "source": "cyrene-catalog",
            "next_cursor": "",
        }

    async def list_versions(self, kind, extension_id):
        self.calls.append(("versions", kind, extension_id))
        return {"versions": ["22.5.0"], "recommended": "22.5.0"}

    async def set_default_version(self, extension_id, version, *, actor):
        self.calls.append(("default", extension_id, version, actor))
        return {"ok": True, "version": version}

    def bind_system_executable(self, extension_id, path):
        self.calls.append(("bind", extension_id, path))
        return {"ok": True, "path": path, "version": "22.5.0"}

    def unbind_system_executable(self, extension_id):
        self.calls.append(("unbind", extension_id))
        return {"ok": True}

    def agent_listing(self):
        return {
            "recommended": [{"agentId": "opencode", "name": "OpenCode"}],
            "installed": [{"agentId": "codex-acp", "enabled": True}],
        }

    async def create_agent_install_proposal(self, source, version, *, actor):
        self.calls.append(("proposal", source, version, actor))
        return {"ok": True, "proposalId": "proposal-1"}

    async def confirm_agent_install_proposal(self, proposal_id, *, actor):
        self.calls.append(("confirm", proposal_id, actor))
        return {"ok": True, "task": {"id": "task-agent"}}


def test_plugin_center_cli_contract_returns_exact_install_request():
    service = _FakeExtensionService()
    router = APIRouter()
    register_plugin_center_routes(
        router,
        kind="cli",
        owner_pack="cyrene_cli",
        service=service,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    listing = client.get("/api/plugin-center/cli").json()
    assert listing["owner_pack"] == "cyrene_cli"
    assert listing["projection"]["mode"] == "process_environment"
    assert [item["id"] for item in listing["items"]] == ["ripgrep"]
    assert [task["id"] for task in listing["tasks"]] == ["task-cli"]

    result = client.get(
        "/api/plugin-center/cli/search",
        params={"q": "fd", "advanced": True},
    ).json()["results"][0]
    assert result["install_request"] == {
        "version": "10.2.0",
        "ref": "aqua:sharkdp/fd",
        "spec": {
            "name": "fd",
            "kind": "cli",
            "manager": "mise",
            "ref": "aqua:sharkdp/fd",
            "version": "10.2.0",
            "description": "Friendly find",
            "verified": True,
        },
    }
    assert result["installable"] is True

    install = client.post(
        "/api/plugin-center/cli/install",
        json={"extension_id": "fd", "request": result["install_request"]},
    ).json()
    assert install["task_id"] == "task-new"
    assert service.calls[-1] == (
        "install",
        "cli",
        "fd",
        result["install_request"],
        "user",
    )
    toggled = client.put(
        "/api/plugin-center/cli/ripgrep/enabled",
        json={"enabled": False},
    ).json()
    assert toggled == {
        "ok": True,
        "kind": "cli",
        "id": "ripgrep",
        "enabled": False,
    }
    removed = client.delete(
        "/api/plugin-center/cli/ripgrep",
        params={"version": "14.1.1"},
    ).json()
    assert removed == {
        "ok": True,
        "kind": "cli",
        "id": "ripgrep",
        "version": "14.1.1",
    }
    cancelled = client.post(
        "/api/plugin-center/cli/tasks/task-cli/cancel"
    ).json()
    assert cancelled["ok"] is True
    assert cancelled["task"]["status"] == "cancelling"
    assert client.post(
        "/api/plugin-center/cli/ripgrep/bind",
        json={"path": "/usr/local/bin/rg"},
    ).json()["path"] == "/usr/local/bin/rg"
    assert client.post(
        "/api/plugin-center/cli/ripgrep/unbind"
    ).json() == {"ok": True}


def test_extensions_pack_exposes_unified_toolchain_and_agent_contracts():
    service = _FakeUnifiedExtensionService()
    router = APIRouter()
    register_plugin_center_extension_routes(router, service=service)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    overview = client.get("/api/plugin-center/overview").json()
    assert overview["owner_pack"] == "cyrene_extensions"
    assert overview["recommended"][0]["id"] == "python"
    assert [item["id"] for item in overview["toolchains"]] == ["python", "node"]
    assert overview["agents"]["recommended"][0]["agentId"] == "opencode"
    assert {task["kind"] for task in overview["tasks"]} >= {"toolchain", "agent"}

    listing = client.get("/api/plugin-center/toolchain").json()
    assert [item["id"] for item in listing["items"]] == ["python", "node"]
    assert [task["id"] for task in listing["tasks"]] == ["task-toolchain"]
    result = client.get(
        "/api/plugin-center/toolchain/search",
        params={"q": "node"},
    ).json()["results"][0]
    assert result["install_request"]["ref"] == "core:node"
    assert result["install_request"]["version"] == "22.5.0"

    installed = client.post(
        "/api/plugin-center/toolchain/install",
        json={"extension_id": "node", "request": result["install_request"]},
    ).json()
    assert installed["task_id"] == "task-new"
    assert service.calls[-1][1:3] == ("toolchain", "node")
    assert client.put(
        "/api/plugin-center/toolchain/python/enabled",
        json={"enabled": False},
    ).json()["enabled"] is False
    assert client.get(
        "/api/plugin-center/toolchain/node/versions"
    ).json()["recommended"] == "22.5.0"
    assert client.post(
        "/api/plugin-center/toolchain/node/default",
        json={"version": "22.5.0"},
    ).json() == {"ok": True, "version": "22.5.0"}
    assert client.post(
        "/api/plugin-center/toolchain/node/bind",
        json={"path": "/usr/local/bin/node"},
    ).json()["path"] == "/usr/local/bin/node"
    assert client.post(
        "/api/plugin-center/toolchain/node/unbind"
    ).json() == {"ok": True}
    assert client.delete(
        "/api/plugin-center/toolchain/node",
        params={"version": "22.5.0"},
    ).json()["kind"] == "toolchain"

    agents = client.get("/api/plugin-center/agent").json()
    assert agents["recommended"][0]["agentId"] == "opencode"
    assert agents["installed"][0]["agentId"] == "codex-acp"
    proposal = client.post(
        "/api/plugin-center/agent/install-proposals",
        json={
            "source": {
                "type": "url",
                "url": "https://example.test/agent.json",
            },
            "requestedVersion": "1.2.3",
        },
    ).json()
    assert proposal["proposalId"] == "proposal-1"
    assert client.post(
        "/api/plugin-center/agent/install-proposals/proposal-1/confirm"
    ).json()["task"]["id"] == "task-agent"
    direct = client.post(
        "/api/plugin-center/agent/install",
        json={"extension_id": "opencode", "request": {"version": "latest"}},
    ).json()
    assert direct["task_id"] == "task-new"
    assert client.delete(
        "/api/plugin-center/agent/codex-acp"
    ).json()["kind"] == "agent"


def test_extensions_pack_owns_sources_health_audit_and_global_tasks():
    service = _FakeUnifiedExtensionService()
    events = []

    def get_sources(*, include_secret=False):
        events.append(("get", include_secret))
        return {
            "network_mode": "auto",
            "github_token": "secret" if include_secret else "••••ret",
        }

    def update_sources(changes):
        events.append(("update", changes))
        return {"network_mode": changes["network_mode"]}

    async def test_sources(settings):
        events.append(("test", settings["github_token"]))
        return {"ok": True, "checks": {"github": {"ok": True}}}

    router = APIRouter()
    register_plugin_center_extension_routes(
        router,
        service=service,
        source_get=get_sources,
        source_update=update_sources,
        source_test=test_sources,
        audit_get=lambda limit: [{"action": "source.update", "limit": limit}],
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert (
        client.get("/api/plugin-center/sources").json()["github_token"]
        == "••••ret"
    )
    assert client.put(
        "/api/plugin-center/sources",
        json={"network_mode": "direct"},
    ).json() == {"network_mode": "direct"}
    assert client.post("/api/plugin-center/sources/test").json()["ok"] is True
    assert ("test", "secret") in events
    assert client.get(
        "/api/plugin-center/audit",
        params={"limit": 25},
    ).json()["records"] == [{"action": "source.update", "limit": 25}]

    tasks = client.get("/api/plugin-center/tasks").json()["tasks"]
    assert {task["id"] for task in tasks} >= {"task-toolchain", "task-agent"}
    cancelled = client.post(
        "/api/plugin-center/tasks/task-agent/cancel"
    ).json()
    assert cancelled["task"]["status"] == "cancelling"
    assert client.get("/api/plugin-center/tasks/missing").status_code == 404


def test_cli_hook_mutations_are_not_captured_as_cli_extension_ids(monkeypatch):
    application = importlib.import_module(
        "agent.plugin.plugin_impl.cyrene_cli.application"
    )
    hooks_module = importlib.import_module(
        "agent.plugin.plugin_impl.cyrene_cli.hooks"
    )
    extension_service = _FakeExtensionService()
    settings = {}
    monkeypatch.setattr(application, "application_extension_service", lambda _context: extension_service)
    monkeypatch.setattr(
        hooks_module,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        hooks_module,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    monkeypatch.setattr(hooks_module, "_audit", lambda _record: None)

    router = APIRouter()
    provided = {}
    context = SimpleNamespace(
        router=router,
        services={},
        provide=lambda name, value: provided.setdefault(name, value),
    )
    application.setup_plugin_center(context)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    created = client.post(
        "/api/plugin-center/cli/hooks",
        json={
            "name": "audit",
            "event": "PostToolUse",
            "enabled": True,
            "runner": {
                "type": "command",
                "executable": "/usr/bin/true",
                "args": [],
                "env": {},
            },
        },
    ).json()["hook"]
    hook_id = created["id"]

    toggled = client.post(
        f"/api/plugin-center/cli/hooks/{hook_id}/enabled",
        json={"enabled": False},
    ).json()
    assert toggled["hook"]["enabled"] is False
    updated = client.put(
        f"/api/plugin-center/cli/hooks/{hook_id}",
        json={
            "name": "audit after edit",
            "event": "PostToolUse",
            "enabled": False,
            "priority": 25,
            "timeout_seconds": 12,
            "runner": {
                "type": "command",
                "executable": "/usr/bin/true",
                "args": ["--edited"],
            },
        },
    ).json()["hook"]
    assert updated["name"] == "audit after edit"
    assert updated["enabled"] is False
    assert updated["priority"] == 25
    assert updated["runner"]["args"] == ["--edited"]
    assert client.delete(
        f"/api/plugin-center/cli/hooks/{hook_id}"
    ).json() == {"ok": True}
    assert not any(call[0] in {"install", "bind", "unbind"} for call in extension_service.calls)


def test_user_hook_brief_is_configured_by_background_agent(tmp_path, monkeypatch):
    hooks_module = importlib.import_module(
        "agent.plugin.plugin_impl.cyrene_cli.hooks"
    )
    config_agent = importlib.import_module(
        "agent.plugin.plugin_impl.cyrene_cli.config_agent"
    )
    service_module = importlib.import_module(
        "agent.plugin.plugin_impl.cyrene_cli.service"
    )
    settings = {}
    monkeypatch.setattr(
        hooks_module,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        hooks_module,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    monkeypatch.setattr(hooks_module, "_audit", lambda _record: None)
    monkeypatch.setattr(config_agent, "DATA_DIR", tmp_path)

    class Gateway:
        async def complete(self, *_args, **_kwargs):
            return {
                "tool_calls": [{
                    "function": {
                        "name": "submit_user_hook_configuration",
                        "arguments": {
                            "matcher": "*",
                            "script": (
                                "import json, sys\n"
                                "event = json.load(sys.stdin)\n"
                                "print(json.dumps({'received_event': event['event']}))"
                            ),
                            "timeout_seconds": 7.5,
                            "priority": -20,
                            "failure_policy": "open",
                            "rationale": "Writes the requested structured result.",
                        },
                    }
                }]
            }

    monkeypatch.setattr(
        config_agent,
        "active_plugin_service",
        lambda name: Gateway() if name == "model" else None,
    )
    hooks = hooks_module.CliHookService()
    service = service_module.CLIPluginService(
        extensions=SimpleNamespace(),
        hooks=hooks,
    )
    requested = hooks.create_generation_request({
        "name": "Record failures",
        "event": "PostToolUse",
        "action_instruction": "Record the event as structured JSON.",
        "description": "Optional explanation",
    })
    assert requested["configuration_status"] == "configuring"
    assert requested["enabled"] is False
    with pytest.raises(ValueError, match="not complete"):
        service.save_hook({"timeout_seconds": 4}, hook_id=requested["id"])

    configured = asyncio.run(
        config_agent.configure_user_hook(requested, hooks=hooks)
    )
    assert configured["configuration_status"] == "ready"
    assert configured["enabled"] is True
    assert configured["timeout_seconds"] == 7.5
    assert configured["priority"] == -20
    assert Path(configured["runner"]["path"]).is_file()
    tested = asyncio.run(hooks.test(configured["id"]))
    assert tested["output"] == {"received_event": "PostToolUse"}

    tuned = service.save_hook(
        {"timeout_seconds": 12, "priority": 250},
        hook_id=configured["id"],
    )["hook"]
    assert tuned["timeout_seconds"] == 12
    assert tuned["priority"] == 250
    reconfiguration = hooks.update_generation_request(
        configured["id"],
        {
            "event": "Stop",
            "action_instruction": "Write a final local summary.",
            "description": "Updated behavior",
            "timeout_seconds": 12,
            "priority": 250,
        },
    )
    assert reconfiguration["configuration_status"] == "configuring"
    assert reconfiguration["enabled"] is False
    regenerated = asyncio.run(
        config_agent.configure_user_hook(reconfiguration, hooks=hooks)
    )
    assert regenerated["event"] == "Stop"
    assert regenerated["action_instruction"] == "Write a final local summary."
    assert regenerated["timeout_seconds"] == 12
    assert regenerated["priority"] == 250
    assert regenerated["configuration_status"] == "ready"
    with pytest.raises(ValueError, match="only allow timeout and priority"):
        service.save_hook({"event": "TurnStart"}, hook_id=configured["id"])
    with pytest.raises(ValueError, match="between -10000 and 10000"):
        service.save_hook({"priority": 10001}, hook_id=configured["id"])


def test_cli_hook_listing_includes_existing_runtime_bindings(tmp_path, monkeypatch):
    from agent import ContextStoreRouter, HookRegistration
    from agent.hook import (
        configure_hook_action_provider,
        configure_hook_override_provider,
    )
    from agent.workbench import hook_listing as hook_listing_module
    from agent.workbench.hook_listing import (
        runtime_hook_listing,
        runtime_hook_action,
        runtime_hook_override,
        update_runtime_hook,
    )

    state_root = tmp_path / "state"
    context_root = state_root / "agent-state" / "context"
    settings = {}
    monkeypatch.setattr(
        hook_listing_module,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        hook_listing_module,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    configure_hook_override_provider(runtime_hook_override)
    configure_hook_action_provider(runtime_hook_action)

    def system_prompt_hook(_event):
        return None

    def memory_stop_hook(_event):
        return None

    try:
        with ContextStoreRouter(context_root) as router:
            router.create_tree(
                {"role": "root"},
                tree_id="older-tree",
                root_id="older-root",
                initial_hooks=(
                    HookRegistration(
                        event="SessionStart",
                        plugin_id="cyrene_system_prompt.mount",
                        hook_id="cyrene-system-prompt-session-start",
                        plugin=system_prompt_hook,
                        root_only=True,
                    ),
                    HookRegistration(
                        event="Stop",
                        plugin_id="cyrene_memory.stop",
                        hook_id="cyrene-memory-stop",
                        plugin=memory_stop_hook,
                    ),
                ),
            )
            router.create_tree(
                {"role": "root"},
                tree_id="current-tree",
                root_id="root",
                initial_hooks=(
                    HookRegistration(
                        event="SessionStart",
                        plugin_id="cyrene_system_prompt.mount",
                        hook_id="cyrene-system-prompt-session-start",
                        plugin=system_prompt_hook,
                        root_only=True,
                    ),
                ),
            )

            hooks = runtime_hook_listing(str(state_root / "db.sqlite3"))

            by_id = {item["id"]: item for item in hooks}
            assert set(by_id) == {
                "cyrene-system-prompt-session-start",
                "cyrene-memory-stop",
            }
            assert by_id["cyrene-system-prompt-session-start"] == {
                "id": "cyrene-system-prompt-session-start",
                "event": "SessionStart",
                "plugin_id": "cyrene_system_prompt.mount",
                "root_only": True,
                "matcher": "",
                "failure_policy": "open",
                "config": {},
                "enabled": True,
                "created_at": by_id["cyrene-system-prompt-session-start"]["created_at"],
                "readonly": True,
                "source": "system",
                "tree_id": "current-tree",
                "tree_count": 2,
                "current": True,
                "action": {"type": "plugin"},
            }
            assert by_id["cyrene-memory-stop"]["tree_count"] == 1
            assert by_id["cyrene-memory-stop"]["current"] is False

            changed = update_runtime_hook(
                str(state_root / "db.sqlite3"),
                "cyrene-system-prompt-session-start",
                {
                    "event": "SessionStart",
                    "plugin_id": "cyrene_system_prompt.mount",
                    "new_hook_id": "custom-system-prompt-trigger",
                    "new_event": "PreToolUse",
                    "new_plugin_id": "custom.system_prompt.handler",
                    "created_at": "2026-08-28T09:30:00+08:00",
                    "enabled": True,
                    "root_only": False,
                    "matcher": "read*",
                    "failure_policy": "block",
                    "config": {"source": "user-override"},
                    "action": {
                        "type": "command",
                        "executable": sys.executable,
                        "args": [
                            "-c",
                            "import json; print(json.dumps({'decision': 'modify', 'arguments': {'changed': True}}))",
                        ],
                        "env": {},
                        "timeout_seconds": 5,
                    },
                    "acknowledge_risk": True,
                },
            )
            assert changed["updated_bindings"] == 2
            assert changed["updated_live_bindings"] == 2
            assert changed["hook"]["id"] == "custom-system-prompt-trigger"
            assert changed["hook"]["event"] == "PreToolUse"
            assert changed["hook"]["plugin_id"] == "custom.system_prompt.handler"
            assert changed["hook"]["matcher"] == "read*"
            assert changed["hook"]["created_at"] == "2026-08-28T09:30:00+08:00"
            assert changed["hook"]["enabled"] is True
            assert changed["hook"]["failure_policy"] == "block"
            assert changed["hook"]["config"] == {"source": "user-override"}
            assert changed["hook"]["action"]["type"] == "command"
            assert changed["hook"]["action"]["executable"] == sys.executable
            live_hook = next(
                item for item in router.hooks_for("current-tree").list()
                if item.id == "custom-system-prompt-trigger"
            )
            assert live_hook.event == "PreToolUse"
            assert live_hook.plugin_id == "custom.system_prompt.handler"
            assert live_hook.enabled is True
            assert live_hook.matcher == "read*"
            assert live_hook.failure_policy == "block"
            assert live_hook.config == {"source": "user-override"}
            reviewed = asyncio.run(
                router.hooks_for("current-tree").pre_tool_use(
                    "read_file",
                    {"original": True},
                )
            )
            assert reviewed == {"changed": True}
            router.create_tree(
                {"role": "root"},
                tree_id="future-tree",
                root_id="future-root",
                initial_hooks=(
                    HookRegistration(
                        event="SessionStart",
                        plugin_id="cyrene_system_prompt.mount",
                        hook_id="cyrene-system-prompt-session-start",
                        plugin=system_prompt_hook,
                        root_only=True,
                    ),
                ),
            )
            future_hook = router.hooks_for("future-tree").list()[0]
            assert future_hook.id == "custom-system-prompt-trigger"
            assert future_hook.event == "PreToolUse"
            assert future_hook.plugin_id == "custom.system_prompt.handler"
            assert future_hook.enabled is True
            assert future_hook.root_only is False
            assert future_hook.matcher == "read*"
            assert future_hook.failure_policy == "block"
            assert future_hook.config == {"source": "user-override"}
    finally:
        configure_hook_action_provider(None)
        configure_hook_override_provider(None)


def test_disabled_extensions_pack_does_not_inject_managed_cli_environment(monkeypatch):
    plugin_application = importlib.import_module("agent.plugin.application")
    extension_service = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    checked = []

    host = SimpleNamespace(
        service=lambda service_id: checked.append(service_id) or None,
    )
    monkeypatch.setattr(
        plugin_application,
        "active_plugin_application_host",
        lambda: host,
    )
    monkeypatch.setattr(
        extension_service,
        "extension_environment",
        lambda: pytest.fail("disabled Plugin pack must not prepare managed CLI state"),
    )

    env = extension_service.agent_process_environment(
        {"PATH": "/system/bin", "npm_config_prefix": "/electron/npm"}
    )

    assert checked == ["cli", "extensions"]
    assert env == {"PATH": "/system/bin"}


@pytest.mark.parametrize(
    ("registry_type", "version", "expected"),
    (
        ("npm", "1.2.3", True),
        ("npm", "1.2.3-beta.1+build.4", True),
        ("npm", "latest", False),
        ("npm", "LATEST", False),
        ("npm", "next", False),
        ("npm", "*", False),
        ("npm", "^1.2.3", False),
        ("npm", "~1.2.3", False),
        ("pypi", "1.2.3", True),
        ("pypi", "1.2.3rc1", True),
        ("pypi", "latest", False),
        ("pypi", ">=1.2", False),
        ("pypi", "1.2.*", False),
    ),
)
def test_mcp_package_versions_must_be_exact(registry_type, version, expected):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")

    assert (
        extension_module._is_fixed_registry_package_version(
            registry_type,
            version,
        )
        is expected
    )


@pytest.mark.parametrize(
    "url",
    (
        "https://user:token@example.test/skill.git",
        "https://example.test/skill.git?access_token=secret",
        "https://example.test/skill.git#token",
    ),
)
def test_remote_skill_request_rejects_urls_that_can_persist_credentials(
    url,
    monkeypatch,
):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")

    class Tasks:
        def create(self, **_kwargs):
            pytest.fail("unsafe Skill URL must be rejected before task persistence")

    monkeypatch.setattr(
        extension_module,
        "_active_skills_service",
        lambda **_kwargs: object(),
    )
    extension_service = object.__new__(extension_module.ExtensionService)
    extension_service.tasks = Tasks()

    with pytest.raises(ValueError, match="must not contain"):
        extension_service.start_install(
            "skill",
            "reviewed",
            {"url": url, "source_commit": "a" * 40, "subdirs": ["."]},
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://user:token@example.test/mcp",
        "https://example.test/mcp?api_key=secret",
        "https://example.test/mcp#token",
    ),
)
def test_mcp_install_rejects_url_secrets_before_task_persistence(url):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")

    class Tasks:
        def create(self, **_kwargs):
            pytest.fail("unsafe MCP URL must be rejected before task persistence")

    extension_service = object.__new__(extension_module.ExtensionService)
    extension_service.tasks = Tasks()

    with pytest.raises(ValueError, match="use headers for authentication"):
        extension_service.start_install(
            "mcp",
            "remote",
            {
                "config": {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": url,
                    "enabled": True,
                }
            },
        )


@pytest.mark.asyncio
async def test_skill_clone_timeout_terminates_and_reaps_the_git_process(
    tmp_path,
    monkeypatch,
):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    process = SimpleNamespace(returncode=None, terminated=False, waited=False)

    async def communicate():
        return b"", b""

    def terminate():
        process.terminated = True
        process.returncode = -15

    async def wait():
        process.waited = True
        return process.returncode

    process.communicate = communicate
    process.terminate = terminate
    process.wait = wait
    process.kill = lambda: None

    async def create_process(*_args, **_kwargs):
        return process

    wait_calls = 0

    async def wait_for(awaitable, *, timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise extension_module.asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr(
        extension_module.asyncio,
        "create_subprocess_exec",
        create_process,
    )
    monkeypatch.setattr(extension_module.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(extension_module, "extension_environment", lambda: {})
    extension_service = object.__new__(extension_module.ExtensionService)

    with pytest.raises(RuntimeError, match="Installation timed out"):
        await extension_service._checkout_skill_source(
            "https://example.test/reviewed.git",
            tmp_path,
        )

    assert process.terminated is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_remote_skill_install_rejects_a_commit_changed_since_inspection(
    tmp_path,
    monkeypatch,
):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    installed = []

    class Skills:
        def install_skill(self, *args, **kwargs):
            installed.append((args, kwargs))
            return {"ok": True, "skill": {"id": "unsafe"}}

    class Tasks:
        def update(self, *args, **kwargs):
            return None

    repository = tmp_path / "repo"
    repository.mkdir()
    repository.joinpath("SKILL.md").write_text("# Reviewed Skill", encoding="utf-8")
    extension_service = object.__new__(extension_module.ExtensionService)
    extension_service.tasks = Tasks()

    async def checkout(_url, _destination):
        return repository, {
            "source_url": "https://example.test/reviewed.git",
            "source_commit": "b" * 40,
        }

    extension_service._checkout_skill_source = checkout
    monkeypatch.setattr(
        extension_module,
        "_active_skills_service",
        lambda **_kwargs: Skills(),
    )

    with pytest.raises(ValueError, match="changed since inspection"):
        await extension_service._install_skill(
            "task-skill",
            "reviewed",
            {
                "url": "https://example.test/reviewed.git",
                "source_commit": "a" * 40,
                "subdirs": ["."],
            },
            tmp_path / "staging",
            "user",
        )

    assert installed == []


@pytest.mark.asyncio
async def test_mcp_registry_latest_package_is_not_offered_as_installable(monkeypatch):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "servers": [
                    {
                        "server": {
                            "name": "io.example/latest-only",
                            "version": "latest",
                            "packages": [
                                {
                                    "registryType": "npm",
                                    "identifier": "@example/latest-only",
                                    "version": "latest",
                                }
                            ],
                        }
                    }
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        extension_module,
        "source_settings",
        lambda **_kwargs: {"mcp_registry_url": "https://registry.example.test"},
    )
    monkeypatch.setattr(
        extension_module,
        "_extension_http_client",
        lambda **_kwargs: Client(),
    )

    extension_service = object.__new__(extension_module.ExtensionService)
    result = await extension_service._search_mcp("latest")
    item = result["results"][0]

    assert item["installable_packages"] == []
    assert item["installable"] is False
    assert item["reason_code"] == "unsupported_registry_type"


@pytest.mark.asyncio
async def test_mcp_install_rolls_back_when_dynamic_plugin_pack_registration_fails(
    monkeypatch,
):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    previous = [
        {
            "name": "existing",
            "transport": "streamable_http",
            "url": "https://existing.example.test/mcp",
            "enabled": True,
        }
    ]
    replacements = []

    class McpService:
        def configs(self):
            return [dict(item) for item in previous]

        async def replace_configs(self, configs):
            replacements.append([dict(item) for item in configs])

        def server_status(self, _name):
            return {
                "status": "error",
                "error": "Plugin pack mcp.docs conflicts with an existing pack",
            }

    class Tasks:
        def update(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        extension_module,
        "_active_mcp_service",
        lambda **_kwargs: McpService(),
    )
    extension_service = object.__new__(extension_module.ExtensionService)
    extension_service.tasks = Tasks()

    with pytest.raises(RuntimeError, match="conflicts with an existing pack"):
        await extension_service._install_mcp(
            "task-mcp",
            "docs",
            {
                "config": {
                    "name": "docs",
                    "transport": "streamable_http",
                    "url": "https://docs.example.test/mcp",
                    "enabled": True,
                },
                "source": {"type": "manual"},
            },
            "user",
        )

    assert len(replacements) == 2
    assert [item["name"] for item in replacements[0]] == ["existing", "docs"]
    assert replacements[1] == previous


@pytest.mark.asyncio
async def test_mcp_npm_activation_is_unused_when_plugin_registration_fails(
    tmp_path,
    monkeypatch,
):
    extension_module = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    install_root = tmp_path / "npm-mcp"
    install_root.mkdir()
    install_root.joinpath("package.json").write_text(
        '{"name":"@example/docs","bin":{"docs":"bin/docs.js"}}',
        encoding="utf-8",
    )
    commands = []

    class McpService:
        def configs(self):
            return []

        async def replace_configs(self, _configs):
            return None

        def server_status(self, _name):
            return {"status": "error", "error": "dynamic pack collision"}

    class Tasks:
        def update(self, *args, **kwargs):
            return None

    class Process:
        returncode = 0

        async def communicate(self):
            return b"/managed/bin/docs\n", b""

    async def run_manager(_task_id, command, *, env, timeout=1800):
        commands.append(list(command))
        if len(command) > 1 and command[1] == "where":
            return str(install_root), ""
        return "", ""

    async def create_process(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(
        extension_module,
        "_active_mcp_service",
        lambda **_kwargs: McpService(),
    )
    monkeypatch.setattr(
        extension_module,
        "_bundled_binary",
        lambda name: Path("/managed/mise") if name == "mise" else None,
    )
    monkeypatch.setattr(extension_module, "extension_environment", lambda: {})
    monkeypatch.setattr(
        extension_module.asyncio,
        "create_subprocess_exec",
        create_process,
    )
    extension_service = object.__new__(extension_module.ExtensionService)
    extension_service.tasks = Tasks()
    extension_service._run_manager = run_manager

    with pytest.raises(RuntimeError, match="dynamic pack collision"):
        await extension_service._install_mcp(
            "task-npm-mcp",
            "docs",
            {
                "version": "1.2.3",
                "package": {
                    "registryType": "npm",
                    "identifier": "@example/docs",
                    "version": "1.2.3",
                },
            },
            "user",
        )

    assert [
        "/managed/mise",
        "unuse",
        "--global",
        "npm:@example/docs@1.2.3",
    ] in commands


def test_plugin_center_skill_upload_preserves_the_reviewed_filename(tmp_path, monkeypatch):
    plugin_center = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_plugin_center")
    monkeypatch.setattr(plugin_center, "TEMP_DIR", tmp_path)
    service = _FakeExtensionService()
    router = APIRouter()
    register_plugin_center_routes(
        router,
        kind="skill",
        owner_pack="cyrene_skills",
        service=service,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/plugin-center/skill/upload",
        files={"file": ("review-helper.md", b"# Review helper", "text/markdown")},
    )

    assert response.status_code == 200
    assert service.calls[-1] == (
        "import",
        "review-helper.md",
        b"# Review helper",
        "user",
    )
    rejected = client.post(
        "/api/plugin-center/skill/upload",
        files={"file": ("unsafe.exe", b"binary", "application/octet-stream")},
    )
    assert rejected.status_code == 400


@pytest.mark.asyncio
async def test_bash_uses_credentials_free_agent_process_environment(tmp_path, monkeypatch):
    bash_module = importlib.import_module("agent.plugin.core_impl.bash")
    expected_env = {"PATH": "/system:/cyrene-managed", "MISE_DATA_DIR": "/managed"}
    captured = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def create_process(command, **kwargs):
        captured.update({"command": command, **kwargs})
        return Process()

    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", create_process)

    result = await bash_module.bash(
        {"command": "managed-cli --version"},
        PluginContext(
            workspace=tmp_path,
            services={
                "extensions": SimpleNamespace(
                    process_environment=lambda: dict(expected_env),
                ),
            },
        ),
    )

    assert result["stdout"] == "ok"
    assert captured["env"] == expected_env


def test_terminal_environment_uses_agent_process_environment(monkeypatch):
    manager = importlib.import_module("agent.plugin.plugin_impl.cyrene_code.terminal.manager")
    extension_service = importlib.import_module("agent.plugin.plugin_impl.cyrene_extensions.extension_service")
    captured = {}
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def agent_environment(base=None):
        captured["base"] = dict(base or {})
        return {
            **dict(base or {}),
            "PATH": "/system:/cyrene-managed",
        }

    monkeypatch.setattr(extension_service, "agent_process_environment", agent_environment)
    env = manager._terminal_environment()

    assert captured["base"]
    assert env["PATH"] == "/system:/cyrene-managed"
    assert env["TERM_PROGRAM"] == "Cyrene"
    assert "GITHUB_TOKEN" not in env
