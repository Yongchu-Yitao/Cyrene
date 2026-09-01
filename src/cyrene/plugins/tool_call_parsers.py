"""Hidden Plugin implementations for model tool-call protocol parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from cyrene.core.plugin.plugin import Plugin, PluginContext
from cyrene.core.plugin.validation import (
    normalize_plugin_arguments,
    validate_plugin_arguments,
)
from cyrene.model.messages import parse_tool_arguments


GENERIC_TOOL_CALL_PARSER = "GenericToolCallParser"
CODEX_OAUTH_TOOL_CALL_PARSER = "CodexOAuthToolCallParser"

_PARSER_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_calls": {"type": "array"},
        "tools": {"type": "array"},
    },
    "required": ["tool_calls"],
    "additionalProperties": False,
}


def _iter_calls(value: Any) -> Sequence[Any]:
    return (
        value
        if isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        else ()
    )


def _tool_schemas(value: Any) -> dict[str, Mapping[str, Any]]:
    schemas: dict[str, Mapping[str, Any]] = {}
    for raw in _iter_calls(value):
        if not isinstance(raw, Mapping):
            continue
        function = raw.get("function")
        source = function if isinstance(function, Mapping) else raw
        name = str(source.get("name") or "").strip()
        schema = source.get("parameters") or source.get("input_schema")
        if name and isinstance(schema, Mapping):
            schemas[name] = schema
    return schemas


def _canonical_arguments(
    name: str,
    arguments: Mapping[str, Any],
    schemas: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    schema = schemas.get(name)
    if schema is None:
        raise ValueError(f"Model Provider Plugin requested unavailable tool: {name}")
    normalized = normalize_plugin_arguments(arguments, schema).arguments
    validate_plugin_arguments(name, normalized, schema)
    return normalized


def _generic_handler(
    arguments: dict[str, Any],
    _context: PluginContext,
) -> dict[str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    schemas = _tool_schemas(arguments.get("tools"))
    for raw in _iter_calls(arguments.get("tool_calls")):
        if not isinstance(raw, Mapping):
            raise ValueError("Model Provider Plugin returned an invalid tool call")
        function = raw.get("function")
        source = function if isinstance(function, Mapping) else raw
        name = str(source.get("name") or "").strip()
        if not name:
            raise ValueError("Model Provider Plugin tool call is missing a name")
        calls.append(
            {
                "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                "name": name,
                "arguments": _canonical_arguments(
                    name,
                    parse_tool_arguments(source.get("arguments")),
                    schemas,
                ),
                "arguments_normalized": True,
            }
        )
    return {"tool_calls": calls}


def _codex_oauth_handler(
    arguments: dict[str, Any],
    _context: PluginContext,
) -> dict[str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    schemas = _tool_schemas(arguments.get("tools"))
    for raw in _iter_calls(arguments.get("tool_calls")):
        if not isinstance(raw, Mapping):
            raise ValueError("Codex OAuth Provider Plugin returned an invalid tool call")
        name = str(raw.get("name") or "").strip()
        parsed_arguments = raw.get("arguments")
        if not name:
            raise ValueError("Codex OAuth Provider Plugin tool call is missing a name")
        if not isinstance(parsed_arguments, Mapping):
            raise ValueError(
                f"Codex OAuth Provider Plugin arguments for {name} must be an object"
            )
        calls.append(
            {
                "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                "name": name,
                "arguments": _canonical_arguments(
                    name,
                    parsed_arguments,
                    schemas,
                ),
                "arguments_normalized": True,
            }
        )
    return {"tool_calls": calls}


def _parser_plugin(name: str, description: str, handler) -> Plugin:
    return Plugin(
        name=name,
        description=description,
        input_schema=_PARSER_INPUT_SCHEMA,
        handler=handler,
        metadata={
            "model_visible": False,
            "agent_exposure": "hidden",
            "permission_review": False,
            "public_errors": True,
            "required": True,
            "tool_call_parser": True,
        },
    )


GENERIC_TOOL_CALL_PARSER_PLUGIN = _parser_plugin(
    GENERIC_TOOL_CALL_PARSER,
    "Parse standard and OpenAI-compatible Provider Plugin tool calls.",
    _generic_handler,
)
CODEX_OAUTH_TOOL_CALL_PARSER_PLUGIN = _parser_plugin(
    CODEX_OAUTH_TOOL_CALL_PARSER,
    "Parse the canonical structured-action output from Codex OAuth.",
    _codex_oauth_handler,
)


__all__ = [
    "CODEX_OAUTH_TOOL_CALL_PARSER",
    "CODEX_OAUTH_TOOL_CALL_PARSER_PLUGIN",
    "GENERIC_TOOL_CALL_PARSER",
    "GENERIC_TOOL_CALL_PARSER_PLUGIN",
]
