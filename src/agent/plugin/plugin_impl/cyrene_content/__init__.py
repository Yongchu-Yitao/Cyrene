"""Editable Cyrene content-access Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_content",
    description="Attachment, paged-result, and web content access.",
    native_module_names=(
        "read_tool_result", "analyze_attachment", "web_fetch", "web_search",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 4:
    raise RuntimeError("cyrene_content pack must contain exactly 4 Plugins")

__all__ = ["plugin_pack"]
