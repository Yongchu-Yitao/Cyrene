"""Editable Cyrene skills Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_skills",
    description="Install, inspect, load, and run Cyrene skills.",
    native_module_names=(
        "install_skill", "uninstall_skill", "list_skills", "search_skills",
        "load_skill", "read_skill_resource", "get_learned_skill",
        "run_learned_skill",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 8:
    raise RuntimeError("skills pack must contain exactly 8 Plugins")

__all__ = ["plugin_pack"]
