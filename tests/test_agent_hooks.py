import json
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _hook(**overrides):
    value = {
        "name": "Demo Hook",
        "event": "PreToolUse",
        "matcher": "Bash",
        "enabled": True,
        "priority": 100,
        "failure_policy": "open",
        "timeout_seconds": 10,
        "runner": {
            "type": "command",
            "executable": "/usr/bin/true",
            "args": [],
            "env": {"HOOK_TOKEN": "secret-value"},
        },
    }
    value.update(overrides)
    return value


def test_hook_registry_preserves_hidden_environment_and_redacts_public_state(monkeypatch):
    from cyrene.hooks import service

    settings = {}
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: settings.__setitem__(key, value))
    monkeypatch.setattr(service, "_audit", lambda _record: None)

    hooks = service.HookService()
    created = hooks.save(_hook())
    updated = hooks.save({
        "id": created["id"],
        "name": "Renamed Hook",
        "event": "PreToolUse",
        "matcher": "Bash",
        "runner": {"type": "command", "executable": "/usr/bin/true", "args": []},
    })

    assert updated["runner"]["env"] == {"HOOK_TOKEN": "secret-value"}
    public = service.public_hook_config(updated)
    assert "env" not in public["runner"]
    assert public["runner"]["environment_keys"] == ["HOOK_TOKEN"]
    assert "secret-value" not in json.dumps(public)


@pytest.mark.asyncio
async def test_pre_hooks_apply_modifications_in_priority_order_and_can_block(monkeypatch):
    from cyrene.hooks import service

    first = _hook(id="first", priority=10)
    second = _hook(id="second", priority=20)
    blocker = _hook(id="blocker", priority=30)
    hook_service = service.HookService()
    monkeypatch.setattr(service, "get_hook_service", lambda: hook_service)
    monkeypatch.setattr(hook_service, "matching", lambda event, name="": [first, second, blocker])

    seen = []

    async def execute(hook, payload, **_kwargs):
        seen.append((hook["id"], payload["tool"]["arguments"].copy()))
        if hook["id"] == "first":
            return {"decision": "modify", "arguments": {"value": 2}}
        if hook["id"] == "second":
            return {"decision": "modify", "arguments": {"value": 3, "review_me": True}}
        return {"decision": "block", "reason": "policy denied"}

    monkeypatch.setattr(hook_service, "execute", execute)
    with pytest.raises(service.HookBlocked, match="policy denied"):
        await service.run_pre_tool_hooks("Bash", {"value": 1})

    assert seen == [
        ("first", {"value": 1}),
        ("second", {"value": 2}),
        ("blocker", {"value": 3, "review_me": True}),
    ]


@pytest.mark.asyncio
async def test_hook_subprocess_protocol_and_session_context(tmp_path, monkeypatch):
    from cyrene.hooks import service

    script = tmp_path / "hook.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "event = json.load(sys.stdin)\n"
        "print(json.dumps({'context': 'loaded:' + event['event']}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    hook = _hook(
        id="session-hook",
        event="SessionStart",
        matcher="*",
        runner={"type": "script", "path": str(script), "args": [], "env": {}},
    )
    hook_service = service.HookService()
    monkeypatch.setattr(service, "get_hook_service", lambda: hook_service)
    monkeypatch.setattr(hook_service, "matching", lambda event, name="": [hook] if event == "SessionStart" else [])
    monkeypatch.setattr(service, "_audit", lambda _record: None)

    context = await service.run_lifecycle_hooks("SessionStart", parent_agent_id="main")
    assert context == "loaded:SessionStart"


@pytest.mark.asyncio
async def test_tool_executor_uses_hook_modified_arguments(monkeypatch):
    import cyrene.hooks as hooks_package
    from cyrene.observability import debug
    from cyrene.tooling import executor

    received = {}
    post = []

    async def handler(arguments, *_args):
        received.update(arguments)
        return "ok"

    async def pre(_name, _arguments):
        return {"changed": True}

    async def after(name, arguments, result, **kwargs):
        post.append((name, arguments, result, kwargs["success"]))

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setitem(executor.TOOL_HANDLERS, "__hook_test__", handler)
    monkeypatch.setattr(hooks_package, "run_pre_tool_hooks", pre)
    monkeypatch.setattr(hooks_package, "run_post_tool_hooks", after)
    monkeypatch.setattr(debug, "publish_event", publish)

    result = await executor._execute_tool("__hook_test__", {"changed": False}, None, 0, "", None)
    assert result == "ok"
    assert received == {"changed": True}
    assert post == [("__hook_test__", {"changed": True}, "ok", True)]


