"""Single TurnStart projection for all composer-attached context."""

from __future__ import annotations

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext

from .mount_plugin import PLUGIN_NAME, build_composer_context

_HOOK_ID = "cyrene-composer-context-turn-start"
_LEGACY_HOOK_ID = "cyrene-composer-context-session-start"


def setup_composer_context(context: PluginSetupContext) -> None:
    async def mount(_event: HookEvent) -> dict[str, str]:
        return build_composer_context(
            data=context.data,
            workspace=context.workspace,
            services=context.services,
        )

    existing = {hook.id: hook for hook in context.hooks.list()}
    if _LEGACY_HOOK_ID in existing:
        context.hooks.unregister(_LEGACY_HOOK_ID)
    if _HOOK_ID in existing:
        context.hooks.bind_plugin(PLUGIN_NAME, mount, replace=True)
        return
    context.hooks.register(
        TURN_START,
        mount,
        plugin_id=PLUGIN_NAME,
        hook_id=_HOOK_ID,
        root_only=True,
        failure_policy="closed",
    )


__all__ = ["setup_composer_context"]
