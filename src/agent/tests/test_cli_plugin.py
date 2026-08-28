from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.hook import PRE_TOOL_USE, HookEvent
from agent.plugin.plugin_impl.cyrene_cli import setup
from agent.plugin.plugin_impl.cyrene_cli.hooks import CliHookService


@pytest.fixture
def cli_hook_settings(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_cli import hooks as hooks_module

    values = {}
    monkeypatch.setattr(hooks_module, "get_setting", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(hooks_module, "set_setting", lambda key, value: values.__setitem__(key, value))
    monkeypatch.setattr(hooks_module, "agent_process_environment", lambda: dict(os.environ))
    monkeypatch.setattr(hooks_module, "_audit", lambda _record: None)
    return values


@pytest.mark.asyncio
async def test_cli_hook_dispatch_uses_tree_event_and_can_modify_tool_arguments(cli_hook_settings):
    hooks = CliHookService()
    hooks.save({
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
    }
    assert all(options["plugin_id"].startswith("cyrene_cli.") for _event, _handler, options in registered)
    assert all(options["hook_id"].startswith("cyrene-cli-") for _event, _handler, options in registered)
