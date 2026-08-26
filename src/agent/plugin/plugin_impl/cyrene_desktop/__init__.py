"""Editable Cyrene desktop Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_desktop",
    description="Inspect and interact with desktop applications.",
    native_module_names=(
        "app_use", "app_ui_snapshot", "app_ui_inspect", "app_ui_click",
        "app_ui_double_click", "app_ui_type", "app_ui_scroll", "app_ui_drag",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 8:
    raise RuntimeError("desktop pack must contain exactly 8 Plugins")

__all__ = ["plugin_pack"]
