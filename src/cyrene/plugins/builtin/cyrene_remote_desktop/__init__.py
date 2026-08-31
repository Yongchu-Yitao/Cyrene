"""Remote Desktop application Plugin pack.

The pack deliberately keeps privileged capture behind a provider boundary.
The built-in Electron provider controls windows in the current user session;
lock-screen and login access require the optional loopback companion.
"""

from cyrene.core.plugin import ExtensionContribution, PluginPack
from cyrene.plugins import (
    WORKBENCH_SURFACE,
    WorkbenchSurfaceContribution,
    WorkbenchSurfaceRenderer,
)


def application_setup(context) -> None:
    from .application import setup_application

    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_remote_desktop",
    description="View and control a paired Cyrene desktop with explicit target consent.",
    plugins=(),
    application_setup=application_setup,
    contributions=(
        ExtensionContribution(
            WORKBENCH_SURFACE,
            WorkbenchSurfaceContribution(
                id="remote-desktop",
                title="Remote Desktop",
                i18n={"zh": {"title": "远程桌面"}},
                renderer=WorkbenchSurfaceRenderer("plugin_view", "main"),
                lifetime="sticky",
                preferred_side="right",
            ),
        ),
    ),
    metadata={
        "i18n": {
            "en": {
                "name": "Remote Desktop",
                "description": "View and control a paired desktop with target-side consent.",
            },
            "zh": {
                "name": "远程桌面",
                "description": "在目标机明确授权后查看和控制已配对桌面。",
            },
        },
        "frontend_views": ({
            "id": "main",
            "entry": "ui/index.html",
            "title": "Remote Desktop",
            "i18n": {"zh": {"title": "远程桌面"}},
        },),
        "project_tools": ({
            "id": "remote-desktop",
            "view": "main",
            "title": "Remote Desktop",
            "subtitle": "Paired device screen and input",
            "icon_text": "▣",
            "i18n": {
                "zh": {
                    "title": "远程桌面",
                    "subtitle": "远程画面与鼠标键盘接管",
                }
            },
        },),
    },
)


__all__ = ["application_setup", "plugin_pack"]
