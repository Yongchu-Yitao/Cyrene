"""Fixed Plugins shipped inside the agent kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..plugin import PluginPack
from .bash import BASH_PLUGIN
from .permission import (
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
    PERMISSION_PLUGIN_ID,
    PERMISSION_SYSTEM_PROMPT,
    PermissionDecision,
    PermissionModel,
    PermissionReviewPlugin,
    UserRequestProvider,
)
from .read import READ_PLUGIN
from .toolbox import TOOLBOX_PLUGIN_NAME, create_toolbox_plugin
from .write import WRITE_PLUGIN

if TYPE_CHECKING:
    from ..registry import PluginRegistry


def create_core_plugin_pack(registry: PluginRegistry) -> PluginPack:
    """Create the core pack with a toolbox bound to this Registry instance."""

    return PluginPack(
        id="core",
        description="Fixed tools required by the agent kernel.",
        plugins=(
            BASH_PLUGIN,
            READ_PLUGIN,
            WRITE_PLUGIN,
            create_toolbox_plugin(registry),
        ),
    )

__all__ = [
    "BASH_PLUGIN",
    "PERMISSION_DECIDE_TOOL",
    "PERMISSION_DECIDE_TOOL_CHOICE",
    "PERMISSION_PLUGIN_ID",
    "PERMISSION_SYSTEM_PROMPT",
    "PermissionDecision",
    "PermissionModel",
    "PermissionReviewPlugin",
    "READ_PLUGIN",
    "TOOLBOX_PLUGIN_NAME",
    "UserRequestProvider",
    "WRITE_PLUGIN",
    "create_core_plugin_pack",
]
