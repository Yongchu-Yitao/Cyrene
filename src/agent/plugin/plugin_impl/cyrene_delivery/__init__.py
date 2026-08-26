"""Editable Cyrene delivery Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_delivery",
    description="Deliver messages, files, and notifications.",
    native_module_names=(
        "send_telegram", "send_message", "send_message_to_user", "send_file",
        "send_wechat_file", "send_notification",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 6:
    raise RuntimeError("delivery pack must contain exactly 6 Plugins")

__all__ = ["plugin_pack"]
