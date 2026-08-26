"""Editable Cyrene map Plugin pack."""

from ._runtime import RegistrationProvider, create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_map",
    description="Search places and calculate map routes.",
    native_module_names=(),
    registration_providers=(RegistrationProvider("tools", "register_to"),),
)
if len(plugin_pack.plugins) != 2:
    raise RuntimeError("map pack must contain exactly 2 Plugins")

__all__ = ["plugin_pack"]
