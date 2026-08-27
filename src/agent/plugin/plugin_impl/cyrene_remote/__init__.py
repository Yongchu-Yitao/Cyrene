"""Editable Cyrene remote-device Plugin pack."""

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from . import action, files, harness, jobs, list_devices, run, status

_MAIN_ONLY = frozenset({
    "RemoteCyreneAction",
    "RemoteCyreneFiles",
    "RemoteCyreneJobs",
    "RemoteHarness",
    "RunRemoteCyrene",
})


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if name in _MAIN_ONLY:
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
    id="cyrene_remote",
    description="Operate explicitly selected paired Cyrene devices.",
    plugins=tuple(
        _plugin(module)
        for module in (
            list_devices,
            status,
            files,
            jobs,
            harness,
            action,
            run,
        )
    ),
)

__all__ = ["plugin_pack"]
