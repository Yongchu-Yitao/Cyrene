"""Editable Cyrene trusted-plugin authoring Plugin pack."""

from ._runtime import RegistrationProvider, create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_plugins",
    description="Author, validate, install, and operate trusted Cyrene plugins.",
    native_module_names=(),
    registration_providers=(
        RegistrationProvider("tools", "register_all", includes_metadata=True),
    ),
)
if len(plugin_pack.plugins) != 12:
    raise RuntimeError("plugins pack must contain exactly 12 Plugins")

__all__ = ["plugin_pack"]
