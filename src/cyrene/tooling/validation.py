"""Strict validation for gateway envelopes and capability arguments."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.results import ToolProtocolError


def ensure_object(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolProtocolError("invalid_arguments", f"`{field}` must be an object.")
    return dict(value)


def validate_schema(value: Any, schema: dict[str, Any], *, path: str = "arguments") -> None:
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    expected_type = type_map.get(expected)
    if expected_type is not None:
        valid = isinstance(value, expected_type)
        if expected in {"integer", "number"}:
            valid = valid and not isinstance(value, bool)
        if not valid:
            raise ToolProtocolError("invalid_arguments", f"`{path}` must be of type {expected}.")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for required in schema.get("required") or ():
            if required not in value:
                raise ToolProtocolError("invalid_arguments", f"Missing required field `{path}.{required}`.")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ToolProtocolError("invalid_arguments", f"Unknown field(s) for `{path}`: {', '.join(unknown)}.")
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                validate_schema(item, child_schema, path=f"{path}.{key}")
    if isinstance(value, list):
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], path=f"{path}[{index}]")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            raise ToolProtocolError("invalid_arguments", f"`{path}` may contain at most {schema['maxItems']} items.")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolProtocolError("invalid_arguments", f"`{path}` must be one of {schema['enum']}.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolProtocolError("invalid_arguments", f"`{path}` must be at least {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolProtocolError("invalid_arguments", f"`{path}` must be at most {schema['maximum']}.")
