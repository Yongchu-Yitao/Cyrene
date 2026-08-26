"""Editable Cyrene remote-device Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_remote",
    description="Operate explicitly selected paired Cyrene devices.",
    native_module_names=(
        "list_devices", "status", "files", "jobs", "harness", "action", "run",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 7:
    raise RuntimeError("remote pack must contain exactly 7 Plugins")

__all__ = ["plugin_pack"]
