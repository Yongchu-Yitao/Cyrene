"""Editable Cyrene entity Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_entity",
    description="Track and query durable entities.",
    native_module_names=(
        "track_entity", "update_entity", "list_entities", "query_entities",
        "delete_entity",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 5:
    raise RuntimeError("entity pack must contain exactly 5 Plugins")

__all__ = ["plugin_pack"]
