"""Per-run immutable catalog snapshots."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from cyrene.runtime.settings_store import is_tool_pack_enabled
from cyrene.tooling.catalog import (
    TOOL_HANDLERS,
    TOOL_METADATA,
    all_capabilities,
    get_effective_function_definitions,
)
from cyrene.tooling.packs import WIRE_NAME_BY_PACK_ID
from cyrene.tooling.types import ToolCatalogSnapshot, ToolSpec


def _schema_hash(schema: dict) -> str:
    payload = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_catalog_snapshot(actor: str = "main") -> ToolCatalogSnapshot:
    capabilities: dict[str, ToolSpec] = {}
    schema_hashes: dict[str, str] = {}
    connectors: set[str] = set()
    from cyrene.tooling.wire import (
        DIRECT_TOOL_NAMES,
        SUBAGENT_DIRECT_TOOL_NAMES,
    )

    direct_names = (
        SUBAGENT_DIRECT_TOOL_NAMES
        if actor == "subagent"
        else DIRECT_TOOL_NAMES
    )
    definitions = get_effective_function_definitions()
    try:
        from cyrene.custom_tools.manager import get_custom_tool_manager

        custom_manager = get_custom_tool_manager()
    except Exception:
        custom_manager = None
    for concrete_name in direct_names:
        tool_def = definitions.get(concrete_name)
        custom_direct = bool(
            custom_manager is not None
            and custom_manager.has_tool(concrete_name)
            and is_tool_pack_enabled("custom_tools")
        )
        custom_identity = concrete_name
        custom_tool = None
        if custom_direct and custom_manager is not None:
            _package, custom_tool = custom_manager.resolve_tool(concrete_name)
            custom_identity = custom_tool.concrete_name
        handler = None if custom_direct else TOOL_HANDLERS.get(concrete_name)
        if tool_def is None:
            # use_tools is a wire-only phase control and has no catalog schema.
            continue
        function = tool_def.get("function") or {}
        input_schema = dict(
            function.get("parameters") or {"type": "object"}
        )
        metadata = TOOL_METADATA.get(concrete_name) or {}
        if custom_tool is not None:
            metadata = custom_tool.metadata
        read_only = bool(metadata.get("read_only"))
        capabilities[concrete_name] = ToolSpec(
            capability_id=concrete_name,
            # Freeze the selected custom implementation for the whole Agent
            # run.  Re-resolving the public name at execution time would let a
            # file edit silently swap schema/handler inside an active snapshot.
            concrete_name=custom_identity,
            pack_id="direct",
            description=str(function.get("description") or ""),
            input_schema=input_schema,
            handler=handler,
            actors=frozenset({actor}),
            risk_class=(
                "custom"
                if custom_direct
                else ("read_only" if read_only else "native")
            ),
            side_effect_class=(
                "unknown_custom"
                if custom_direct
                else ("none" if read_only else "unknown")
            ),
            resource_templates=tuple(metadata.get("resource_keys") or ()),
            timeout_seconds=(
                custom_manager.get_tool_timeout(concrete_name)
                if custom_direct and custom_manager is not None
                else 180.0
            ) or 180.0,
            source="custom" if custom_direct else "native",
        )
        schema_hashes[concrete_name] = _schema_hash(input_schema)
    for capability in all_capabilities(
        actor=actor,
        include_disabled=True,
    ):
        handler_name = capability.concrete_name.removeprefix("system:")
        handler = TOOL_HANDLERS.get(handler_name)
        if (
            handler is None
            and not capability.external
            and capability.source != "custom"
        ):
            continue
        if capability.source == "custom" and custom_manager is not None:
            try:
                metadata = custom_manager.get_tool_metadata(
                    capability.concrete_name
                )
            except (KeyError, ValueError):
                metadata = {}
        else:
            metadata = TOOL_METADATA.get(handler_name) or {}
        read_only = bool(metadata.get("read_only"))
        spec = ToolSpec(
            capability_id=capability.capability_id,
            concrete_name=capability.concrete_name,
            pack_id=capability.pack_id,
            description=capability.description,
            input_schema=capability.input_schema,
            handler=handler,
            actors=frozenset({actor}),
            risk_class=(
                "external"
                if capability.external
                else (
                    "custom"
                    if capability.source == "custom"
                    else ("read_only" if read_only else "native")
                )
            ),
            side_effect_class=(
                "unknown_external"
                if capability.external
                else (
                    "unknown_custom"
                    if capability.source == "custom"
                    else ("none" if read_only else "unknown")
                )
            ),
            resource_templates=tuple(metadata.get("resource_keys") or ()),
            timeout_seconds=(
                custom_manager.get_tool_timeout(capability.concrete_name)
                if capability.source == "custom" and custom_manager is not None
                else 180.0
            ) or 180.0,
            external=capability.external,
            source=capability.source,
        )
        capabilities[capability.capability_id] = spec
        schema_hashes[capability.capability_id] = _schema_hash(
            capability.input_schema
        )
        if capability.external:
            connectors.add(capability.concrete_name)
    version_payload = json.dumps(
        schema_hashes,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ToolCatalogSnapshot(
        version=hashlib.sha256(version_payload).hexdigest(),
        actor=actor,
        capabilities=MappingProxyType(capabilities),
        enabled_capability_ids=frozenset(
            capability_id
            for capability_id, spec in capabilities.items()
            if (
                spec.pack_id == "direct"
                or is_tool_pack_enabled(
                    WIRE_NAME_BY_PACK_ID.get(spec.pack_id, "")
                )
            )
        ),
        available_connector_ids=frozenset(connectors),
        schema_hashes=MappingProxyType(schema_hashes),
    )
