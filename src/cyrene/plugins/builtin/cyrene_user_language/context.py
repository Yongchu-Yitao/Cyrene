"""Project the live application language into each turn's system context."""

from __future__ import annotations

from cyrene.core.hook import TURN_START, HookEvent
from cyrene.core.plugin import PluginSetupContext
from cyrene.localization import app_language

_HOOK_ID = "cyrene-user-language-turn-start"
_PLUGIN_ID = "cyrene_user_language.mount"


def setup_user_language(context: PluginSetupContext) -> None:
    """Bind the language mount without caching a session's initial preference."""

    async def mount(_event: HookEvent) -> dict[str, str]:
        # Read the authoritative setting on every turn: run_context may have
        # been captured before the user changed the application's language.
        language = app_language()
        name = "Simplified Chinese" if language == "zh" else "English"
        return {
            "context": "\n".join((
                f'<user_language code="{language}">',
                f"The user's current Cyrene language preference is {name}.",
                f"Use {name} for all user-facing communication, including progress "
                "updates, questions, explanations, and final replies, unless the "
                "user explicitly requests another language. Follow an explicit "
                "language request for the scope specified by the user.",
                "Preserve code, commands, identifiers, paths, and exact quotations "
                "in their original form where needed. The language of tools, "
                "documents, or quoted material does not change the reply language.",
                "This preference supersedes earlier user_language snapshots.",
                "</user_language>",
            )),
            "context_position": "system",
            "context_kind": "user_language",
            "context_source": "cyrene_user_language",
        }

    if _HOOK_ID in {hook.id for hook in context.hooks.list()}:
        context.hooks.bind_plugin(_PLUGIN_ID, mount, replace=True)
        return
    context.hooks.register(
        TURN_START,
        mount,
        plugin_id=_PLUGIN_ID,
        hook_id=_HOOK_ID,
        root_only=True,
        failure_policy="closed",
    )


__all__ = ["setup_user_language"]
