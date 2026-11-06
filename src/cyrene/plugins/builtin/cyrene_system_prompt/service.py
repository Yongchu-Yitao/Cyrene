"""Required SessionStart mount for the selected Agent system prompt."""

from __future__ import annotations

from cyrene.core.hook import (
    SESSION_START,
    HookEvent,
    with_session_start_cache_fingerprint,
)
from cyrene.core.plugin import PluginSetupContext

from .system_prompt import SYSTEM_PROMPT
from .assistant_prompt import ASSISTANT_SYSTEM_PROMPT

_HOOK_ID = "cyrene-system-prompt-session-start"
_PLUGIN_ID = "cyrene_system_prompt.mount"


def setup_system_prompt(context: PluginSetupContext) -> None:
    """Mount the selected prompt before other system-context contributions."""

    def rendered_prompt() -> str:
        selected = (
            ASSISTANT_SYSTEM_PROMPT
            if context.data.get("personal_assistant") is True
            else SYSTEM_PROMPT
        )
        prompt = str(selected or "")
        return prompt.replace("{workspace}", str(context.workspace)).strip()

    async def mount_system_prompt(_event: HookEvent) -> dict[str, str]:
        content = rendered_prompt()
        if not content:
            raise RuntimeError("The required system prompt is empty")
        return {
            "context": content,
            "context_position": "system",
            "context_kind": "system_prompt",
            "context_source": "cyrene_system_prompt",
        }

    def cache_fingerprint(_event: HookEvent) -> str:
        return rendered_prompt()

    with_session_start_cache_fingerprint(mount_system_prompt, cache_fingerprint)

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
