"""Validate and route stable wire-tool calls to concrete Cyrene handlers."""

from __future__ import annotations

import hashlib
import importlib
import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from cyrene.tooling.catalog import (
    describe_capabilities,
    discover_capabilities,
    get_capability,
    get_capability_by_concrete_name,
    get_tool_execution_metadata,
    get_effective_function_definitions,
    module_wire_names,
)
from cyrene.runtime.settings_store import is_tool_pack_enabled
from cyrene.tooling.results import (
    ToolProtocolError,
    serialize_error,
)
from cyrene.tooling.packs import PACK_BY_WIRE_NAME
from cyrene.tooling.policy.engine import capability_available
from cyrene.tooling.types import ToolExecutionContext
from cyrene.tooling.types import ToolCatalogSnapshot, ToolSpec
from cyrene.tooling.validation import ensure_object, validate_schema
from cyrene.tooling.wire import DIRECT_TOOL_NAMES, SUBAGENT_DIRECT_TOOL_NAMES

WireToolError = ToolProtocolError
_active_catalog_snapshot: ContextVar[ToolCatalogSnapshot | None] = ContextVar(
    "_active_catalog_snapshot",
    default=None,
)


@dataclass(frozen=True)
class WireCallResolution:
    wire_name: str
    operation: str
    capability_id: str
    concrete_name: str
    concrete_arguments: dict[str, Any]
    concrete_compat: bool = False

    @property
    def spawned_subagent(self) -> bool:
        return self.capability_id == "subagent.spawn"

    @property
    def coordination_call(self) -> bool:
        return self.capability_id in {
            "subagent.send_message",
            "subagent.broadcast",
        }


def activate_catalog_snapshot(actor: str) -> Token[ToolCatalogSnapshot | None]:
    """Freeze deferred capabilities/settings for one agent run."""
    from cyrene.tooling.snapshot import build_catalog_snapshot

    return _active_catalog_snapshot.set(build_catalog_snapshot(actor))


def reset_catalog_snapshot(token: Token[ToolCatalogSnapshot | None]) -> None:
    _active_catalog_snapshot.reset(token)


def _effective_snapshot(
    actor: str,
    snapshot: ToolCatalogSnapshot | None = None,
) -> ToolCatalogSnapshot | None:
    selected = snapshot or _active_catalog_snapshot.get()
    if selected is None or selected.actor != actor:
        return None
    return selected


def _snapshot_spec(
    capability_id: str,
    *,
    actor: str,
    snapshot: ToolCatalogSnapshot | None,
    include_disabled: bool = False,
) -> ToolSpec | None:
    selected = _effective_snapshot(actor, snapshot)
    if selected is None:
        return None
    spec = selected.capabilities.get(str(capability_id or "").strip())
    if spec is None:
        return None
    if not include_disabled and spec.capability_id not in selected.enabled_capability_ids:
        return None
    return spec


def _snapshot_specs_for_wire(
    wire_name: str,
    *,
    actor: str,
    snapshot: ToolCatalogSnapshot | None,
    include_disabled: bool = False,
) -> list[ToolSpec] | None:
    selected = _effective_snapshot(actor, snapshot)
    if selected is None:
        return None
    pack = PACK_BY_WIRE_NAME.get(wire_name)
    if pack is None:
        return []
    return [
        spec
        for spec in selected.capabilities.values()
        if spec.pack_id == pack.pack_id
        and (
            include_disabled
            or spec.capability_id in selected.enabled_capability_ids
        )
    ]


def _wire_name_for_pack_id(pack_id: str) -> str:
    return next(
        (
            wire_name
            for wire_name, pack in PACK_BY_WIRE_NAME.items()
            if pack.pack_id == str(pack_id or "").strip()
        ),
        "",
    )


