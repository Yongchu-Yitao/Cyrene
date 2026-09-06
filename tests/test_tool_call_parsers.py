"""Focused tests for Provider tool-call parsing boundaries."""

from __future__ import annotations

import asyncio
import json

from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins import ensure_model_router
from cyrene.plugins.builtin.edit import plugin as edit_plugin
from cyrene.plugins.tool_call_parsers import (
    GENERIC_TOOL_CALL_PARSER,
    _drop_optional_nulls,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_strict_wire_nulls_are_removed_only_for_optional_fields() -> None:
    schema = {
        "type": "object",
        "properties": {
            "required_value": {"type": ["string", "null"]},
            "optional_value": {"type": "string"},
            "optional_nullable": {"type": ["string", "null"]},
            "nested": {
                "type": "object",
                "properties": {
                    "keep": {"type": "string"},
                    "drop": {"type": "string"},
                },
                "required": ["keep"],
            },
        },
        "required": ["required_value"],
    }

    assert _drop_optional_nulls(
        {
            "required_value": None,
            "optional_value": None,
            "optional_nullable": None,
            "nested": {"keep": "yes", "drop": None},
        },
        schema,
    ) == {
        "required_value": None,
        "optional_nullable": None,
        "nested": {"keep": "yes"},
    }


def _target_plugin(*, agent_exposure: str = "discoverable") -> Plugin:
    return Plugin(
        name="DeferredEdit",
        description="Test an unexposed edit-like tool.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string", "minLength": 1},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
        handler=lambda arguments, _context: arguments,
        metadata={
            "agent_exposure": agent_exposure,
            "argument_aliases": {
                "old_text": "old_string",
                "new_text": "new_string",
            },
        },
    )


def _runtime(*, agent_exposure: str = "discoverable") -> PluginRuntime:
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        _target_plugin(agent_exposure=agent_exposure),
        source="test",
    )
    ensure_model_router(registry)
    return PluginRuntime(registry)


def _parse(runtime: PluginRuntime, arguments: dict) -> object:
    return run(
        runtime.call(
            GENERIC_TOOL_CALL_PARSER,
            arguments,
            PluginContext(data={"agent_id": "main"}),
        )
    )


def test_parser_resolves_unexposed_registered_tool_and_declared_aliases():
    runtime = _runtime()

    parsed = _parse(
        runtime,
        {
            "tools": [],
            "tool_calls": [
                {
                    "id": "call-edit",
                    "type": "function",
                    "function": {
                        "name": "DeferredEdit",
                        "arguments": json.dumps(
                            {
                                "path": "game.js",
                                "oldText": "before",
                                "new-text": "after",
                            }
                        ),
                    },
                }
            ],
        },
    )

    assert parsed.success is True
    assert parsed.value == {
        "tool_calls": [
            {
                "id": "call-edit",
                "name": "DeferredEdit",
                "arguments": {
                    "path": "game.js",
                    "old_string": "before",
                    "new_string": "after",
                },
                "arguments_normalized": True,
            }
        ]
    }


def test_minimax_edit_aliases_execute_through_unexposed_tool(tmp_path):
    target = tmp_path / "game.js"
    target.write_text("before\n", encoding="utf-8")
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(edit_plugin, source="test")
    ensure_model_router(registry)
    runtime = PluginRuntime(registry)

    parsed = _parse(
        runtime,
        {
            "tools": [],
            "tool_calls": [
                {
                    "id": "call-edit",
                    "function": {
                        "name": "Edit",
                        "arguments": json.dumps(
                            {
                                "path": str(target),
                                "old_text": "before",
                                "newText": "after",
                            }
                        ),
                    },
                }
            ],
        },
    )

    assert parsed.success is True
    call = parsed.value["tool_calls"][0]
    executed = run(
        runtime.call(
            call["name"],
            call["arguments"],
            call_id=call["id"],
            arguments_normalized=call["arguments_normalized"],
        )
    )
    assert executed.success is True
    assert target.read_text(encoding="utf-8") == "after\n"


def test_parser_defers_invalid_arguments_to_runtime_tool_failure():
    runtime = _runtime()

    parsed = _parse(
        runtime,
        {
            "tools": [],
            "tool_calls": [
                {
                    "id": "call-edit",
                    "function": {
                        "name": "DeferredEdit",
                        "arguments": '{"path":"game.js","old_text":"before"}',
                    },
                }
            ],
        },
    )

    assert parsed.success is True
    call = parsed.value["tool_calls"][0]
    executed = run(
        runtime.call(
            call["name"],
            call["arguments"],
            call_id=call["id"],
            arguments_normalized=call["arguments_normalized"],
        )
    )
    assert executed.success is False
    assert executed.failure is not None
    assert executed.failure.error_code == "plugin_invalid_arguments"
    assert executed.failure.retryable is True
    assert executed.failure.retry_scope == "different_arguments"


def test_parser_does_not_make_hidden_tool_model_callable():
    runtime = _runtime(agent_exposure="hidden")

    parsed = _parse(
        runtime,
        {
            "tools": [],
            "tool_calls": [
                {
                    "function": {
                        "name": "DeferredEdit",
                        "arguments": "{}",
                    },
                }
            ],
        },
    )

    assert parsed.success is False
    assert "requested unavailable tool: DeferredEdit" in parsed.error
    assert parsed.failure is not None
    assert parsed.failure.error_code == "provider_tool_unavailable"
    assert parsed.failure.retryable is True
    assert parsed.failure.retry_scope == "different_arguments"
    assert parsed.failure.circuit_scope == "none"

    repeated = _parse(
        runtime,
        {
            "tools": [],
            "tool_calls": [
                {
                    "function": {
                        "name": "DeferredEdit",
                        "arguments": "{}",
                    },
                }
            ],
        },
    )

    assert repeated.success is False
    assert repeated.failure is not None
    assert repeated.failure.error_code == "provider_tool_unavailable"
