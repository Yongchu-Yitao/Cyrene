"""Editable Cyrene renderer Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_renderer",
    description="Load Workbench renderer contracts.",
    native_module_names=("load_contract",),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 1:
    raise RuntimeError("renderer pack must contain exactly 1 Plugin")

__all__ = ["plugin_pack"]
