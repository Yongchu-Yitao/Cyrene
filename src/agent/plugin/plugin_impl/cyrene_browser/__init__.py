"""Editable Cyrene browser Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_browser",
    description="Navigate and interact with browser sessions.",
    native_module_names=(
        "browser_navigate", "browser_snapshot", "browser_screenshot",
        "browser_click", "browser_click_ref", "browser_click_at",
        "browser_type", "browser_type_ref", "browser_upload_files",
        "browser_wait", "browser_network_log", "browser_tab_list",
        "browser_tab_new", "browser_tab_select", "browser_tab_close",
        "browser_scroll", "browser_user_events", "browser_request_takeover",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 18:
    raise RuntimeError("browser pack must contain exactly 18 Plugins")

__all__ = ["plugin_pack"]
