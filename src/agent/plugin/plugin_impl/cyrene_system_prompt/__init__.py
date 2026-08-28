"""Required editable system-prompt Plugin pack."""

from agent.plugin import PluginPack

from .service import setup_system_prompt


plugin_pack = PluginPack(
    id="cyrene_system_prompt",
    description="Mount the base Agent instructions at the start of system context.",
    plugins=(),
    setup=setup_system_prompt,
    metadata={
        "required": True,
        "i18n": {
            "en": {
                "name": "System prompt",
                "description": (
                    "Mount the base Agent instructions at the start of system context."
                ),
            },
            "zh": {
                "name": "系统提示词",
                "description": "在系统上下文最前方挂载 Agent 的基础指令。",
            },
        },
    },
)


__all__ = ["plugin_pack"]
