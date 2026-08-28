"""Editable Cyrene subagent Plugin pack."""

from collections.abc import Mapping
from types import ModuleType

from agent.plugin import (
    Plugin,
    PluginApplicationContext,
    PluginPack,
    PluginSetupContext,
)

from . import (
    broadcast_agent_message,
    query_round,
    send_agent_message,
    spawn_subagent,
)
from .policy import setup_spawn_policy_context


def setup(context: PluginSetupContext) -> None:
    """Own subagent coordination only for an enabled main Agent session."""

    if context.agent_id != "main":
        return
    owner = context.services.get("agent_session")
    if owner is None:
        raise RuntimeError("cyrene_subagent requires the current Agent session")
    collisions = {"subagents", "session_driver"} & set(context.services)
    if collisions:
        raise ValueError(
            "cyrene_subagent service collision: " + ", ".join(sorted(collisions))
        )

    from .manager import SubagentManager

    manager = SubagentManager(owner)
    setup_spawn_policy_context(context)
    context.provide("subagents", manager)
    context.provide("session_driver", manager)


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        plugin_setting_spec,
    )

    context.expose_frontend("subagent")
    specs = (
        plugin_setting_spec(
            "spawn_policy", "string", "conservative", tab="agents",
            enum=("aggressive", "conservative", "off"), apply_mode="next_run",
        ),
        plugin_setting_spec("subagent_execution_max_tool_calls", "integer", 200, tab="agents", minimum=1, maximum=5000),
        plugin_setting_spec("subagent_execution_max_wall_seconds", "integer", 1800, tab="agents", minimum=30, maximum=86400),
        plugin_setting_spec("subagent_execution_no_progress_turns", "integer", 3, tab="agents", minimum=1, maximum=20),
        plugin_setting_spec("subagent_execution_checkpoint_calls", "integer", 20, tab="agents", minimum=1, maximum=500),
        plugin_setting_spec("subagent_execution_max_cost_usd", "number", 5.0, tab="agents", minimum=0, maximum=1000),
        plugin_setting_spec("subagent_execution_max_context_tokens", "integer", 0, tab="agents", minimum=0, maximum=4_000_000),
        plugin_setting_spec("subagent_discussion_max_rounds", "integer", 5, tab="agents", minimum=1, maximum=50),
        plugin_setting_spec("subagent_discussion_max_messages_per_agent", "integer", 4, tab="agents", minimum=1, maximum=50),
        plugin_setting_spec("subagent_discussion_max_total_messages", "integer", 20, tab="agents", minimum=1, maximum=500),
        plugin_setting_spec("subagent_discussion_max_message_chars", "integer", 2000, tab="agents", minimum=100, maximum=20000),
        plugin_setting_spec("subagent_discussion_max_wall_seconds", "integer", 600, tab="agents", minimum=30, maximum=86400),
        plugin_setting_spec("subagent_discussion_max_tool_calls", "integer", 50, tab="agents", minimum=1, maximum=1000),
        plugin_setting_spec("subagent_discussion_no_new_info_rounds", "integer", 2, tab="agents", minimum=1, maximum=20),
    )
    context.provide(
        "subagent_settings",
        PluginSettingsContribution(specs=specs),
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
    setup=setup,
    application_setup=application_setup,
)

__all__ = ["application_setup", "plugin_pack", "setup"]
