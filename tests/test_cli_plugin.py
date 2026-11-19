"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cyrene.core.hook import HOOK_EVENTS, PRE_TOOL_USE, HookEvent
from cyrene.plugins.builtin.cyrene_cli import setup
from cyrene.plugins.builtin.cyrene_cli.hooks import CliHookService


@pytest.fixture
def cli_hook_settings(monkeypatch):
    from cyrene.plugins.builtin.cyrene_cli import hooks as hooks_module

    values = {}
    monkeypatch.setattr(hooks_module, "get_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(hooks_module, "set_setting", lambda key, value: values.__setitem__(key, value))
    monkeypatch.setattr(hooks_module, "agent_process_environment", lambda: dict(os.environ))
    monkeypatch.setattr(hooks_module, "_audit", lambda _record: None)
    return values


@pytest.mark.asyncio
async def test_cli_hook_dispatch_uses_tree_event_and_can_modify_tool_arguments(cli_hook_settings):
    hooks = CliHookService()
    hook = hooks.save({
        "name": "rewrite",
        "event": "PreToolUse",
        "matcher": "Bash",
        "enabled": True,
        "failure_policy": "block",
        "runner": {
            "type": "command",
            "executable": sys.executable,
            "args": [
                "-c",
                "import json,sys; event=json.load(sys.stdin); "
                "print(json.dumps({'decision':'modify','arguments':{'command':event['tool']['arguments']['command']+' --color=never'}}))",
            ],
            "env": {},
        },
    })

    decision = await hooks.dispatch(HookEvent(
        PRE_TOOL_USE,
        "tree-one",
        datetime.now(timezone.utc),
        payload={"tool": {"name": "Bash", "arguments": {"command": "rg TODO"}}},
    ))

    assert decision == {
        "decision": "modify",
        "arguments": {"command": "rg TODO --color=never"},
    }
    tested = await hooks.test(hook["id"], {"tool": {"name": "Bash", "arguments": {"command": "rg TODO"}}})
    assert tested == {"ok": True, "output": decision}


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["open", "block"])
async def test_unsupported_cli_output_reports_the_actual_dispatch_outcome(cli_hook_settings, monkeypatch, policy):
    from cyrene.plugins.builtin.cyrene_cli import hooks as hooks_module

    audit = []
    monkeypatch.setattr(hooks_module, "_audit", audit.append)
    hooks = CliHookService()
    hook = hooks.save({
        "name": "unsupported", "enabled": True, "event": "PreToolUse", "matcher": "Read*", "failure_policy": policy,
        "runner": {"type": "command", "executable": sys.executable,
                   "args": ["-c", "print('{\"decision\":\"allow\",\"unknown_field\":\"hello\"}')"]},
    })
    payload = {"tool": {"name": "ReadFile", "arguments": {"path": "example"}}}
    live = await hooks.dispatch(HookEvent(PRE_TOOL_USE, "tree", datetime.now(timezone.utc), payload=payload))
    tested = await hooks.test(hook["id"], payload)
    assert tested["output"] == live
    assert live["decision"] == ("block" if policy == "block" else "allow")
    assert not tested["ok"] and "unknown_field" in tested["error"] and "PreToolUse" in tested["error"]
    assert [(r["test"], r["status"]) for r in audit if r["kind"] == "execution"] == [(False, "error"), (True, "error")]
    assert all("unknown_field" in r["error"] for r in audit if r["kind"] == "execution")


@pytest.mark.asyncio
@pytest.mark.parametrize(("event", "output", "expected"), [
    ("SessionStart", {"context": "  persona  "}, {"context": "persona"}),
    ("TurnStart", {"context": "turn"}, {"context": "turn"}),
    ("PreToolUse", {"decision": "block", "reason": "blocked"}, {"decision": "block", "reason": "blocked"}),
    ("PostToolUse", {}, {}), ("SessionEnd", {}, {}), ("Stop", {}, {}),
])
async def test_cli_hook_test_returns_effective_result(cli_hook_settings, event, output, expected):
    service = CliHookService()
    hook = service.save({
        "name": "example", "enabled": True, "event": event, "matcher": "[Rr]ead?*",
        "runner": {"type": "command", "executable": sys.executable, "args": ["-c", f"print({json.dumps(output)!r})"]},
    })
    assert await service.test(hook["id"]) == {"ok": True, "output": expected}



