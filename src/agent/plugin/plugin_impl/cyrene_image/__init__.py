"""Editable Cyrene image Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_image",
    description="Generate image assets and attach their results.",
    native_module_names=("generate_image",),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 1:
    raise RuntimeError("image pack must contain exactly 1 Plugin")

__all__ = ["plugin_pack"]
