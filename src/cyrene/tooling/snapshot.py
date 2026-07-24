"""Per-run immutable catalog snapshots."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from cyrene.settings_store import is_tool_pack_enabled
from cyrene.tooling.catalog import TOOL_DEFS, TOOL_HANDLERS, all_capabilities
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
    definitions = {
        str((tool_def.get("function") or {}).get("name") or ""): tool_def
        for tool_def in TOOL_DEFS
    }
    for concrete_name in direct_names:
        tool_def = definitions.get(concrete_name)
        handler = TOOL_HANDLERS.get(concrete_name)
        if tool_def is None:
            # use_tools is a wire-only phase control and has no catalog schema.
            continue
        function = tool_def.get("function") or {}
        input_schema = dict(
            function.get("parameters") or {"type": "object"}
        )
        capabilities[concrete_name] = ToolSpec(
            capability_id=concrete_name,
            concrete_name=concrete_name,
            pack_id="direct",
            description=str(function.get("description") or ""),
            input_schema=input_schema,
            handler=handler,
            actors=frozenset({actor}),
            risk_class="native",
            side_effect_class="unknown",
            resource_templates=(),
        )
        schema_hashes[concrete_name] = _schema_hash(input_schema)
    for capability in all_capabilities(
        actor=actor,
        include_disabled=True,
    ):
        handler = TOOL_HANDLERS.get(capability.concrete_name)
        if handler is None and not capability.external:
            continue
        spec = ToolSpec(
            capability_id=capability.capability_id,
            concrete_name=capability.concrete_name,
            pack_id=capability.pack_id,
            description=capability.description,
            input_schema=capability.input_schema,
            handler=handler,
            actors=frozenset({actor}),
            risk_class="external" if capability.external else "native",
            side_effect_class="unknown",
            resource_templates=(),
            external=capability.external,
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
