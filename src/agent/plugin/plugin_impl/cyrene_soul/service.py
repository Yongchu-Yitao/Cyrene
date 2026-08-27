"""High-priority SessionStart Hook for SOUL.md."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginSetupContext

logger = logging.getLogger(__name__)


def _enabled(data: Mapping[str, object]) -> bool:
    run_context = data.get("run_context")
    if isinstance(run_context, Mapping) and "soul_enabled" in run_context:
        return bool(run_context.get("soul_enabled"))
    return bool(data.get("soul_enabled", True))


def setup_soul(context: PluginSetupContext) -> None:
    async def mount_soul(_event: HookEvent) -> dict[str, str]:
        if not _enabled(context.data):
            return {}
        try:
            # The memory pack remains the editor/store owner. This pack owns
            # only the context contribution, so activation has one clear effect.
            from agent.plugin.plugin_impl.cyrene_memory.soul import read_shallow_memory

            content = read_shallow_memory().strip()
        except Exception:
            logger.exception("Failed to read SOUL.md")
            return {}
        if not content:
            return {}
        return {
            "context": "## Persona memory\n" + content,
            "context_position": "top",
        }

    context.hooks.register(
        SESSION_START,
        mount_soul,
        plugin_id="cyrene_soul.mount",
        hook_id="cyrene-soul-session-start",
        root_only=True,
    )


__all__ = ["setup_soul"]
