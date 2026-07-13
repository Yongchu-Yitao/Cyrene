from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


def test_app_use_is_one_stable_main_only_tool():
    from cyrene.registry_tools import get_active_tool_defs_for_actor

    main_before = get_active_tool_defs_for_actor("main")
    main_after = get_active_tool_defs_for_actor("main")
    main_names = [item["function"]["name"] for item in main_before]
    subagent_names = [item["function"]["name"] for item in get_active_tool_defs_for_actor("subagent")]

    assert main_names.count("app_use") == 1
    assert "app_use" not in subagent_names
    assert json.dumps(main_before, sort_keys=True) == json.dumps(main_after, sort_keys=True)


def test_app_use_schema_keeps_runtime_capabilities_out_of_function_enum():
    from cyrene.tool_impl.app_use import TOOL_DEF

    function = TOOL_DEF["function"]
    properties = function["parameters"]["properties"]
    assert function["name"] == "app_use"
    assert properties["operation"]["enum"] == [
        "list_targets", "connect", "call", "status", "disconnect"
    ]
    assert "enum" not in properties["capability"]
    assert function["parameters"]["required"] == ["operation"]


@pytest.mark.asyncio
async def test_app_use_round_keeps_identical_wire_tool_array(monkeypatch):
    from cyrene.agent import agent as agent_core

    calls = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "phase1",
                "function": {"name": "use_tools", "arguments": json.dumps({"task": "control TextEdit"})},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "app1",
                "function": {"name": "app_use", "arguments": json.dumps({"operation": "list_targets"})},
            }],
        },
        {
            "content": "App Use cache stability was verified successfully.",
            "tool_calls": [{
                "id": "done",
                "function": {"name": "quit", "arguments": json.dumps({"reply": "App Use cache stability was verified successfully."})},
            }],
        },
    ])

    async def fake_llm(messages, tools=None, **_kwargs):
        calls.append(json.dumps(tools, sort_keys=True, ensure_ascii=False))
        return next(responses)

    async def fake_execute(name, arguments, *_args, **_kwargs):
        assert name == "app_use"
        assert arguments == {"operation": "list_targets"}
        return json.dumps({"status": "success", "targets": []})

    app_tool = {
        "type": "function",
        "function": {
            "name": "app_use",
            "description": "stable gateway",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    quit_tool = {
        "type": "function",
        "function": {
            "name": "quit",
            "description": "finish",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    monkeypatch.setattr(agent_core, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_core, "_execute_tool", fake_execute)
    monkeypatch.setattr(agent_core, "get_active_tool_defs", lambda: [app_tool, quit_tool])
    monkeypatch.setattr(agent_core, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_core, "_streaming_reply_requested", lambda: False)

    result = await agent_core._run_main_agent("control TextEdit", [], None, 0, "db.sqlite3")
    assert result == "App Use cache stability was verified successfully."
    assert len(calls) == 3
    assert len(set(calls)) == 1
    assert calls[0].count('"name": "app_use"') == 1


@pytest.mark.asyncio
async def test_execute_app_use_validates_gateway_arguments(monkeypatch):
    from cyrene import app_use

    calls = []

    async def fake_rpc(operation, arguments, **_kwargs):
        calls.append((operation, arguments))
        return {"status": "success"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)

    invalid = await app_use.execute_app_use({"operation": "call", "capability": "snapshot"})
    assert invalid["status"] == "error"
    assert invalid["type"] == "invalid_arguments"

    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "snapshot",
        "parameters": {"max_nodes": 40},
    })
    assert result == {"status": "success"}
    assert calls == [("call", {
        "parameters": {"max_nodes": 40},
        "session_id": "session-1",
        "capability": "snapshot",
    })]


@pytest.mark.asyncio
async def test_visual_describe_converts_window_capture_to_text(monkeypatch):
    from cyrene import app_use
    from cyrene import attachments

    async def fake_rpc(_operation, _arguments, **_kwargs):
        return {
            "status": "success",
            "session_id": "session-1",
            "image_base64": "aW1hZ2U=",
            "mime_type": "image/png",
            "width": 800,
            "height": 600,
        }

    async def fake_vision(content, content_prompt=""):
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content_prompt == "Explain the chart"
        return {"vision_text": "A rising line chart.", "vision_model": "vision-test"}

    monkeypatch.setattr(app_use, "_electron_app_rpc", fake_rpc)
    monkeypatch.setattr(attachments, "run_vision_chat", fake_vision)
    result = await app_use.execute_app_use({
        "operation": "call",
        "session_id": "session-1",
        "capability": "visual_describe",
        "parameters": {"prompt": "Explain the chart"},
    })
    assert result["status"] == "success"
    assert result["visual_observation"] == "A rising line chart."
    assert result["vision_model"] == "vision-test"
    assert "image_base64" not in result


@pytest.mark.asyncio
async def test_app_use_tool_returns_structured_json(monkeypatch):
    from cyrene import app_use
    from cyrene.tool_impl import app_use as tool

    async def fake_execute(arguments):
        return {"status": "success", "operation": arguments["operation"], "targets": []}

    monkeypatch.setattr(app_use, "execute_app_use", fake_execute)
    result = await tool.handler({"operation": "list_targets"}, None, 0, "", None)
    parsed = json.loads(result)
    assert parsed == {"status": "success", "operation": "list_targets", "targets": []}


def test_app_use_result_limiter_prunes_nodes():
    from cyrene.app_use import format_app_use_result

    result = {
        "status": "success",
        "nodes": [{"ref": f"e{i}", "name": "x" * 100} for i in range(100)],
    }
    rendered = format_app_use_result(result, max_chars=900)
    parsed = json.loads(rendered)
    assert len(rendered) <= 900
    assert parsed["status"] == "success"
    assert parsed["truncated"] is True
    assert len(parsed["nodes"]) < 100


def test_app_use_result_limiter_prunes_nested_verification():
    from cyrene.app_use import format_app_use_result

    result = {
        "status": "success",
        "summary": "Pressed Save.",
        "verification": {
            "status": "success",
            "nodes": [{"ref": f"e{i}", "name": "x" * 100} for i in range(100)],
        },
    }
    rendered = format_app_use_result(result, max_chars=900)
    parsed = json.loads(rendered)
    assert len(rendered) <= 900
    assert parsed["status"] == "success"
    assert parsed["summary"] == "Pressed Save."
    assert parsed["verification"]["truncated"] is True
    assert len(parsed["verification"]["nodes"]) < 100


@pytest.mark.asyncio
async def test_electron_app_rpc_uses_app_endpoint(monkeypatch):
    from cyrene import app_use

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success"}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setenv("CYRENE_ELECTRON_RPC_PORT", "43210")
    monkeypatch.setenv("CYRENE_ELECTRON_RPC_TOKEN", "test-token")
    monkeypatch.setattr(app_use.httpx, "AsyncClient", FakeClient)

    result = await app_use._electron_app_rpc("list_targets", {})
    assert result == {"status": "success"}
    assert captured["url"] == "http://127.0.0.1:43210/app/rpc"
    assert captured["post_kwargs"]["headers"]["X-Cyrene-Token"] == "test-token"
    assert json.loads(captured["post_kwargs"]["content"])["method"] == "list_targets"


def test_electron_main_wires_app_rpc_and_quick_chat_origin():
    from pathlib import Path

    main = (Path(__file__).resolve().parents[1] / "electron" / "main.js").read_text(encoding="utf-8")
    assert "require('./app-use')" in main
    assert "'/app/rpc'" in main
    assert "handleAppUseRpc" in main
    assert "captureQuickChatOrigin" in main


def test_platform_provider_scripts_exist():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "electron"
    assert (root / "app-use-macos.jxa").is_file()
    assert (root / "app-use-windows.ps1").is_file()
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    extra_resources = package["build"]["extraResources"]
    assert {
        "from": "app-use-macos.jxa",
        "to": "app-use/app-use-macos.jxa",
    } in extra_resources
    assert {
        "from": "app-use-windows.ps1",
        "to": "app-use/app-use-windows.ps1",
    } in extra_resources
    # osascript and PowerShell cannot execute scripts from Electron's ASAR FS.
    assert "app-use-macos.jxa" not in package["build"]["files"]
    assert "app-use-windows.ps1" not in package["build"]["files"]


def test_agent_never_bypasses_an_unavailable_app_use_provider():
    from pathlib import Path

    prompts = (Path(__file__).resolve().parents[1] / "src" / "cyrene" / "agent" / "prompts.py").read_text(
        encoding="utf-8"
    )
    rule = (
        "never bypass it with Bash, osascript, PowerShell, direct file edits, "
        "or another tool that imitates the requested App Use action"
    )
    assert prompts.count(rule) == 2
