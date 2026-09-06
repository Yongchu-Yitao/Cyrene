"""Editable Cyrene agent-control Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from cyrene.core.plugin import Plugin, PluginPack, PluginSetupContext

from . import deep_reflect, enter_plan_mode, update_plan_progress

_MAIN_ONLY = {"enter_plan_mode", "update_plan_progress", "DeepReflect"}


def _setup(context: PluginSetupContext) -> None:
    context.provide(
        deep_reflect.SERVICE_ID,
        deep_reflect.DeepReflectionService(),
        replace=True,
    )


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if str(function["name"]) in _MAIN_ONLY:
        metadata["main_only"] = True
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
        handler=module.handler,
        permission_boundary=getattr(module, "permission_boundary", None),
        allow_parallel=bool(metadata.get("allow_parallel", not metadata.get("requires_order", True))),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_control",
    description="Plan, reflect, and update plan progress.",
    plugins=tuple(_plugin(module) for module in (
        enter_plan_mode,
        update_plan_progress,
        deep_reflect,
    )),
    setup=_setup,
)

__all__ = ["plugin_pack"]
