"""Cross-platform, Pane-native Remote Desktop PluginPack."""

from types import ModuleType
from typing import Any

from cyrene.core.plugin import Plugin, PluginPack
from cyrene.plugins.context import PluginApplicationContext

from . import inspect_desktop, list_sessions


def application_setup(context: PluginApplicationContext) -> None:
    from .application import setup_application

    setup_application(context)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {}),
        handler=module.handler,
        allow_parallel=not bool(metadata.get("requires_order", True)),
        timeout_seconds=float(metadata.get("timeout_seconds", 30.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_remote_desktop",
    description="Secure cross-platform remote desktop sessions for paired Cyrene devices.",
    plugins=tuple(_plugin(module) for module in (list_sessions, inspect_desktop)),
    application_setup=application_setup,
    metadata={
        "requires_plugin_packs": ("cyrene_remote",),
        "i18n": {
            "zh": {
                "name": "远程桌面",
                "description": "安全查看和控制已配对的 Cyrene 桌面设备。",
            }
        },
        "frontend_views": (
            {
                "id": "main",
                "entry": "frontend/index.html",
                "title": "Remote Desktop",
                # The view stays in a unique-origin sandbox. These narrowly
                # scoped declarations let the generic Plugin host opt into
                # only the browser/native capabilities this UI needs.
                "iframe_permissions": ("microphone", "autoplay"),
                "host_capabilities": ("clipboard_text",),
                "i18n": {"zh": {"title": "远程桌面"}},
            },
        ),
        "project_tools": (
            {
                "id": "remote_desktop",
                "view": "main",
                "presentation": "collection",
                "items_method": "remoteDesktop.cards.list",
                "layout_projection_method": "remoteDesktop.layout.project",
                "close_method": "remoteDesktop.session.disconnect",
                "click_behavior": "replace_workspace",
                "restore_layout": True,
                "title": "Remote Desktop",
                "subtitle": "Paired desktop devices",
                "icon_text": "▣",
                "i18n": {
                    "zh": {
                        "title": "远程桌面",
                        "subtitle": "已配对的桌面设备",
                    }
                },
                "pane_menu": (
                    {
                        "id": "quality",
                        "label": "Quality mode",
                        "kind": "radio-group",
                        "state_key": "quality_mode",
                        "argument_key": "quality_mode",
                        "session_key": "session_id",
                        "requires_session": True,
                        "method": "remoteDesktop.quality.set",
                        "options": (
                            {"value": "auto", "label": "Auto", "i18n": {"zh": {"label": "自动"}}},
                            {"value": "smooth", "label": "Smooth", "i18n": {"zh": {"label": "流畅"}}},
                            {"value": "balanced", "label": "Balanced", "i18n": {"zh": {"label": "均衡"}}},
                            {"value": "clear", "label": "Clear", "i18n": {"zh": {"label": "清晰"}}},
                        ),
                    },
                ),
            },
        ),
    },
)


__all__ = ["application_setup", "plugin_pack"]
