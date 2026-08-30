"""Tree-local Hooks that turn an ordinary AgentSession into a subagent.

This is the key plugin boundary: child execution remains the generic Agent
kernel, while this pack contributes mode prompts and resource policy through
the same Hook protocol available to any other Plugin pack.
"""

from __future__ import annotations

from collections.abc import Mapping

from cyrene.core.hook import CONTEXT_USED, POST_TOOL_USE, PRE_TOOL_USE, TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext


def setup_child_runtime(context: PluginSetupContext, manager: object) -> None:
    if context.agent_id == "main":
        return

    agent_id = context.agent_id

    async def mount_mode(_event: HookEvent) -> dict[str, str]:
        return {
            "context": manager.mode_context(agent_id),
            "context_kind": "subagent_runtime_policy",
            "context_source": "cyrene_subagent",
            "position": "system",
        }

    async def review_tool(event: HookEvent) -> Mapping[str, object]:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else {}
        tool = tool if isinstance(tool, Mapping) else {}
        arguments = tool.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        return manager.review_tool(agent_id, str(tool.get("name") or ""), arguments)

    async def observe_tool(event: HookEvent) -> None:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else {}
        result = payload.get("result") if isinstance(payload, Mapping) else {}
        tool = tool if isinstance(tool, Mapping) else {}
        result = result if isinstance(result, Mapping) else {}
        arguments = tool.get("arguments")
        manager.record_tool_result(
            agent_id,
            str(tool.get("name") or ""),
            arguments if isinstance(arguments, Mapping) else {},
            result,
        )

    async def observe_context(event: HookEvent) -> None:
        usage = event.payload
        manager.observe_context(
            agent_id,
            int(getattr(usage, "tokens", 0) or 0),
            int(getattr(usage, "token_limit", 0) or 0),
        )

    registrations = (
        (
            TURN_START,
            mount_mode,
            "cyrene-subagent-mode-turn-start",
            "cyrene_subagent.runtime.mode",
            "closed",
        ),
        (
            PRE_TOOL_USE,
            review_tool,
            "cyrene-subagent-budget-pre-tool",
            "cyrene_subagent.runtime.pre_tool",
            "block",
        ),
        (
            POST_TOOL_USE,
            observe_tool,
            "cyrene-subagent-metrics-post-tool",
            "cyrene_subagent.runtime.post_tool",
            "closed",
        ),
        (
            CONTEXT_USED,
            observe_context,
            "cyrene-subagent-context-budget",
            "cyrene_subagent.runtime.context",
            "closed",
        ),
    )
    existing = {hook.id for hook in context.hooks.list()}
    for event, callback, hook_id, plugin_id, failure_policy in registrations:
        config = {"include_node_tokens": False} if event == CONTEXT_USED else {}
        if hook_id in existing:
            context.hooks.bind_plugin(plugin_id, callback, replace=True)
            context.hooks.update_config(hook_id, config)
        else:
            context.hooks.register(
                event,
                callback,
                plugin_id=plugin_id,
                hook_id=hook_id,
                root_only=(event == TURN_START),
                failure_policy=failure_policy,
                config=config,
            )


__all__ = ["setup_child_runtime"]