@pytest.mark.asyncio
async def test_mcp_tool_uses_hooks_and_modified_arguments(monkeypatch):
    import cyrene.hooks as hooks_package
    from cyrene.observability import debug
    from cyrene.tooling import executor
    from cyrene.tooling import runtime_api
    from cyrene.tooling.backends import mcp_manager

    calls = []
    post = []

    class Manager:
        def has_tool(self, name):
            return name == "external_hook_probe"

        async def execute_tool(self, name, arguments):
            calls.append((name, arguments))
            return "mcp-ok"

    async def pre(_name, _arguments):
        return {"modified": "by-hook"}

    async def after(name, arguments, result, **kwargs):
        post.append((name, arguments, result, kwargs["success"]))

    async def no_review(**_kwargs):
        return None

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_manager, "get_manager", lambda: Manager())
    monkeypatch.setattr(runtime_api, "request_scope_elevation", no_review)
    monkeypatch.setattr(hooks_package, "run_pre_tool_hooks", pre)
    monkeypatch.setattr(hooks_package, "run_post_tool_hooks", after)
    monkeypatch.setattr(debug, "publish_event", publish)

    result = await executor._execute_tool("external_hook_probe", {"original": True}, None, 0, "", None)
    assert result == "mcp-ok"
    assert calls == [("external_hook_probe", {"modified": "by-hook"})]
    assert post == [("external_hook_probe", {"modified": "by-hook"}, "mcp-ok", True)]


@pytest.mark.asyncio
async def test_reviewer_sees_pre_hook_modified_shell_command(monkeypatch):
    import cyrene.hooks as hooks_package
    from cyrene.tooling import executor, runtime_api

    reviews = []

    async def pre(_name, _arguments):
        return {"command": "curl https://example.com/after-hook"}

    async def review(**kwargs):
        reviews.append(kwargs)
        return "review-stopped-execution"

    monkeypatch.setattr(hooks_package, "run_pre_tool_hooks", pre)
    monkeypatch.setattr(runtime_api, "request_scope_elevation", review)

    result = await executor._execute_tool("Bash", {"command": "printf before-hook"}, None, 0, "", None)
    assert result == "review-stopped-execution"
    assert "curl https://example.com/after-hook" in reviews[0]["reason"]


@pytest.mark.asyncio
async def test_main_agent_hook_lifecycle_injects_context_and_emits_end_and_stop(monkeypatch):
    import cyrene.hooks as hooks_package
    from cyrene.agent import coordinator

    events = []
    fixed_contexts = []

    async def lifecycle(event, **kwargs):
        events.append((event, kwargs))
        return "context-from-hook" if event == "SessionStart" else ""

    async def successful(*_args, **kwargs):
        fixed_contexts.append(kwargs.get("fixed_ephemeral_system", ""))
        return "completed"

    monkeypatch.setattr(hooks_package, "run_lifecycle_hooks", lifecycle)
    monkeypatch.setattr(coordinator, "_run_chat_agent_impl", successful)
    assert await coordinator._run_chat_agent("hello", None, 0, "") == "completed"
    assert [event for event, _kwargs in events] == ["SessionStart", "SessionEnd"]
    assert "## Agent Hook Context\ncontext-from-hook" in fixed_contexts[0]

    events.clear()

    async def failed(*_args, **_kwargs):
        raise RuntimeError("agent failed")

    monkeypatch.setattr(coordinator, "_run_chat_agent_impl", failed)
    with pytest.raises(RuntimeError, match="agent failed"):
        await coordinator._run_chat_agent("hello", None, 0, "")
    assert [event for event, _kwargs in events] == ["SessionStart", "Stop"]


