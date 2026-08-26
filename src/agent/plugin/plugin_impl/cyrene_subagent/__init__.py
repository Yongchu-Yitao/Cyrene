"""Editable Cyrene subagent Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_subagent",
    description="Spawn and coordinate Cyrene subagents.",
    native_module_names=(
        "send_agent_message", "broadcast_agent_message", "spawn_subagent",
        "query_round",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 4:
    raise RuntimeError("subagent pack must contain exactly 4 Plugins")

__all__ = ["plugin_pack"]
