"""JSON Schema validation at the Plugin execution boundary."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from jsonschema import exceptions, validators


class PluginSchemaError(ValueError):
    """Raised when a Plugin declares an invalid input schema."""


class PluginInputValidationError(ValueError):
    """Raised when call arguments do not satisfy the current Plugin schema."""


@dataclass(frozen=True, slots=True)
class PluginArgumentRepair:
    """One deterministic, lossless repair applied before strict validation."""

    path: str
    kind: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"path": self.path, "kind": self.kind}
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass(frozen=True, slots=True)
class PluginArgumentNormalization:
    """Canonical Plugin arguments plus an auditable repair trail."""

    arguments: dict[str, Any]
    repairs: tuple[PluginArgumentRepair, ...] = ()


def check_input_schema(schema: Mapping[str, Any]) -> None:
    """Reject malformed schemas when a Plugin is created."""

    normalized = dict(schema)
    validator = validators.validator_for(normalized)
    try:
        validator.check_schema(normalized)
    except exceptions.SchemaError as exc:
        raise PluginSchemaError(f"invalid Plugin input_schema: {exc.message}") from exc


def _schema_types(schema: Mapping[str, Any]) -> frozenset[str]:
    expected = schema.get("type")
    if isinstance(expected, str):
        return frozenset((expected,))
    if isinstance(expected, list):
        return frozenset(str(item) for item in expected if isinstance(item, str))
    if isinstance(schema.get("properties"), Mapping):
        return frozenset(("object",))
    if "items" in schema:
        return frozenset(("array",))
    return frozenset()


def _schema_accepts(value: Any, schema: Mapping[str, Any]) -> bool:
    normalized_schema = dict(schema)
    try:
        validator_type = validators.validator_for(normalized_schema)
        validator_type.check_schema(normalized_schema)
        return validator_type(normalized_schema).is_valid(value)
    except exceptions.SchemaError:
        return False


def _normalize_object_fields(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    path: str,
    repairs: list[PluginArgumentRepair],
    *,
    allow_wrappers: bool,
) -> dict[str, Any]:
    properties = schema.get("properties")
    property_schemas = properties if isinstance(properties, Mapping) else {}
    canonical_properties: dict[str, list[str]] = {}
    for property_name in property_schemas:
        canonical = re.sub(r"[^a-z0-9]+", "", str(property_name).casefold())
        if canonical:
            canonical_properties.setdefault(canonical, []).append(
                str(property_name)
            )
    result: dict[str, Any] = {}
    for key, item in value.items():
        source_key = str(key)
        target_key = source_key
        child_schema = property_schemas.get(source_key)
        if not isinstance(child_schema, Mapping):
            canonical = re.sub(r"[^a-z0-9]+", "", source_key.casefold())
            matches = canonical_properties.get(canonical, ())
            if (
                len(matches) == 1
                and matches[0] not in value
                and matches[0] not in result
            ):
                target_key = matches[0]
                child_schema = property_schemas.get(target_key)
                repairs.append(
                    PluginArgumentRepair(
                        f"{path}.{source_key}",
                        "normalize_property_name",
                        detail=f"{source_key}->{target_key}",
                    )
                )
        if isinstance(child_schema, Mapping):
            result[target_key] = _normalize_value(
                item,
                child_schema,
                f"{path}.{target_key}",
                repairs,
                allow_wrappers=allow_wrappers,
            )
        else:
            result[source_key] = deepcopy(item)
    return result


def _nested_argument_objects(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current: Any = value
    for _ in range(8):
        if not isinstance(current, Mapping):
            break
        candidates.append(dict(current))
        nested = current.get("arguments")
        if not isinstance(nested, Mapping):
            break
        current = nested
    return candidates


def _flatten_call_envelope(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(value.get("operation") or "") != "call":
        return None
    capability = value.get("capability")
    parameters = value.get("parameters")
    if not isinstance(capability, str) or not capability.strip():
        return None
    if not isinstance(parameters, Mapping):
        return None
    properties = schema.get("properties")
    operation_schema = (
        properties.get("operation")
        if isinstance(properties, Mapping)
        else None
    )
    operation_enum = (
        operation_schema.get("enum")
        if isinstance(operation_schema, Mapping)
        else None
    )
    if not isinstance(operation_enum, list) or capability.strip() not in operation_enum:
        return None
    flattened = {
        str(key): deepcopy(item)
        for key, item in value.items()
        if key not in {"operation", "capability", "parameters"}
    }
    flattened.update({str(key): deepcopy(item) for key, item in parameters.items()})
    flattened["operation"] = capability.strip()
    return flattened


def _project_unique_schema_fields(
    candidates: list[dict[str, Any]],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for field in properties:
        values = [candidate[field] for candidate in candidates if field in candidate]
        if values and all(value == values[0] for value in values[1:]):
            projected[str(field)] = deepcopy(values[0])
    return projected


def _normalize_value(
    value: Any,
    schema: Mapping[str, Any],
    path: str,
    repairs: list[PluginArgumentRepair],
    *,
    allow_wrappers: bool = True,
) -> Any:
    # A value that already satisfies its declared schema is canonical. This is
    # especially important for union schemas where, for example, both a string
    # and an integer may be intentional representations.
    if _schema_accepts(value, schema):
        return deepcopy(value)

    expected = _schema_types(schema)

    if "array" in expected:
        if (
            isinstance(value, Mapping)
            and set(value) == {"item"}
            and isinstance(value.get("item"), list)
        ):
            repairs.append(PluginArgumentRepair(path, "unwrap_item_array"))
            value = value["item"]
        if isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                return [
                    _normalize_value(
                        item,
                        item_schema,
                        f"{path}[{index}]",
                        repairs,
                        allow_wrappers=allow_wrappers,
                    )
                    for index, item in enumerate(value)
                ]
            return deepcopy(value)

    if (
        "integer" in expected
        and isinstance(value, str)
        and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value)
    ):
        repairs.append(PluginArgumentRepair(path, "coerce_integer_string"))
        return int(value)

    if "object" not in expected or not isinstance(value, Mapping):
        return deepcopy(value)

    direct_repairs: list[PluginArgumentRepair] = []
    direct = _normalize_object_fields(
        value,
        schema,
        path,
        direct_repairs,
        allow_wrappers=allow_wrappers,
    )
    if _schema_accepts(direct, schema) or not allow_wrappers:
        repairs.extend(direct_repairs)
        return direct

    candidates = _nested_argument_objects(value)
    if len(candidates) > 1:
        for depth, candidate in reversed(list(enumerate(candidates[1:], start=1))):
            candidate_repairs: list[PluginArgumentRepair] = []
            normalized_candidate = _normalize_object_fields(
                candidate,
                schema,
                path,
                candidate_repairs,
                allow_wrappers=False,
            )
            if _schema_accepts(normalized_candidate, schema):
                repairs.append(
                    PluginArgumentRepair(
                        path,
                        "unwrap_arguments",
                        detail=f"depth={depth}",
                    )
                )
                repairs.extend(candidate_repairs)
                return normalized_candidate

        for candidate in candidates:
            flattened = _flatten_call_envelope(candidate, schema)
            if flattened is None:
                continue
            flattened_repairs: list[PluginArgumentRepair] = []
            normalized_flattened = _normalize_object_fields(
                flattened,
                schema,
                path,
                flattened_repairs,
                allow_wrappers=False,
            )
            if _schema_accepts(normalized_flattened, schema):
                repairs.append(PluginArgumentRepair(path, "flatten_call_envelope"))
                repairs.extend(flattened_repairs)
                return normalized_flattened

        projected = _project_unique_schema_fields(candidates, schema)
        if projected:
            projected_repairs: list[PluginArgumentRepair] = []
            normalized_projected = _normalize_object_fields(
                projected,
                schema,
                path,
                projected_repairs,
                allow_wrappers=False,
            )
            if _schema_accepts(normalized_projected, schema):
                repairs.append(
                    PluginArgumentRepair(
                        path,
                        "project_schema_fields",
                        detail=",".join(sorted(projected)),
                    )
                )
                repairs.extend(projected_repairs)
                return normalized_projected

    flattened = _flatten_call_envelope(value, schema)
    if flattened is not None:
        flattened_repairs: list[PluginArgumentRepair] = []
        normalized_flattened = _normalize_object_fields(
            flattened,
            schema,
            path,
            flattened_repairs,
            allow_wrappers=False,
        )
        if _schema_accepts(normalized_flattened, schema):
            repairs.append(PluginArgumentRepair(path, "flatten_call_envelope"))
            repairs.extend(flattened_repairs)
            return normalized_flattened

    repairs.extend(direct_repairs)
    return direct


def normalize_plugin_arguments(
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> PluginArgumentNormalization:
    """Repair only unambiguous model argument shapes using the resolved schema.

    The returned arguments are not implicitly trusted: callers must still run
    :func:`validate_plugin_arguments` before permission review or execution.
    """

    check_input_schema(schema)
    repairs: list[PluginArgumentRepair] = []
    normalized = _normalize_value(
        dict(arguments),
        dict(schema),
        "arguments",
        repairs,
    )
    if not isinstance(normalized, Mapping):
        normalized = dict(arguments)
        repairs = []
    return PluginArgumentNormalization(dict(normalized), tuple(repairs))


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
    "PluginArgumentNormalization",
    "PluginArgumentRepair",
    "PluginInputValidationError",
    "PluginSchemaError",
    "check_input_schema",
    "normalize_plugin_arguments",
    "validate_plugin_arguments",
]
