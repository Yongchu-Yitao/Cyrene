"""Required live-guidance Session Plugin."""

from cyrene.core.plugin import PluginPack, PluginSetupContext

from .service import GuidanceService


def setup(context: PluginSetupContext) -> None:
    """Attach guidance only to the main Agent that owns the Workbench run."""

    if context.agent_id != "main":
        return
    owner = context.services.get("agent_session")
    if owner is None:
        raise RuntimeError("cyrene_guidance requires the current Agent session")
    if "guidance" in context.services:
        raise ValueError("cyrene_guidance service collision: guidance")
    context.provide(
        "guidance",
        GuidanceService(owner, context.services.get("guidance_channel")),
    )


plugin_pack = PluginPack(
    id="cyrene_guidance",
    description="Apply durable user guidance to an active Agent run.",
    plugins=(),
    setup=setup,
    metadata={
        "required": True,
        "i18n": {
            "en": {
                "name": "Live guidance",
                "description": "Apply durable user guidance to an active Agent run.",
            },
            "zh": {
                "name": "运行中引导",
                "description": "把持久化的用户引导应用到正在运行的智能体。",
            },
        },
    },
)


__all__ = ["GuidanceService", "plugin_pack", "setup"]
