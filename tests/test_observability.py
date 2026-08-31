"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import logging

from cyrene.core.context import ContextStoreRouter
from cyrene.core.hook import POST_TOOL_USE
from cyrene.core.observability import (
    LOG_PREFIX,
    MAX_EVENT_BYTES,
    MAX_STRING_LENGTH,
    log_operation,
    safe_log_value,
)
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
    private_content = "x" * (MAX_STRING_LENGTH + 25)
    private_command = "read /Users/alice/private.txt"
    private_path = r"C:\Users\alice\Documents\report.docx"
    value = safe_log_value(
        {
            "api_key": "do-not-log",
            "nested": {"Authorization": "Bearer private", "tokens": 17},
            "content": private_content,
            "command": private_command,
            "path": private_path,
            "diagnostic": r"failed under C:\Users\alice\Documents",
            "ordinary": "x" * (MAX_STRING_LENGTH + 25),
        }
    )

    assert value["api_key"] == "<redacted>"
    assert value["nested"]["Authorization"] == "<redacted>"
    assert value["nested"]["tokens"] == 17
    assert value["content"] == f"<redacted:{len(private_content)} chars>"
    assert value["command"] == f"<redacted:{len(private_command)} chars>"
    assert value["path"] == f"<redacted:{len(private_path)} chars>"
    assert value["diagnostic"] == r"failed under C:\Users\<user>\Documents"
    assert value["ordinary"].endswith("<truncated 25 chars>")


def test_operation_log_has_a_hard_size_limit(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("test.operation-size")

    log_operation(
        logger,
        "test.component",
        "large_event",
        phase="completed",
        details={f"field_{index}": "x" * MAX_STRING_LENGTH for index in range(100)},
    )

    message = caplog.records[-1].getMessage()
    prefix = f"{LOG_PREFIX} "
    assert message.startswith(prefix)
    assert len(message[len(prefix):].encode("utf-8")) <= MAX_EVENT_BYTES
    payload = json.loads(message[len(prefix):])
    assert payload["component"] == "test.component"
    assert payload["action"] == "large_event"
    assert payload["phase"] == "completed"
    assert payload["log_payload_truncated"] is True
    assert payload["original_payload_bytes"] > MAX_EVENT_BYTES
    assert payload["details"] == {
        "items": 100,
        "keys": [f"field_{index}" for index in range(20)],
        "type": "mapping",
    }


def test_routine_operations_are_debug_but_failures_remain_visible(caplog):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.operation-level")

    log_operation(logger, "test.component", "routine", phase="completed")
    log_operation(
        logger,
        "test.component",
        "failure",
        phase="failed",
        error=RuntimeError("broken"),
    )

    records = [record for record in caplog.records if record.name == logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    payload = json.loads(records[0].getMessage().removeprefix(f"{LOG_PREFIX} "))
    assert payload["action"] == "failure"
    assert payload["phase"] == "failed"
    assert payload["error"] == {"message": "broken", "type": "RuntimeError"}


def test_context_hook_and_plugin_actions_are_structurally_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
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
    caplog.set_level(logging.DEBUG)

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