@pytest.mark.parametrize("action", ["context", "modify", "block", "nested", "model_error"])
def test_pre_tool_context_reaches_agent_before_execution(cli_hook_settings, tmp_path, action):
    from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
    from cyrene.core.session import AgentSession
    from cyrene.workbench.core_adapter.bridge import workbench_events

    service = CliHookService()
    prompt = "Before this call, announce the check."
    output = {"context": prompt}
    if action == "modify":
        output.update(decision="modify", arguments={"value": "edited"})
    if action == "block":
        output.update(decision="block", reason="blocked by hook")
    hook = service.save({
        "name": "before probe", "event": "PreToolUse", "matcher": "Probe", "enabled": True,
        "runner": {"type": "command", "executable": sys.executable,
                   "args": ["-c", f"print({json.dumps(output)!r})"]},
    })
    assert asyncio.run(service.test(hook["id"]))["output"]["context"] == prompt
    trace = []
    final_messages = []
    reply = "I am starting the check."
    tool_call = {"id": "one", "name": "Probe", "arguments": {"value": "original"}}
    if action == "nested":
        tool_call = {"id": "one", "name": "toolbox", "arguments": {
            "operation": "invoke", "name": "Probe", "arguments": {"value": "original"},
        }}

    async def model(arguments, context):
        if context.data.get("model_call_kind") == "pre_tool_context":
            trace.append("model read hook")
            assert arguments["tools"] == []
            assert prompt in arguments["messages"][-1]["content"]
            assert "Use Probe" in str(arguments["messages"])
            if action == "model_error":
                return {"tool_calls": [tool_call]}
            return {"content": reply, "tool_calls": []}
        if not trace:
            trace.append("requested tool")
            return {"content": "", "tool_calls": [tool_call]}
        final_messages.extend(arguments["messages"])
        return {"content": "done", "tool_calls": []}

    async def probe(arguments, _context):
        trace.append(arguments["value"])
        return "tool result"

    registry = PluginRegistry()
    registry.register_pack(PluginPack("case", "case", (
        Plugin("MiniMax", "model", {"type": "object"}, model, kind="model"),
        Plugin("Probe", "test tool", {"type": "object", "properties": {"value": {"type": "string"}}},
               probe, metadata={"read_only": True}),
    ), setup=setup), source="test")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    session = AgentSession(tmp_path / "data", tmp_path, plugin_dir, registry=registry,
                           load_plugins=False, inherit_application_scope=False,
                           plugin_services={"cli": SimpleNamespace(hooks=service)})
    live_ids = []
    def observe(event):
        if event.type == "assistant.pre_tool_reply":
            projected = workbench_events(event)
            assert projected[-1]["response"] == reply
            live_ids.append(projected[-1]["eventId"])
            trace.append("visible reply")

    try:
        session.subscribe(observe)
        session.submit("Use Probe", run_id="run")
        asyncio.run(session.drain())
        if action == "model_error":
            assert trace == ["requested tool", "model read hook"]
            assert "pre_tool_context_failed" in str(final_messages)
            return
        expected = ["requested tool", "model read hook", "visible reply"]
        if action != "block":
            expected.append("edited" if action == "modify" else "original")
        assert trace == expected
        assert any(m["role"] == "assistant" and m.get("content") == reply for m in final_messages)
        assert prompt in str(final_messages)
        replay = [e for e in session.events() if e.type == "assistant.pre_tool_reply"]
        assert [workbench_events(e)[-1]["eventId"] for e in replay] == live_ids
        assert len(live_ids) == 1
        if action == "block":
            assert "blocked by hook" in str(final_messages)
    finally:
        session.close()


def test_cli_pack_binds_all_supported_events_to_the_new_hook_set(cli_hook_settings):
    registered = []

    class Hooks:
        def list(self):
            return ()

        def register(self, event, handler, **options):
            registered.append((event, handler, options))

    service = SimpleNamespace(hooks=CliHookService())
    context = SimpleNamespace(services={"cli": service}, hooks=Hooks())

    setup(context)

    assert {event for event, _handler, _options in registered} == {
        "PreToolUse", "PostToolUse", "SessionStart", "TurnStart", "SessionEnd", "Stop",
        "ModelStart", "ConversationTurnCommitted", "ContextUsed", "ContextChange",
    }
    assert all(options["plugin_id"].startswith("cyrene_cli.") for _event, _handler, options in registered)
    assert all(options["hook_id"].startswith("cyrene-cli-") for _event, _handler, options in registered)


