"""Editable Cyrene extensions Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_extensions",
    description="Inspect and manage extensions, environments, and hooks.",
    native_module_names=(
        "list_environment", "search_environment", "manage_extensions",
        "manage_agent_hooks",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 4:
    raise RuntimeError("extensions pack must contain exactly 4 Plugins")

__all__ = ["plugin_pack"]