@pytest.mark.asyncio
async def test_subagent_hook_lifecycle_injects_context(monkeypatch):
    import cyrene.hooks as hooks_package
    import cyrene.tooling as tooling
    from cyrene import subagent
    from cyrene.agent import state

    events = []
    prompts = []

    async def lifecycle(event, **kwargs):
        events.append((event, kwargs))
        return "subagent-hook-context" if event == "SessionStart" else ""

    async def call_llm(messages, **_kwargs):
        prompts.append(json.loads(json.dumps(messages)))
        return {
            "content": "done",
            "tool_calls": [{"id": "quit-1", "function": {"name": "quit", "arguments": "{}"}}],
        }

    async def execute(_name, _args, *_rest, **_kwargs):
        return "ok"

    monkeypatch.setattr(hooks_package, "run_lifecycle_hooks", lifecycle)
    monkeypatch.setattr(state, "_call_llm", call_llm)
    monkeypatch.setattr(tooling, "execute_wire_tool", execute)
    await subagent.clear()
    await subagent.register("hook-worker", "finish", mode="execution")

    result = await subagent._run_subagent("hook-worker", "finish", None, 0, "")
    assert result == "done"
    assert [event for event, _kwargs in events] == ["SessionStart", "SessionEnd"]
    assert "subagent-hook-context" in prompts[0][0]["content"]


