"""Hidden Plugin implementations for model tool-call protocol parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from cyrene.core.plugin.context import run_context_value
from cyrene.core.plugin.execution import require_plugin_execution
from cyrene.core.plugin.plugin import (
    Plugin,
    PluginContext,
    PluginExecutionError,
    PluginFailure,
)
from cyrene.core.plugin.registry import PluginNotFoundError, PluginUnavailableError
from cyrene.core.plugin.validation import normalize_plugin_arguments
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


def _provider_tool_call_error(
    message: str,
    *,
    error_code: str = "provider_tool_call_invalid",
) -> PluginExecutionError:
    """Return a non-circuiting failure that requires a changed model call.

    Parser Plugins are runtime infrastructure. Treating their deterministic
    input failures like ordinary tool crashes opens the run-scoped Plugin
    circuit and guarantees that AgentSession's recovery attempt fails again.
    """

    return PluginExecutionError(PluginFailure(
        error_code=error_code,
        message=message,
        retryable=True,
        retry_scope="different_arguments",
        circuit_scope="none",
    ))


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


def _drop_optional_nulls(value: Any, schema: Mapping[str, Any]) -> Any:
    """Undo nullable placeholders introduced by strict wire schemas.

    OpenAI strict function schemas require every property to be present. The
    provider may therefore return ``null`` for a field that is optional in the
    Plugin's authoritative schema. Drop only those optional nulls; required
    nulls and all other values remain subject to ordinary runtime validation.
    """

    properties = schema.get("properties")
    if isinstance(value, Mapping) and isinstance(properties, Mapping):
        raw_required = schema.get("required")
        required = {
            str(name)
            for name in (raw_required if isinstance(raw_required, list) else ())
            if isinstance(name, str)
        }
        result: dict[str, Any] = {}
        for raw_name, item in value.items():
            name = str(raw_name)
            child_schema = properties.get(name)
            if (
                item is None
                and name not in required
                and isinstance(child_schema, Mapping)
                and not _schema_accepts_null(child_schema)
            ):
                continue
            result[name] = (
                _drop_optional_nulls(item, child_schema)
                if isinstance(child_schema, Mapping)
                else item
            )
        return result
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, Mapping):
        return [_drop_optional_nulls(item, items) for item in value]
    return value


def _schema_accepts_null(schema: Mapping[str, Any]) -> bool:
    expected = schema.get("type")
    if expected == "null":
        return True
    if isinstance(expected, list) and "null" in expected:
        return True
    enum = schema.get("enum")
    if isinstance(enum, list) and None in enum:
        return True
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and any(
            isinstance(branch, Mapping) and _schema_accepts_null(branch)
            for branch in branches
        ):
            return True
    return False


def _canonical_arguments(
    name: str,
    arguments: Mapping[str, Any],
    schemas: Mapping[str, Mapping[str, Any]],
    context: PluginContext,
) -> dict[str, Any]:
    schema = schemas.get(name)
    aliases: Mapping[str, str] = {}
    execution = require_plugin_execution()
    agent_id = str(run_context_value(context, "agent_id", "main") or "main")
    try:
        plugin = execution.runtime.registry.resolve(name, agent_id=agent_id)
    except (PluginNotFoundError, PluginUnavailableError):
        plugin = None
    if plugin is not None:
        if (
            plugin.kind != "tool"
            or not plugin.model_visible
            or (schema is None and plugin.agent_exposure == "hidden")
        ):
            raise _provider_tool_call_error(
                f"Model Provider Plugin requested unavailable tool: {name}",
                error_code="provider_tool_unavailable",
            )
        aliases = plugin.argument_aliases
        if schema is None:
            schema = plugin.input_schema
    if schema is None:
        raise _provider_tool_call_error(
            f"Model Provider Plugin requested unavailable tool: {name}",
            error_code="provider_tool_unavailable",
        )
    return normalize_plugin_arguments(
        _drop_optional_nulls(arguments, schema),
        schema,
        property_aliases=aliases,
    ).arguments


def _generic_handler(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    schemas = _tool_schemas(arguments.get("tools"))
    try:
        for raw in _iter_calls(arguments.get("tool_calls")):
            if not isinstance(raw, Mapping):
                raise _provider_tool_call_error(
                    "Model Provider Plugin returned an invalid tool call"
                )
            function = raw.get("function")
            source = function if isinstance(function, Mapping) else raw
            name = str(source.get("name") or "").strip()
            if not name:
                raise _provider_tool_call_error(
                    "Model Provider Plugin tool call is missing a name"
                )
            calls.append(
                {
                    "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                    "name": name,
                    "arguments": _canonical_arguments(
                        name,
                        parse_tool_arguments(source.get("arguments")),
                        schemas,
                        context,
                    ),
                    "arguments_normalized": True,
                }
            )
    except PluginExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise _provider_tool_call_error(
            "Model Provider Plugin returned invalid tool-call arguments"
        ) from exc
    return {"tool_calls": calls}


def _codex_oauth_handler(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    schemas = _tool_schemas(arguments.get("tools"))
    try:
        for raw in _iter_calls(arguments.get("tool_calls")):
            if not isinstance(raw, Mapping):
                raise _provider_tool_call_error(
                    "Codex OAuth Provider Plugin returned an invalid tool call"
                )
            name = str(raw.get("name") or "").strip()
            parsed_arguments = raw.get("arguments")
            if not name:
                raise _provider_tool_call_error(
                    "Codex OAuth Provider Plugin tool call is missing a name"
                )
            if not isinstance(parsed_arguments, Mapping):
                raise _provider_tool_call_error(
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
                        context,
                    ),
                    "arguments_normalized": True,
                }
            )
    except PluginExecutionError:
        raise
    except (TypeError, ValueError) as exc:
        raise _provider_tool_call_error(
            "Codex OAuth Provider Plugin returned invalid tool-call arguments"
        ) from exc
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
