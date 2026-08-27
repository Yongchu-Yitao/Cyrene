"""High-priority SessionStart Hook for SOUL.md."""

from __future__ import annotations

from collections.abc import Mapping

from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginSetupContext
from cyrene.localization import app_language, localized


def _enabled(data: Mapping[str, object]) -> bool:
    run_context = data.get("run_context")
    if isinstance(run_context, Mapping) and "soul_enabled" in run_context:
        return bool(run_context.get("soul_enabled"))
    return bool(data.get("soul_enabled", True))


def setup_soul(context: PluginSetupContext) -> None:
    run_context = context.data.get("run_context")
    explicit_language = (
        run_context.get("language")
        if isinstance(run_context, Mapping)
        else None
    ) or context.data.get("language")
    language = app_language(explicit_language)

    async def mount_soul(_event: HookEvent) -> dict[str, str]:
        if not _enabled(context.data):
            return {}
        soul = context.services.get("soul")
        reader = getattr(soul, "persona_context", None)
        if not callable(reader):
            raise RuntimeError(
                "SOUL context is enabled but soul service is unavailable"
            )
        content = str(reader() or "").strip()
        if not content:
            return {}
        return {
            "context": localized(
                "## Persona memory\n{content}",
                "## 人格记忆\n{content}",
                language=language,
                content=content,
            ),
            "context_position": "top",
        }

    hook_id = "cyrene-soul-session-start"
    plugin_id = "cyrene_soul.mount"
    existing = next(
        (hook for hook in context.hooks.list() if hook.id == hook_id),
        None,
    )
    if existing is not None:
        if existing.failure_policy == "closed":
            context.hooks.bind_plugin(plugin_id, mount_soul, replace=True)
            return
        # Upgrade the durable binding created by older builds, where this Hook
        # was fail-open. Merely rebinding the callable would retain the stale
        # persistence policy after a ContextTree restore.
        context.hooks.unregister(hook_id)
    context.hooks.register(
        SESSION_START,
        mount_soul,
        plugin_id=plugin_id,
        hook_id=hook_id,
        root_only=True,
        failure_policy="closed",
    )


__all__ = ["setup_soul"]
