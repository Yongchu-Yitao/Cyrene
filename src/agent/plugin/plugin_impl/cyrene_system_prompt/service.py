"""Required SessionStart mount for the editable base system prompt."""

from __future__ import annotations

from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginSetupContext

from .prompt import SYSTEM_PROMPT

_HOOK_ID = "cyrene-system-prompt-session-start"
_PLUGIN_ID = "cyrene_system_prompt.mount"


def setup_system_prompt(context: PluginSetupContext) -> None:
    """Mount the base prompt before every other system-context contribution."""

    async def mount_system_prompt(_event: HookEvent) -> dict[str, str]:
        content = str(SYSTEM_PROMPT or "").replace(
            "{workspace}", str(context.workspace)
        ).strip()
        if not content:
            raise RuntimeError("The required system prompt is empty")
        return {"context": content, "context_position": "system"}

    existing = {hook.id for hook in context.hooks.list()}
    if _HOOK_ID in existing:
        context.hooks.bind_plugin(_PLUGIN_ID, mount_system_prompt, replace=True)
        return
    context.hooks.register(
        SESSION_START,
        mount_system_prompt,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        failure_policy="closed",
    )


__all__ = ["setup_system_prompt"]
