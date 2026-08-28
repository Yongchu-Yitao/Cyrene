"""Native CLI installation and tree-local Hook Plugin pack."""

from __future__ import annotations

from agent.hook import (
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    TURN_START,
    with_session_start_cache_fingerprint,
)
from agent.plugin import Plugin, PluginApplicationContext, PluginPack, PluginSetupContext

from .tools import definitions


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


def setup(context: PluginSetupContext) -> None:
    """Attach CLI dispatchers to the current tree's native HookSet."""

    service = context.services.get("cli")
    hooks_service = getattr(service, "hooks", None)
    if hooks_service is None or context.hooks is None:
        return
    existing = {hook.id for hook in context.hooks.list()}
    for event in (
        PRE_TOOL_USE,
        POST_TOOL_USE,
        SESSION_START,
        TURN_START,
        SESSION_END,
        STOP,
    ):
        slug = {
            PRE_TOOL_USE: "pre-tool-use",
            POST_TOOL_USE: "post-tool-use",
            SESSION_START: "session-start",
            TURN_START: "turn-start",
            SESSION_END: "session-end",
            STOP: "stop",
        }[event]
        hook_id = f"cyrene-cli-{slug}"
        plugin_id = f"cyrene_cli.{slug.replace('-', '_')}"

        async def dispatch(hook_event, *, _hooks=hooks_service):
            return await _hooks.dispatch(hook_event)

        if event == SESSION_START:
            def cache_fingerprint(_event, *, _hooks=hooks_service):
                return [
                    hook
                    for hook in _hooks.list()
                    if str(hook.get("event") or "") == SESSION_START
                    and hook.get("enabled", True) is not False
                ]

            with_session_start_cache_fingerprint(dispatch, cache_fingerprint)

        if hook_id in existing:
            context.hooks.bind_plugin(plugin_id, dispatch, replace=True)
        else:
            context.hooks.register(
                event,
                dispatch,
                plugin_id=plugin_id,
                hook_id=hook_id,
                root_only=event in {SESSION_START, TURN_START, SESSION_END},
                failure_policy="open",
            )


plugin_pack = PluginPack(
    id="cyrene_cli",
    description="Install CLI tools and connect approved commands through tree-local Hooks.",
    plugins=tuple(
        Plugin(
            name=str(definition["function"]["name"]),
            description=str(definition["function"].get("description") or ""),
            input_schema=dict(definition["function"].get("parameters") or {}),
            handler=handler,
            allow_parallel=read_only,
            metadata={
                "read_only": read_only,
                "resource_keys": ("cli:catalog",) if read_only else ("cli:global",),
                "main_only": not read_only,
            },
        )
        for definition, handler, read_only in definitions()
    ),
    setup=setup,
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "CLI Plugins",
                "description": "Install CLI tools and connect approved commands through tree-local Hooks.",
            },
            "zh": {
                "name": "CLI 插件",
                "description": "安装命令行工具，并通过树级 Hook 接入已批准的命令。",
            },
        }
    },
)


__all__ = ["application_setup", "plugin_pack", "setup"]