def _gateway_resolution(
    wire_name: str,
    arguments: dict[str, Any],
    *,
    actor: str,
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> WireCallResolution:
    operation = str(arguments.get("operation") or "").strip()
    if operation not in {"discover", "describe", "invoke"}:
        raise WireToolError(
            "invalid_arguments",
            "`operation` must be discover, describe, or invoke.",
        )
    allowed_fields = {
        "discover": {"operation", "query", "limit"},
        "describe": {"operation", "capability_id", "capability_ids"},
        "invoke": {"operation", "capability_id", "arguments"},
    }[operation]
    unknown_fields = sorted(set(arguments) - allowed_fields)
    if unknown_fields:
        raise WireToolError(
            "invalid_arguments",
            f"Unknown field(s) for operation={operation}: "
            + ", ".join(unknown_fields)
            + ".",
        )
    if operation == "discover":
        if "query" in arguments and not isinstance(arguments["query"], str):
            raise WireToolError("invalid_arguments", "`query` must be a string.")
        if "limit" in arguments:
            limit = arguments["limit"]
            if (
                not isinstance(limit, int)
                or isinstance(limit, bool)
                or not 1 <= limit <= 50
            ):
                raise WireToolError(
                    "invalid_arguments",
                    "`limit` must be an integer from 1 to 50.",
                )
    if operation == "describe":
        if "capability_id" in arguments and not isinstance(
            arguments["capability_id"],
            str,
        ):
            raise WireToolError(
                "invalid_arguments",
                "`capability_id` must be a string.",
            )
        if "capability_ids" in arguments and (
            not isinstance(arguments["capability_ids"], list)
            or not all(
                isinstance(item, str)
                for item in arguments["capability_ids"]
            )
        ):
            raise WireToolError(
                "invalid_arguments",
                "`capability_ids` must be an array of strings.",
            )
    if operation != "invoke":
        return WireCallResolution(
            wire_name=wire_name,
            operation=operation,
            capability_id=f"{wire_name}.{operation}",
            concrete_name="",
            concrete_arguments={},
        )
    capability_id = str(arguments.get("capability_id") or "").strip()
    if not capability_id:
        raise WireToolError(
            "invalid_arguments",
            "`capability_id` is required for operation=invoke.",
        )
    selected = _effective_snapshot(actor, catalog_snapshot)
    capability = (
        _snapshot_spec(
            capability_id,
            actor=actor,
            snapshot=selected,
            include_disabled=True,
        )
        if selected is not None
        else get_capability(
            capability_id,
            actor=actor,
            include_disabled=True,
        )
    )
    capability_pack_id = (
        capability.pack_id
        if capability is not None
        else ""
    )
    expected_pack = PACK_BY_WIRE_NAME[wire_name]
    if capability is None or capability_pack_id != expected_pack.pack_id:
        raise WireToolError(
            "unknown_capability",
            f"Capability `{capability_id}` is not available through `{wire_name}`.",
        )
    enabled = (
        capability_id in selected.enabled_capability_ids
        if selected is not None
        else (
            is_tool_pack_enabled(wire_name)
        )
    )
    if not enabled:
        raise WireToolError(
            "permission_denied",
            f"Capability `{capability_id}` is disabled in settings.",
        )
    concrete_arguments = ensure_object(arguments.get("arguments"), "arguments")
    validate_schema(concrete_arguments, capability.input_schema)
    return WireCallResolution(
        wire_name=wire_name,
        operation=operation,
        capability_id=capability.capability_id,
        concrete_name=capability.concrete_name,
        concrete_arguments=concrete_arguments,
    )


def resolve_wire_call(
    wire_name: str,
    arguments: dict[str, Any] | None,
    *,
    actor: str = "main",
    allow_concrete_compat: bool = True,
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> WireCallResolution:
    """Resolve a wire call without executing it.

    Capability IDs are accepted as hidden aliases for accidental direct calls.
    Hidden concrete names also remain accepted for persisted conversations and
    learned-skill replays. Neither kind of compatibility alias is included in
    the model-facing wire definitions.
    """
    name = str(wire_name or "").strip()
    args = ensure_object(arguments, "arguments")
    if name in module_wire_names():
        return _gateway_resolution(
            name,
            args,
            actor=actor,
            catalog_snapshot=catalog_snapshot,
        )

    direct_names = (
        set(SUBAGENT_DIRECT_TOOL_NAMES)
        if actor == "subagent"
        else set(DIRECT_TOOL_NAMES)
    )
    if name in direct_names:
        concrete_name = name
        if name != "use_tools":
            selected = _effective_snapshot(actor, catalog_snapshot)
            spec = (
                _snapshot_spec(
                    name,
                    actor=actor,
                    snapshot=selected,
                    include_disabled=True,
                )
                if selected is not None
                else None
            )
            enabled = (
                name in selected.enabled_capability_ids
                if selected is not None
                else True
            )
            if not enabled:
                raise WireToolError(
                    "permission_denied",
                    f"Tool `{name}` is disabled in settings.",
                )
            if spec is not None:
                validate_schema(args, spec.input_schema)
                concrete_name = spec.concrete_name
            else:
                definition = get_effective_function_definitions().get(name)
                if definition is not None:
                    validate_schema(
                        args,
                        dict(
                            (definition.get("function") or {}).get("parameters")
                            or {"type": "object"}
                        ),
                    )
        return WireCallResolution(
            wire_name=name,
            operation="invoke",
            capability_id=name,
            concrete_name=concrete_name,
            concrete_arguments=args,
        )

    # Capability IDs are intentionally omitted from the model-facing function
    # definitions: the advertised contract is discover -> describe -> invoke
    # through the owning module gateway.  Models can nevertheless mistake an
    # ID returned by discover (for example ``memory.recall``) for a callable
    # function name.  Accept every registered capability ID as a hidden
    # compatibility alias so that such a call has the same validation,
    # settings, policy, and execution semantics as operation=invoke.
    selected = _effective_snapshot(actor, catalog_snapshot)
    capability_alias = (
        _snapshot_spec(
            name,
            actor=actor,
            snapshot=selected,
            include_disabled=True,
        )
        if selected is not None
        else get_capability(
            name,
            actor=actor,
            include_disabled=True,
        )
    )
    if capability_alias is not None:
        alias_wire_name = (
            _wire_name_for_pack_id(capability_alias.pack_id)
            if selected is not None
            else capability_alias.wire_name
        )
        if not alias_wire_name:
            raise WireToolError(
                "unknown_capability",
                f"Capability `{capability_alias.capability_id}` has no owning module.",
            )
        enabled = (
            capability_alias.capability_id in selected.enabled_capability_ids
            if selected is not None
            else is_tool_pack_enabled(alias_wire_name)
        )
        if not enabled:
            raise WireToolError(
                "permission_denied",
                f"Capability `{capability_alias.capability_id}` is disabled in settings.",
            )
        validate_schema(args, capability_alias.input_schema)
        return WireCallResolution(
            wire_name=alias_wire_name,
            operation="invoke",
            capability_id=capability_alias.capability_id,
            concrete_name=capability_alias.concrete_name,
            concrete_arguments=args,
            concrete_compat=True,
        )

    if allow_concrete_compat:
        capability = (
            next(
                (
                    spec
                    for spec in selected.capabilities.values()
                    if spec.concrete_name == name
                ),
                None,
            )
            if selected is not None
            else get_capability_by_concrete_name(
                name,
                actor=actor,
                include_disabled=True,
            )
        )
        if capability is not None:
            enabled = (
                capability.capability_id in selected.enabled_capability_ids
                if selected is not None
                else (
                    is_tool_pack_enabled(capability.wire_name)
                )
            )
            if not enabled:
                raise WireToolError(
                    "permission_denied",
                    f"Capability `{capability.capability_id}` is disabled in settings.",
                )
            validate_schema(args, capability.input_schema)
            return WireCallResolution(
                wire_name=name,
                operation="invoke",
                capability_id=capability.capability_id,
                concrete_name=capability.concrete_name,
                concrete_arguments=args,
                concrete_compat=True,
            )

    raise WireToolError("unknown_tool", f"Unknown wire tool `{name}`.")


def _describe_ids(arguments: dict[str, Any]) -> list[str]:
    ids = arguments.get("capability_ids")
    if isinstance(ids, list):
        return [str(item).strip() for item in ids if str(item).strip()]
    single = str(arguments.get("capability_id") or "").strip()
    return [single] if single else []


def _nested_argument_objects(value: Any) -> list[dict[str, Any]]:
    """Return the gateway payload and any nested ``arguments`` wrappers."""
    objects: list[dict[str, Any]] = []
    current = value
    for _ in range(8):
        if not isinstance(current, dict):
            break
        objects.append(dict(current))
        nested = current.get("arguments")
        if not isinstance(nested, dict):
            break
        current = nested
    return objects


def _schema_accepts(value: Any, schema: dict[str, Any]) -> bool:
    try:
        validate_schema(value, schema)
    except WireToolError:
        return False
    return True


def _unique_nested_value(
    objects: list[dict[str, Any]],
    field: str,
) -> tuple[bool, Any]:
    values = [item[field] for item in objects if field in item]
    if not values:
        return False, None
    first = values[0]
    if any(value != first for value in values[1:]):
        return False, None
    return True, first


def _flatten_call_envelope(
    candidate: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Unwrap a visual-style call envelope into flat schema-shaped arguments.

    Models sometimes route a flat capability through the visual scheme's
    ``operation=call`` + ``capability`` + ``parameters`` envelope even when the
    target capability expects a flat ``operation`` and inline fields. Only
    flatten when the envelope's capability is a real operation of this schema
    and the reconstructed payload still passes strict schema validation.
    """
    if str(candidate.get("operation") or "") != "call":
        return None
    capability = candidate.get("capability")
    parameters = candidate.get("parameters")
    if not isinstance(capability, str) or not capability.strip():
        return None
    if not isinstance(parameters, dict):
        return None
    operation_enum = ((schema.get("properties") or {}).get("operation") or {}).get("enum") or []
    if capability.strip() not in operation_enum:
        return None
    flattened = {
        key: value
        for key, value in candidate.items()
        if key not in {"operation", "capability", "parameters"}
    }
    flattened.update(parameters)
    # The envelope's declared capability wins over any echoed key inside
    # parameters (e.g. a leaked "operation"), which would otherwise execute a
    # different operation than the one the model declared.
    flattened["operation"] = capability.strip()
    return flattened


def _repair_concrete_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Select or reconstruct an unambiguous payload that satisfies ``schema``.

    Local models frequently add one or more gateway-shaped ``arguments``
    wrappers. Prefer an existing nested object that validates as-is. If no
    level validates, project uniquely present schema fields from those wrapper
    levels and accept the result only when strict schema validation succeeds.
    """
    candidates = _nested_argument_objects(arguments)
    for candidate in reversed(candidates):
        if _schema_accepts(candidate, schema):
            return candidate
        flattened = _flatten_call_envelope(candidate, schema)
        if flattened is not None and _schema_accepts(flattened, schema):
            return flattened

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return arguments
    projected: dict[str, Any] = {}
    for field in properties:
        found, value = _unique_nested_value(candidates, str(field))
        if found:
            projected[str(field)] = value
    if _schema_accepts(projected, schema):
        return projected
    return arguments


def _capability_for_normalization(
    capability_id: str,
    *,
    actor: str,
    catalog_snapshot: ToolCatalogSnapshot | None,
) -> ToolSpec | None:
    selected = _effective_snapshot(actor, catalog_snapshot)
    if selected is not None:
        return _snapshot_spec(
            capability_id,
            actor=actor,
            snapshot=selected,
            include_disabled=True,
        )
    return get_capability(
        capability_id,
        actor=actor,
        include_disabled=True,
    )


def _normalize_module_arguments(
    wire_name: str,
    arguments: dict[str, Any] | None,
    *,
    actor: str = "main",
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> dict[str, Any]:
    """Repair common, unambiguous gateway nesting mistakes.

    This runs only at execution time. The stable model-facing tool definitions
    remain unchanged, preserving prompt/tool-schema cache prefixes.
    """
    normalized = dict(arguments or {})
    if wire_name not in module_wire_names():
        return normalized

    # Lift misplaced gateway-envelope fields through multiple pure
    # ``arguments`` wrappers. This is deliberately bounded and never guesses
    # between conflicting duplicate values.
    for _ in range(8):
        nested = normalized.get("arguments")
        if not isinstance(nested, dict):
            break
        nested_copy = dict(nested)
        changed = False

        operation = str(normalized.get("operation") or "").strip()
        if not operation:
            nested_operation = nested_copy.get("operation")
            if (
                isinstance(nested_operation, str)
                and nested_operation.strip() in {"discover", "describe", "invoke"}
            ):
                normalized["operation"] = nested_operation.strip()
                nested_copy.pop("operation", None)
                operation = nested_operation.strip()
                changed = True

        lift_fields = {
            "discover": ("query", "limit"),
            "describe": ("capability_id", "capability_ids"),
            "invoke": ("capability_id",),
        }.get(operation, ())
        for field in lift_fields:
            if field in normalized or field not in nested_copy:
                continue
            normalized[field] = nested_copy.pop(field)
            changed = True

        if not changed:
            break
        if nested_copy:
            normalized["arguments"] = nested_copy
        else:
            normalized.pop("arguments", None)

    if not str(normalized.get("operation") or "").strip():
        nested = normalized.get("arguments")
        nested_copy = dict(nested) if isinstance(nested, dict) else {}
        capability_id = str(normalized.get("capability_id") or "").strip()
        if not capability_id and isinstance(nested_copy.get("capability_id"), str):
            capability_id = nested_copy.pop("capability_id").strip()
            normalized["capability_id"] = capability_id
            if nested_copy:
                normalized["arguments"] = nested_copy
            else:
                normalized.pop("arguments", None)
        if capability_id:
            normalized["operation"] = "invoke"

    if str(normalized.get("operation") or "").strip() == "invoke":
        capability_id = str(normalized.get("capability_id") or "").strip()
        concrete_arguments = normalized.get("arguments")
        capability = _capability_for_normalization(
            capability_id,
            actor=actor,
            catalog_snapshot=catalog_snapshot,
        )
        if capability is not None and isinstance(concrete_arguments, dict):
            normalized["arguments"] = _repair_concrete_arguments(
                concrete_arguments,
                capability.input_schema,
            )
    return normalized


def _schema_placeholder(schema: dict[str, Any], field: str) -> Any:
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    expected = schema.get("type")
    if expected == "string":
        return f"<{field}>"
    if expected == "integer":
        return int(schema.get("minimum") or 0)
    if expected == "number":
        return schema.get("minimum") or 0
    if expected == "boolean":
        return False
    if expected == "array":
        return []
    if expected == "object":
        properties = schema.get("properties") or {}
        return {
            name: _schema_placeholder(properties.get(name) or {}, str(name))
            for name in schema.get("required") or ()
        }
    return f"<{field}>"


def _schema_argument_example(
    schema: dict[str, Any],
    source: Any,
) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    candidates = _nested_argument_objects(source)
    example: dict[str, Any] = {}
    required = set(schema.get("required") or ())
    for field, field_schema in properties.items():
        found, value = _unique_nested_value(candidates, str(field))
        valid_source_value = (
            found
            and isinstance(field_schema, dict)
            and _schema_accepts(value, field_schema)
        )
        if valid_source_value:
            example[str(field)] = value
        elif field in required:
            example[str(field)] = _schema_placeholder(
                field_schema if isinstance(field_schema, dict) else {},
                str(field),
            )
    return example


def _invalid_argument_example(
    wire_name: str,
    arguments: dict[str, Any] | None,
    *,
    actor: str = "main",
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> dict[str, Any] | None:
    """Return a concrete gateway-call shape for an invalid argument response."""
    if wire_name not in module_wire_names():
        return None
    args = dict(arguments or {})
    operation = str(args.get("operation") or "").strip()
    if operation == "discover":
        return {
            "tool": wire_name,
            "arguments": {
                "operation": "discover",
                "query": str(args.get("query") or "<search terms>"),
            },
        }
    if operation == "describe":
        capability_ids = _describe_ids(args)
        return {
            "tool": wire_name,
            "arguments": {
                "operation": "describe",
                "capability_ids": capability_ids or ["<capability_id>"],
            },
        }
    if operation == "invoke":
        nested = args.get("arguments")
        nested_objects = _nested_argument_objects(nested)
        nested_capability_id = next(
            (
                item.get("capability_id")
                for item in nested_objects
                if isinstance(item.get("capability_id"), str)
                and str(item.get("capability_id") or "").strip()
            ),
            "",
        )
        capability_id = str(
            args.get("capability_id") or nested_capability_id or "<capability_id>"
        ).strip()
        capability = _capability_for_normalization(
            capability_id,
            actor=actor,
            catalog_snapshot=catalog_snapshot,
        )
        concrete_arguments = (
            _schema_argument_example(capability.input_schema, nested)
            if capability is not None
            else {}
        )
        return {
            "tool": wire_name,
            "arguments": {
                "operation": "invoke",
                "capability_id": capability_id,
                "arguments": concrete_arguments,
            },
        }
    return {
        "tool": wire_name,
        "arguments": {
            "operation": "discover",
            "query": "<search terms>",
        },
    }


def _serialize_wire_error(
    error: WireToolError,
    wire_name: str,
    arguments: dict[str, Any] | None,
    *,
    actor: str = "main",
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": "error",
        "error": {
            "type": error.code,
            "message": error.message,
        },
    }
    if error.code == "invalid_arguments":
        example = _invalid_argument_example(
            wire_name,
            arguments,
            actor=actor,
            catalog_snapshot=catalog_snapshot,
        )
        if example is not None:
            payload["error"]["expected_call"] = example
    return json.dumps(payload, ensure_ascii=False)


def _runtime_capability_available(
    capability_id: str,
    *,
    bot: Any,
) -> bool:
    if capability_id == "delivery.send_wechat_file":
        return bool(bot is not None and hasattr(bot, "send_file"))
    return True


async def execute_wire_tool(
    wire_name: str,
    arguments: dict[str, Any] | None,
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
    *,
    actor: str = "main",
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> str:
    args = _normalize_module_arguments(
        wire_name,
        arguments,
        actor=actor,
        catalog_snapshot=catalog_snapshot,
    )
    try:
        selected_snapshot = _effective_snapshot(actor, catalog_snapshot)
        resolution = resolve_wire_call(
            wire_name,
            args,
            actor=actor,
            catalog_snapshot=selected_snapshot,
        )
        if resolution.operation == "discover":
            snapshot_specs = _snapshot_specs_for_wire(
                resolution.wire_name,
                actor=actor,
                snapshot=selected_snapshot,
            )
            if snapshot_specs is None:
                result = discover_capabilities(
                    resolution.wire_name,
                    actor=actor,
                    query=str(args.get("query") or ""),
                    limit=int(args.get("limit") or 20),
                )
            else:
                from cyrene.tooling.catalog import search_capability_items

                matched = search_capability_items(
                    snapshot_specs,
                    query=str(args.get("query") or ""),
                    limit=int(args.get("limit") or 20),
                )
                result = [
                    {"id": spec.capability_id, "description": spec.description}
                    for spec in matched
                ]
            result = [
                item
                for item in result
                if _runtime_capability_available(
                    str(item.get("id") or ""),
                    bot=bot,
                )
            ]
            return json.dumps(
                {
                    "status": "success",
                    "module": resolution.wire_name,
                    "capabilities": result,
                    "important": (
                        "Capability IDs are identifiers, not model-visible function "
                        f"names. The only advertised callable function for this module is "
                        f"`{resolution.wire_name}`. Never emit a function call named "
                        "after a capability ID."
                    ),
                    "next": (
                        f"Call `{resolution.wire_name}` with operation=describe and "
                        "the selected capability_ids. Then call the same "
                        f"`{resolution.wire_name}` function with operation=invoke, "
                        "one capability_id, and arguments matching the returned "
                        "input_schema."
                    ),
                    "example_describe": {
                        "tool": resolution.wire_name,
                        "arguments": {
                            "operation": "describe",
                            "capability_ids": (
                                [str(result[0].get("id") or "")]
                                if result
                                else ["<capability_id>"]
                            ),
                        },
                    },
                },
                ensure_ascii=False,
            )
        if resolution.operation == "describe":
            capability_ids = _describe_ids(args)
            if not capability_ids:
                raise WireToolError(
                    "invalid_arguments",
                    "`capability_id` or `capability_ids` is required for operation=describe.",
                )
            snapshot_specs = _snapshot_specs_for_wire(
                resolution.wire_name,
                actor=actor,
                snapshot=selected_snapshot,
            )
            if snapshot_specs is None:
                details = describe_capabilities(
                    resolution.wire_name,
                    capability_ids,
                    actor=actor,
                )
            else:
                available = {
                    spec.capability_id: spec
                    for spec in snapshot_specs
                }
                details = [
                    {
                        "id": available[capability_id].capability_id,
                        "description": available[capability_id].description,
                        "input_schema": available[capability_id].input_schema,
                        "source": available[capability_id].source,
                    }
                    for capability_id in capability_ids[:20]
                    if capability_id in available
                ]
            details = [
                detail
                for detail in details
                if _runtime_capability_available(
                    str(detail.get("id") or ""),
                    bot=bot,
                )
            ]
            missing = [item for item in capability_ids if item not in {detail["id"] for detail in details}]
            if missing:
                raise WireToolError(
                    "unknown_capability",
                    f"Unavailable capability ID(s): {', '.join(missing)}.",
                )
            return json.dumps(
                {
                    "status": "success",
                    "module": resolution.wire_name,
                    "capabilities": details,
                },
                ensure_ascii=False,
            )
        if resolution.concrete_name == "use_tools":
            return "Already in the execution phase; choose a direct tool or tool module."
        enabled = (
            resolution.capability_id in selected_snapshot.enabled_capability_ids
            if selected_snapshot is not None
            and resolution.concrete_name != "use_tools"
            else None
        )
        available, unavailable_reason = capability_available(
            resolution.concrete_name,
            capability_id=resolution.capability_id,
            actor=actor,
            bot=bot,
            enabled=enabled,
        )
        if not available:
            raise WireToolError(
                "permission_denied",
                unavailable_reason,
            )
        execute_tool = importlib.import_module(
            "cyrene.tooling.executor"
        )._execute_tool
        result = await execute_tool(
            resolution.concrete_name,
            resolution.concrete_arguments,
            bot,
            chat_id,
            db_path,
            notify_state,
        )
        status = (
            "error"
            if str(result).casefold().startswith(("tool failed:", "tool unavailable:"))
            else "success"
        )
        if resolution.wire_name in module_wire_names() or resolution.concrete_compat:
            wire_result: Any = result
            if isinstance(result, str):
                try:
                    wire_result = json.loads(result)
                except (TypeError, ValueError, json.JSONDecodeError):
                    wire_result = result
            if isinstance(wire_result, dict) and str(wire_result.get("status") or ""):
                status = str(wire_result["status"])
            return json.dumps(
                {
                    "status": status,
                    "capability_id": resolution.capability_id,
                    "result": wire_result,
                },
                ensure_ascii=False,
            )
        return str(result)
    except WireToolError as error:
        return _serialize_wire_error(
            error,
            wire_name,
            args,
            actor=actor,
            catalog_snapshot=catalog_snapshot,
        )


async def execute_wire_tool_in_context(
    wire_name: str,
    arguments: dict[str, Any] | None,
    context: ToolExecutionContext,
) -> str:
    return await execute_wire_tool(
        wire_name,
        arguments,
        context.bot,
        context.chat_id,
        context.db_path,
        context.notify_state,
        actor=context.actor,
        catalog_snapshot=context.catalog_snapshot,
    )


async def execute_capability(
    capability_id: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    capability = get_capability(
        capability_id, actor=context.actor, include_disabled=True
    )
    wire_name = capability.wire_name if capability is not None else ""
    if context.catalog_snapshot is not None:
        capability = _snapshot_spec(
            capability_id,
            actor=context.actor,
            snapshot=context.catalog_snapshot,
            include_disabled=True,
        )
        wire_name = next(
            (
                candidate_wire_name
                for candidate_wire_name, pack in PACK_BY_WIRE_NAME.items()
                if capability is not None
                and pack.pack_id == capability.pack_id
            ),
            "",
        )
    if capability is None or not wire_name:
        return serialize_error(ToolProtocolError(
            "unknown_capability",
            f"Unknown capability `{capability_id}`.",
        ))
    return await execute_wire_tool(
        wire_name,
        {
            "operation": "invoke",
            "capability_id": capability_id,
            "arguments": arguments,
        },
        context.bot,
        context.chat_id,
        context.db_path,
        context.notify_state,
        actor=context.actor,
        catalog_snapshot=context.catalog_snapshot,
    )


def get_wire_tool_execution_metadata(
    wire_name: str,
    arguments: dict[str, Any] | None,
    *,
    actor: str = "main",
    catalog_snapshot: ToolCatalogSnapshot | None = None,
) -> dict[str, Any]:
    """Resolve scheduler metadata from the concrete capability when possible."""
    args = _normalize_module_arguments(
        wire_name,
        arguments,
        actor=actor,
        catalog_snapshot=catalog_snapshot,
    )
    try:
        resolution = resolve_wire_call(
            wire_name,
            args,
            actor=actor,
            catalog_snapshot=_effective_snapshot(actor, catalog_snapshot),
        )
    except WireToolError:
        return {
            "read_only": False,
            "resource_keys": (f"tool:{wire_name}",),
            "requires_order": True,
        }
    if resolution.operation in {"discover", "describe"}:
        return {
            "read_only": True,
            "resource_keys": (f"tool-catalog:{wire_name}",),
            "requires_order": False,
        }
    if resolution.capability_id == "subagent.spawn":
        agent_id = str(resolution.concrete_arguments.get("agent_id") or "")
        task = str(resolution.concrete_arguments.get("task") or "")
        identity = agent_id or task
        task_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return {
            "read_only": False,
            "resource_keys": (f"subagent:spawn:{task_key}",),
            "requires_order": False,
        }
    return get_tool_execution_metadata(
        resolution.concrete_name,
        resolution.concrete_arguments,
    )
