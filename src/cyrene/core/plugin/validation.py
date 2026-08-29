"""JSON Schema validation at the Plugin execution boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import exceptions, validators


class PluginSchemaError(ValueError):
    """Raised when a Plugin declares an invalid input schema."""


class PluginInputValidationError(ValueError):
    """Raised when call arguments do not satisfy the current Plugin schema."""


def check_input_schema(schema: Mapping[str, Any]) -> None:
    """Reject malformed schemas when a Plugin is created."""

    normalized = dict(schema)
    validator = validators.validator_for(normalized)
    try:
        validator.check_schema(normalized)
    except exceptions.SchemaError as exc:
        raise PluginSchemaError(f"invalid Plugin input_schema: {exc.message}") from exc


def validate_plugin_arguments(
    plugin_name: str,
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> None:
    """Validate one call against the schema attached to the resolved Plugin."""

    normalized_schema = dict(schema)
    validator_type = validators.validator_for(normalized_schema)
    try:
        validator_type.check_schema(normalized_schema)
    except exceptions.SchemaError as exc:
        raise PluginSchemaError(
            f"Plugin {plugin_name!r} has an invalid input_schema: {exc.message}"
        ) from exc

    error = exceptions.best_match(
        validator_type(normalized_schema).iter_errors(dict(arguments))
    )
    if error is None:
        return

    path = "arguments"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    raise PluginInputValidationError(
        f"Invalid arguments for Plugin {plugin_name!r} at {path}: {error.message}"
    )


__all__ = [
    "PluginInputValidationError",
    "PluginSchemaError",
    "check_input_schema",
    "validate_plugin_arguments",
]
