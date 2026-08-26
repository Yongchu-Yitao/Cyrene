"""Editable Cyrene code Plugin pack."""

from ._runtime import RegistrationProvider, create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_code",
    description="Shell sessions, code analysis, Git, and workspace indexing.",
    native_module_names=(
        "start_shell", "send_shell", "list_shells", "read_shell",
        "interrupt_shell", "show_shell", "delete_shell",
    ),
    registration_providers=(
        RegistrationProvider("analysis", "register_to"),
        RegistrationProvider("git", "register_to"),
        RegistrationProvider("indexer", "register_to"),
    ),
)
if len(plugin_pack.plugins) != 19:
    raise RuntimeError("code pack must contain exactly 19 Plugins")

__all__ = ["plugin_pack"]
