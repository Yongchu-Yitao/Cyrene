"""Editable Cyrene task Plugin pack."""

from types import ModuleType

from cyrene.core.plugin import Plugin, PluginPack

from . import set_task_goal, update_task_plan


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(
            function.get("parameters")
            or {"type": "object", "properties": {}}
        ),
        handler=module.handler,
        permission_boundary=getattr(module, "permission_boundary", None),
        allow_parallel=False,
        timeout_seconds=180.0,
        metadata={"main_only": True},
    )


plugin_pack = PluginPack(
    id="cyrene_task",
    description="Manage the current Workbench task goal and execution plan.",
    plugins=(_plugin(set_task_goal), _plugin(update_task_plan)),
)

__all__ = ["plugin_pack"]
