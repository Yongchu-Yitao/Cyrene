"""Editable Cyrene Microsoft Office Plugin pack."""

from ._runtime import RegistrationProvider, create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_office",
    description="Inspect, edit, render, and compose PowerPoint presentations.",
    native_module_names=(
        "setup", "list_sessions", "get_context", "inspect", "apply_batch",
        "render_slide", "tool_search",
    ),
    registration_providers=(
        RegistrationProvider("kit", "register_all", includes_metadata=True),
    ),
)
if len(plugin_pack.plugins) != 51:
    raise RuntimeError("office pack must contain exactly 51 Plugins")

__all__ = ["plugin_pack"]
