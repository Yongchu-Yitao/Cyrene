"""Editable Cyrene application Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_application",
    description="Inspect and control the local Cyrene application.",
    native_module_names=(
        "status", "window", "ui_snapshot", "ui_inspect", "ui_click",
        "ui_double_click", "ui_type", "ui_scroll", "ui_drag",
        "session_message", "settings_describe", "settings_read",
        "settings_update", "projects", "chats", "data", "updates",
        "lifecycle",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 18:
    raise RuntimeError("application pack must contain exactly 18 Plugins")

__all__ = ["plugin_pack"]
