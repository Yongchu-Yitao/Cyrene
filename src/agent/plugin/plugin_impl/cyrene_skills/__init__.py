"""Editable Cyrene skills Plugin pack."""

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from .learning_capture import setup_learning_capture

from . import (
    get_learned_skill,
    install_skill,
    list_skills,
    load_skill,
    read_skill_resource,
    run_learned_skill,
    search_skills,
    uninstall_skill,
)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if name in {"InstallSkill", "UninstallSkill"}:
        metadata["main_only"] = True
    return Plugin(
        name=name,
        description=str(function.get("description") or ""),
        input_schema=dict(
            function.get("parameters")
            or {"type": "object", "properties": {}}
        ),
        handler=module.handler,
        allow_parallel=bool(
            metadata.get(
                "allow_parallel",
                not metadata.get("requires_order", True),
            )
        ),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_skills",
    description="Install, inspect, load, and run Cyrene skills.",
    plugins=tuple(
        _plugin(module)
        for module in (
            install_skill,
            uninstall_skill,
            list_skills,
            search_skills,
            load_skill,
            read_skill_resource,
            get_learned_skill,
            run_learned_skill,
        )
    ),
    setup=setup_learning_capture,
)

__all__ = ["plugin_pack"]
