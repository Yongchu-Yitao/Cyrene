"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import logging

from cyrene.core.context import ContextStoreRouter
from cyrene.core.hook import POST_TOOL_USE
from cyrene.core.observability import LOG_PREFIX, MAX_STRING_LENGTH, safe_log_value
from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry, PluginRuntime
from cyrene.core.session import AgentSession


def _operations(caplog) -> list[dict]:
    prefix = f"{LOG_PREFIX} "
    return [
        json.loads(record.getMessage()[len(prefix):])
        for record in caplog.records
        if record.getMessage().startswith(prefix)
    ]


def test_safe_log_value_redacts_secrets_and_bounds_payloads():
    value = safe_log_value(
        {
            "api_key": "do-not-log",
            "nested": {"Authorization": "Bearer private", "tokens": 17},
            "content": "x" * (MAX_STRING_LENGTH + 25),
        }
    )

    assert value["api_key"] == "<redacted>"
    assert value["nested"]["Authorization"] == "<redacted>"
    assert value["nested"]["tokens"] == 17
    assert value["content"].endswith("<truncated 25 chars>")


def test_context_hook_and_plugin_actions_are_structurally_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    observed = []
    store = ContextStoreRouter(tmp_path / "context")
    try:
        tree = store.create_tree("root", tree_id="audit-tree", root_id="root")
        hooks = store.hooks_for(tree.id)
        hooks.register(
            POST_TOOL_USE,
            lambda event: observed.append(event.payload),
            hook_id="audit-post-tool",
            plugin_id="audit.post-tool",
        )
        node = store.mount(tree.id, tree.root_id, {"role": "user", "content": "hello"})

        registry = PluginRegistry(include_core=False)
        registry.register_plugin(
            Plugin(
                "AuditEcho",
                "echo",
                {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "api_key": {"type": "string"},
                    },
                    "required": ["value", "api_key"],
                    "additionalProperties": False,
                },
                lambda arguments, _context: {"echo": arguments["value"]},
            ),
            source="test",
        )
        result = asyncio.run(
            PluginRuntime(registry).call(
                "AuditEcho",
                {"value": "hello", "api_key": "super-secret-value"},
                PluginContext(
                    tree=store,
                    tree_id=tree.id,
                    node_id=node.id,
                    hooks=hooks,
                    data={"run_id": "audit-run"},
                ),
                call_id="audit-call",
            )
        )
        asyncio.run(hooks.drain())
    finally:
        store.close()

    assert result.success is True
    assert observed[0]["result"]["value"] == {"echo": "hello"}
    operations = _operations(caplog)
    components = {item["component"] for item in operations}
    assert {
        "context.publisher",
        "context.router",
        "context.store",
        "hook.set",
        "hook.store",
        "plugin.registry",
        "plugin.runtime",
    } <= components
    assert any(
        item["component"] == "plugin.runtime"
        and item["action"] == "execute"
        and item["phase"] == "completed"
        and item["call_id"] == "audit-call"
        and item["success"] is True
        and "duration_ms" in item
        for item in operations
    )
    assert any(
        item["component"] == "hook.set"
        and item["action"] == "invoke"
        and item["hook_id"] == "audit-post-tool"
        for item in operations
    )
    assert any(
        item["component"] == "context.store"
        and item["action"] == "mount"
        and item["phase"] == "completed"
        and item["node_id"] == node.id
        for item in operations
    )
    assert "super-secret-value" not in "\n".join(
        record.getMessage() for record in caplog.records
    )


def test_agent_session_logs_state_transitions_and_output_events(tmp_path, caplog):
    caplog.set_level(logging.INFO)

    async def fake_model(_arguments, _context):
        return {
            "content": "done",
            "reasoning": "",
            "tool_calls": [],
            "model": "fake",
        }

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (
                Plugin(
                    "MiniMax",
                    "fake model",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugins"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
        tree_id="agent-audit-tree",
    )
    try:
        session.submit("hello", run_id="agent-audit-run")
        asyncio.run(session.drain())
        assert session.final_output("agent-audit-run")["content"] == "done"
    finally:
        session.close()

    operations = _operations(caplog)
    agent_operations = [
        item for item in operations if item["component"] == "cyrene.core.session"
    ]
    assert any(
        item["action"] == "submit"
        and item["phase"] == "completed"
        and item["run_id"] == "agent-audit-run"
        for item in agent_operations
    )
    assert any(
        item["action"] == "transition"
        and item["phase"] == "completed"
        and item["run_id"] == "agent-audit-run"
        and "duration_ms" in item
        for item in agent_operations
    )
    assert any(
        item["action"] == "emit_event"
        and item["event_type"] == "assistant.completed"
        and item["run_id"] == "agent-audit-run"
        for item in agent_operations
    )
