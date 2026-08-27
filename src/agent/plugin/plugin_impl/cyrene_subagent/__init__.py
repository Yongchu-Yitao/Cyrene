"""Editable Cyrene subagent Plugin pack."""

from collections.abc import Mapping
from types import ModuleType

from agent.plugin import Plugin, PluginPack

from . import (
    broadcast_agent_message,
    query_round,
    send_agent_message,
    spawn_subagent,
)


def _plugin(module: ModuleType, *, main_only: bool = False) -> Plugin:
    definition = module.TOOL_DEF
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise TypeError(f"{module.__name__} must define a function object")
    parameters = function.get("parameters")
    if not isinstance(parameters, Mapping):
        raise TypeError(f"{module.__name__} must define an input schema")
    return Plugin(
        name=str(function.get("name") or ""),
        description=str(function.get("description") or ""),
        input_schema=dict(parameters),
        handler=module.handler,
        allow_parallel=False,
        timeout_seconds=180.0,
        metadata={"main_only": main_only},
    )


plugin_pack = PluginPack(
    id="cyrene_subagent",
    description="Spawn and coordinate Cyrene subagents.",
    plugins=(
        _plugin(send_agent_message),
        _plugin(broadcast_agent_message),
        _plugin(spawn_subagent, main_only=True),
        _plugin(query_round, main_only=True),
    ),
)

__all__ = ["plugin_pack"]
