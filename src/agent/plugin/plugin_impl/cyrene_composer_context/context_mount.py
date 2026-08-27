"""Single SessionStart projection for all composer-attached context."""

from __future__ import annotations

from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginSetupContext

_HOOK_ID = "cyrene-composer-context-session-start"
_PLUGIN_ID = "cyrene_composer_context.mount"


def setup_composer_context(context: PluginSetupContext) -> None:
    async def mount(_event: HookEvent) -> dict[str, str]:
        service = context.services.get("composer_context")
        builder = getattr(service, "build_session_context", None)
        if not callable(builder):
            raise RuntimeError(
                "required composer_context application service is unavailable"
            )
        content = str(
            builder(
                context.data,
                workspace=context.workspace,
                services=context.services,
            )
            or ""
        ).strip()
        return {"context": content} if content else {}

    existing = {hook.id for hook in context.hooks.list()}
    if _HOOK_ID in existing:
        context.hooks.bind_plugin(_PLUGIN_ID, mount, replace=True)
        return
    context.hooks.register(
        SESSION_START,
        mount,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        failure_policy="closed",
    )


__all__ = ["setup_composer_context"]