@pytest.mark.asyncio
@pytest.mark.parametrize("event", sorted(HOOK_EVENTS))
async def test_all_events_accept_independent_actions(cli_hook_settings, event):
    service = CliHookService()
    actions = [{"message": "literal"}, {"context": "guidance"}, {"decision": "allow", "target": "tool"},
               {"decision": "modify", "target": "result", "arguments": {"value": "replacement"}},
               {"decision": "ask", "target": "model", "question": "Proceed?"},
               {"decision": "block", "target": "tool", "reason": "halt"}]
    script = f"print({json.dumps({'actions': actions})!r})"
    hook = service.save({"name": "all actions", "event": event, "enabled": True,
                         "runner": {"type": "command", "executable": sys.executable, "args": ["-c", script]}})
    preview = await service.test(hook["id"])
    assert preview["ok"]
    assert preview["output"]["message"] == "literal"
    assert "guidance" in str(preview["output"]) and "Proceed?" in str(preview["output"])


@pytest.mark.parametrize("mode", ["mixed", "ask", "tool_ask", "block", "resume"])
def test_user_hook_actions_in_a_running_conversation(cli_hook_settings, tmp_path, mode):
    from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
    from cyrene.core.session import AgentSession
    from cyrene.workbench.core_adapter.bridge import workbench_events

    service = CliHookService()
    def add(event, actions):
        service.save({"name": event, "event": event, "enabled": True, "runner": {
            "type": "command", "executable": sys.executable,
            "args": ["-c", f"print({json.dumps({'actions': actions})!r})"],
        }})

    if mode == "mixed":
        add("TurnStart", [{"decision": "modify", "target": "tool", "arguments": {"name": "edited"}}])
        add("PostToolUse", [{"message": "literal notice"}, {"context": "read the redacted result"},
                            {"decision": "modify", "target": "result", "arguments": {"value": "redacted"}}])
    elif mode == "tool_ask":
        add("PreToolUse", [{"decision": "ask", "target": "tool", "question": "Run the probe?"}])
    elif mode == "ask":
        add("ModelStart", [{"message": "before request"}, {"decision": "ask", "target": "model", "question": "Which color?"}])
    elif mode == "block":
        add("TurnStart", [{"decision": "block", "target": "model", "reason": "stopped before request"}])
    else:
        add("ConversationTurnCommitted", [{"message": "saved"}, {"context": "remember this on resume"}])
    messages = []
    effects = []
    async def model(arguments, _):
        messages.append(arguments["messages"])
        if (mode == "mixed" and len(messages) == 1) or (mode == "tool_ask" and len(messages) <= 2):
            return {"content": "", "tool_calls": [{"id": "call" + str(len(messages)), "name": "Probe", "arguments": {"name": "old"}}]}
        return {"content": "done", "tool_calls": []}

    async def probe(arguments, _):
        effects.append(arguments)
        return "original sensitive result"

    registry = PluginRegistry()
    registry.register_pack(PluginPack("integration", "integration", (
        Plugin("MiniMax", "model", {"type": "object"}, model, kind="model"),
        Plugin("Probe", "tool", {"type": "object", "properties": {"name": {"type": "string"}}},
               probe, metadata={"read_only": True}),
    ), setup=setup), source="test")
    root = tmp_path / "plugins"
    root.mkdir()
    def open_session():
        return AgentSession(tmp_path / "data", tmp_path, root, registry=registry, load_plugins=False,
                            inherit_application_scope=False, plugin_services={"cli": SimpleNamespace(hooks=service)})
    session = open_session()
    try:
        session.submit("run", run_id="first")
        asyncio.run(session.drain())
        if mode == "mixed":
            assert effects == [{"name": "edited"}]
            assert len(messages) == 2 and "redacted" in str(messages[-1])
            assert "read the redacted result" in str(messages[-1])
            assert "original sensitive result" not in str(messages[-1])
            assert "literal notice" not in str(messages)
            event, = [e for e in session.events() if e.type == "hook.message"]
            assert workbench_events(event)[0]["payload"]["message"] == "literal notice"
        elif mode == "tool_ask":
            question = session.pending_output()
            assert question["text"] == "Run the probe?" and not effects
            session.answer(question["id"], "yes")
            asyncio.run(session.drain())
            assert effects == [{"name": "old"}] and session.pending_output() is None
        elif mode == "ask":
            assert messages == []
            question = session.pending_output()
            assert question["text"] == "Which color?"
            session.answer(question["id"], "green")
            asyncio.run(session.drain())
            assert len(messages) == 1 and "green" in str(messages[0])
        elif mode == "block":
            assert not messages and "stopped before request" in str(session.final_output("first"))
        else:
            asyncio.run(session.hooks.conversation_turn_committed({"event_id": "commit", "run_id": "first"}))
            session.close()
            session = open_session()
            session.submit("continue", run_id="second")
            asyncio.run(session.drain())
            assert "remember this on resume" in str(messages[-1])
            assert sum(e.type == "hook.message" for e in session.events()) == 1
    finally:
        session.close()
