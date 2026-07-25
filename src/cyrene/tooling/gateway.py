"""Validate and route stable wire-tool calls to concrete Cyrene handlers."""

from __future__ import annotations

import hashlib
import importlib
import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from cyrene.tooling.catalog import (
    TOOL_DEFS,
    describe_capabilities,
    discover_capabilities,
    get_capability,
    get_capability_by_concrete_name,
    get_tool_execution_metadata,
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

    Hidden concrete names remain accepted for persisted conversations and
    learned-skill replays. They are never included in the model-facing wire
    definitions.
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
            else:
                definition = next(
                    (
                        tool_def
                        for tool_def in TOOL_DEFS
                        if str(
                            (tool_def.get("function") or {}).get("name") or ""
                        ) == name
                    ),
                    None,
                )
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
            concrete_name=name,
            concrete_arguments=args,
        )

    if allow_concrete_compat:
        selected = _effective_snapshot(actor, catalog_snapshot)
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
    try:
        selected_snapshot = _effective_snapshot(actor, catalog_snapshot)
        resolution = resolve_wire_call(
            wire_name,
            arguments,
            actor=actor,
            catalog_snapshot=selected_snapshot,
        )
        args = dict(arguments or {})
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
                terms = [
                    term.casefold()
                    for term in str(args.get("query") or "").split()
                    if term
                ]
                matched = [
                    spec
                    for spec in snapshot_specs
                    if not terms
                    or all(
                        term in (
                            spec.capability_id
                            + " "
                            + spec.description
                            + " "
                            + spec.concrete_name
                        ).casefold()
                        for term in terms
                    )
                ]
                result = [
                    {"id": spec.capability_id, "description": spec.description}
                    for spec in matched[: int(args.get("limit") or 20)]
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
                    "next": "Describe selected capability IDs before invoking them.",
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
                        "source": (
                            "integration"
                            if available[capability_id].external
                            else "native"
                        ),
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
            return json.dumps(
                {
                    "status": status,
                    "capability_id": resolution.capability_id,
                    "result": str(result),
                },
                ensure_ascii=False,
            )
        return str(result)
    except WireToolError as error:
        return serialize_error(error)


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
    args = dict(arguments or {})
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