@pytest.mark.asyncio
async def test_cli_configuration_agent_proposes_only_structured_verified_hook(monkeypatch, tmp_path):
    from cyrene.hooks import config_agent

    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    proposals = []
    notifications = []
    records = {}

    class Hooks:
        def add_proposal(self, **kwargs):
            proposals.append(kwargs)
            return {"id": "proposal-1", "rationale": kwargs["rationale"], "hook": kwargs["hook"]}

    async def help_text(*_args):
        return "hook --stdin-json: consume one Agent event JSON object from stdin"

    async def call_model(*_args, **_kwargs):
        return {"tool_calls": [{"function": {"name": "submit_hook_assessment", "arguments": {
            "action": "propose", "rationale": "Documented JSON hook command",
            "event": "PreToolUse", "matcher": "Bash", "executable": str(executable),
            "args": ["hook", "--stdin-json"], "failure_policy": "open", "description": "Checks calls",
        }}}]}

    monkeypatch.setattr(config_agent, "hook_process_environment", lambda: {"PATH": str(tmp_path)})
    monkeypatch.setattr(config_agent.shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setattr(config_agent, "_help_text", help_text)
    monkeypatch.setattr(config_agent, "call_agent_model", call_model)
    monkeypatch.setattr(config_agent, "get_hook_service", lambda: Hooks())
    monkeypatch.setattr(config_agent, "get_setting", lambda key, default=None: records.get(key, default))
    monkeypatch.setattr(config_agent, "set_setting", lambda key, value: records.__setitem__(key, value))
    monkeypatch.setattr(config_agent, "append_notification", lambda **kwargs: notifications.append(kwargs))

    result = await config_agent.configure_cli({
        "id": "tool", "key": "cli:tool", "name": "Tool", "version": "1.0",
        "spec": {"tool": "tool"},
    })

    assert result["status"] == "pending_approval"
    assert proposals[0]["hook"]["runner"]["executable"] == str(executable)
    assert proposals[0]["hook"]["failure_policy"] == "open"
    assert notifications[0]["meta"]["category"] == "hook_approval"


@pytest.mark.asyncio
async def test_mise_cli_install_schedules_background_hook_configuration(monkeypatch, tmp_path):
    from cyrene.extensions import service
    from cyrene.hooks import config_agent

    saved = {}
    scheduled = []
    mise = tmp_path / "mise"
    mise.write_text("#!/bin/sh\n", encoding="utf-8")
    mise.chmod(mise.stat().st_mode | stat.S_IXUSR)

    class Tasks:
        def update(self, *_args, **_kwargs):
            return None

    class Process:
        returncode = 0

        async def communicate(self):
            return str(tmp_path / "installed-ripgrep").encode(), b""

    async def latest(*_args):
        return "15.1.0"

    async def run_manager(*_args, **_kwargs):
        return "", ""

    async def create_process(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(service, "_bundled_binary", lambda name: mise if name == "mise" else None)
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(service, "_save_extension_enabled", lambda *_args: None)
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "extension_environment", lambda: {})
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(config_agent, "schedule_cli_configuration", lambda extension, **kwargs: scheduled.append((extension, kwargs)))
    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()
    monkeypatch.setattr(extension_service, "_mise_exact_version", latest)
    monkeypatch.setattr(extension_service, "_run_manager", run_manager)

    result = await extension_service._install_mise("task-1", "cli", "ripgrep", {}, "user")
    assert result["version"] == "15.1.0"
    assert saved["extension_clis"][0]["id"] == "ripgrep"
    assert scheduled[0][0]["key"] == "cli:ripgrep"
    assert scheduled[0][1] == {"trigger": "install"}


@pytest.mark.asyncio
async def test_hook_process_has_minimal_environment_and_redacts_custom_secrets(tmp_path, monkeypatch):
    from cyrene.hooks import service

    script = tmp_path / "environment-hook.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'has_app_secret': 'OPENAI_API_KEY' in os.environ, 'custom': os.environ.get('HOOK_SECRET')}))\n"
        "print(os.environ.get('HOOK_SECRET', ''), file=sys.stderr)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    audits = []
    monkeypatch.setattr(service, "agent_process_environment", lambda: {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "OPENAI_API_KEY": "application-secret",
    })
    monkeypatch.setattr(service, "_audit", audits.append)
    hook = _hook(runner={
        "type": "script", "path": str(script), "args": [], "env": {"HOOK_SECRET": "hook-secret"},
    })

    output = await service.HookService().execute(hook, {"event": "SessionStart"})

    assert output == {"has_app_secret": False, "custom": "hook-secret"}
    assert audits[-1]["stderr"] == "[REDACTED]\n"
    assert "application-secret" not in json.dumps(audits[-1])
    assert "hook-secret" not in json.dumps(audits[-1])


@pytest.mark.asyncio
async def test_hook_rejects_missing_shebang_and_oversized_stdout(tmp_path, monkeypatch):
    from cyrene.hooks import service

    monkeypatch.setattr(service, "_audit", lambda _record: None)
    no_shebang = tmp_path / "no-shebang"
    no_shebang.write_text("print('hello')\n", encoding="utf-8")
    no_shebang.chmod(no_shebang.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(RuntimeError, match="shebang"):
        await service.HookService().execute(
            _hook(runner={"type": "script", "path": str(no_shebang), "args": [], "env": {}}),
            {"event": "SessionStart"},
        )

    oversized = tmp_path / "oversized.py"
    oversized.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.write('x' * (300 * 1024))\n",
        encoding="utf-8",
    )
    oversized.chmod(oversized.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(RuntimeError, match="stdout exceeded 256 KB"):
        await service.HookService().execute(
            _hook(runner={"type": "script", "path": str(oversized), "args": [], "env": {}}),
            {"event": "SessionStart"},
        )


def test_hook_api_crud_test_and_proposal_approval(tmp_path, monkeypatch):
    from cyrene.hooks import service
    from route import hooks as hook_routes

    settings = {}
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: settings.__setitem__(key, value))
    monkeypatch.setattr(service, "_AUDIT_FILE", tmp_path / "audit.jsonl")

    async def approve_review(*_args, **_kwargs):
        return None

    monkeypatch.setattr(hook_routes, "_review", approve_review)
    script = tmp_path / "api-hook.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'decision': 'allow', 'event': payload['event']}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    app = FastAPI()
    hook_routes.register_hook_routes(app.router, None, "")
    client = TestClient(app)
    payload = _hook(runner={"type": "script", "path": str(script), "args": [], "env": {}})

    created = client.post("/api/hooks", json=payload)
    assert created.status_code == 200
    hook_id = created.json()["hook"]["id"]
    assert client.get("/api/hooks").json()["hooks"][0]["id"] == hook_id
    tested = client.post(f"/api/hooks/{hook_id}/test", json={})
    assert tested.json()["output"] == {"decision": "allow", "event": "PreToolUse"}
    assert client.post(f"/api/hooks/{hook_id}/enabled", json={"enabled": False}).json()["hook"]["enabled"] is False

    proposal = service.get_hook_service().add_proposal(
        extension={"key": "cli:probe", "id": "probe", "name": "Probe"},
        hook={**payload, "name": "Proposed Hook"},
        rationale="Verified integration",
    )
    approved = client.post(f"/api/hooks/proposals/{proposal['id']}/decision", json={"approve": True}).json()
    assert approved["proposal"]["status"] == "approved"
    assert approved["hook"]["enabled"] is True
    assert client.delete(f"/api/hooks/{hook_id}").json() == {"ok": True}


def test_hook_management_ui_entry_and_i18n_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    overlay = (root / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    i18n = (root / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")
    hook_button = 't("settings.agentHooks")'
    source_button = 't("settings.extensionSources")'

    assert overlay.index(hook_button) < overlay.index(source_button)
    assert "/api/hooks" in overlay
    assert "settings.agentHooks" in i18n
    assert "settings.hookPendingApprovals" in i18n
