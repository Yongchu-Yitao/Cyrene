"""Required runtime-only context Plugin pack."""

from agent.plugin import PluginPack

from .service import setup_runtime_context


plugin_pack = PluginPack(
    id="cyrene_context",
    description=(
        "Mount required run-scoped ephemeral metadata and Task constraints."
    ),
    plugins=(),
    setup=setup_runtime_context,
    metadata={
        # Workbench host context is part of the durable Agent transcript, not
        # an optional capability.  Lock this infrastructure pack so disabling
        # it cannot silently remove Task constraints after router-side legacy
        # projection has been retired.
        "required": True,
        "i18n": {
            "en": {
                "name": "Runtime context",
                "description": (
                    "Mount required run-scoped ephemeral metadata and Task "
                    "constraints."
                ),
            },
            "zh": {
                "name": "运行上下文",
                "description": "挂载当前运行必需的临时元数据与任务约束。",
            },
        }
    },
)


__all__ = ["plugin_pack"]
