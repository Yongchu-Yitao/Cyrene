"""Editable Cyrene task Plugin pack."""

from ._runtime import create_plugin_pack

plugin_pack = create_plugin_pack(
    package_name=__name__,
    pack_id="cyrene_task",
    description="Schedule, manage, plan, and track tasks and goals.",
    native_module_names=(
        "schedule_task", "list_tasks", "edit_task", "pause_task",
        "resume_task", "cancel_task", "set_task_goal", "update_task_plan",
    ),
    registration_providers=(),
)
if len(plugin_pack.plugins) != 8:
    raise RuntimeError("task pack must contain exactly 8 Plugins")

__all__ = ["plugin_pack"]
