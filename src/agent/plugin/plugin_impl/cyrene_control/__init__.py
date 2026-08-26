"""Editable Cyrene agent-control Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_control",
    description="Ask the user, plan, reflect, and finish an interaction.",
    native_module_names=(
        "ask_user", "enter_plan_mode", "update_plan_progress",
        "deep_reflect", "quit",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 5:
    raise RuntimeError("control pack must contain exactly 5 Plugins")

__all__ = ["plugin_pack"]
