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
                "rail_section": "project_tools",
                "items_method": "remoteDesktop.cards.list",
                "layout_projection_method": "remoteDesktop.layout.project",
                "close_method": "remoteDesktop.session.disconnect",
                "pane_owner_scope": "project",
                "click_behavior": "replace_workspace",
                "restore_layout": True,
                "title": "Remote Desktop",
                "subtitle": "Paired desktop devices",
                "icon_name": "remoteDesktop",
                "i18n": {
                    "zh": {
                        "title": "远程桌面",
                        "subtitle": "已配对的桌面设备",
                    }
                },
                "pane_menu": (
                    {
                        "id": "information",
                        "label": "Information",
                        "i18n": {"zh": {"label": "信息"}},
                        "kind": "information",
                        "placement": "root",
                        "icon_name": "infoCircle",
                        "fields": (
                            {
                                "label": "Status",
                                "i18n": {"zh": {"label": "状态"}},
                                "state_key": "state",
                                "value_labels": (
                                    {"value": "connected", "label": "Connected", "tone": "success", "i18n": {"zh": {"label": "已连接"}}},
                                    {"value": "connecting", "label": "Connecting", "i18n": {"zh": {"label": "正在连接"}}},
                                    {"value": "reconnecting", "label": "Reconnecting", "i18n": {"zh": {"label": "正在重连"}}},
                                    {"value": "ready", "label": "Ready", "i18n": {"zh": {"label": "就绪"}}},
                                    {"value": "offline", "label": "Offline", "i18n": {"zh": {"label": "离线"}}},
                                    {"value": "failed", "label": "Connection failed", "tone": "danger", "i18n": {"zh": {"label": "连接失败"}}},
                                ),
                            },
                            {
                                "label": "Device",
                                "i18n": {"zh": {"label": "设备"}},
                                "state_key": "title",
                            },
                            {
                                "label": "Platform",
                                "state_key": "platform",
                                "empty_label": "Unknown",
                                "i18n": {"zh": {"label": "平台", "empty_label": "未知"}},
                            },
                            {
                                "label": "Connection mode",
                                "i18n": {"zh": {"label": "连接方式"}},
                                "state_key": "preferred_mode",
                                "value_labels": (
                                    {"value": "current_desktop", "label": "Current desktop", "i18n": {"zh": {"label": "当前桌面"}}},
                                    {"value": "remote_login", "label": "System login", "i18n": {"zh": {"label": "系统登录"}}},
                                ),
                            },
                            {
                                "label": "Transport",
                                "empty_label": "Not connected",
                                "i18n": {"zh": {"label": "传输", "empty_label": "未连接"}},
                                "state_key": "transport_kind",
                            },
                            {
                                "label": "Network",
                                "empty_label": "Not connected",
                                "i18n": {"zh": {"label": "网络", "empty_label": "未连接"}},
                                "state_key": "network_status",
                                "value_labels": (
                                    {"value": "direct_only", "label": "Direct ICE only · TURN not configured", "i18n": {"zh": {"label": "仅直连 ICE · 尚未配置 TURN"}}},
                                    {"value": "relay_ready", "label": "TURN relay available", "i18n": {"zh": {"label": "TURN 中继可用"}}},
                                ),
                            },
                            {
                                "label": "Quality",
                                "i18n": {"zh": {"label": "画质"}},
                                "state_key": "quality_mode",
                                "group": "facts",
                                "value_labels": (
                                    {"value": "auto", "label": "Auto", "i18n": {"zh": {"label": "自动"}}},
                                    {"value": "smooth", "label": "Smooth", "i18n": {"zh": {"label": "流畅"}}},
                                    {"value": "balanced", "label": "Balanced", "i18n": {"zh": {"label": "均衡"}}},
                                    {"value": "clear", "label": "Clear", "i18n": {"zh": {"label": "清晰"}}},
                                ),
                            },
                            {
                                "label": "Clipboard",
                                "empty_label": "Not connected",
                                "i18n": {"zh": {"label": "剪贴板", "empty_label": "未连接"}},
                                "state_key": "clipboard_status",
                                "group": "facts",
                                "value_labels": (
                                    {"value": "ready", "label": "Ready", "i18n": {"zh": {"label": "已就绪"}}},
                                    {"value": "unavailable", "label": "Unavailable", "i18n": {"zh": {"label": "不可用"}}},
                                ),
                            },
                        ),
                    },
                    {
                        "id": "file_transfer",
                        "label": "File transfer",
                        "i18n": {"zh": {"label": "文件传输"}},
                        "kind": "action",
                        "placement": "root",
                        "icon_name": "file",
                        "frontend_action": "file_transfer",
                        "session_key": "session_id",
                        "requires_session": True,
                        "requires_state": "clipboard_file_available",
                    },
                    {
                        "id": "switch_display",
                        "label": "Switch display",
                        "i18n": {"zh": {"label": "切换显示器"}},
                        "kind": "action",
                        "placement": "root",
                        "icon_name": "device",
                        "frontend_action": "switch_display",
                        "session_key": "session_id",
                        "requires_session": True,
                        "requires_state": "display_select_available",
                    },
                    {
                        "id": "microphone",
                        "label": "Share microphone",
                        "i18n": {"zh": {"label": "共享麦克风"}},
                        "kind": "toggle",
                        "placement": "root",
                        "icon_name": "eventPulse",
                        "frontend_action": "toggle_microphone",
                        "state_key": "microphone_enabled",
                        "session_key": "session_id",
                        "requires_session": True,
                        "requires_state": "microphone_available",
                    },
                    {
                        "id": "disconnect",
                        "label": "Disconnect",
                        "i18n": {"zh": {"label": "断开连接"}},
                        "kind": "action",
                        "placement": "root",
                        "icon_name": "x",
                        "frontend_action": "disconnect",
                        "session_key": "session_id",
                        "requires_session": True,
                    },
                    {
                        "id": "connection_mode",
                        "label": "Connection mode",
                        "i18n": {"zh": {"label": "连接方式"}},
                        "kind": "radio-group",
                        "placement": "settings",
                        "state_key": "preferred_mode",
                        "available_values_state_key": "modes",
                        "argument_key": "mode",
                        "session_key": "session_id",
                        "requires_session": True,
                        "method": "remoteDesktop.mode.set",
                        "reload_view": True,
                        "options": (
                            {
                                "value": "current_desktop",
                                "label": "Current desktop",
                                "i18n": {"zh": {"label": "当前桌面"}},
                            },
                            {
                                "value": "remote_login",
                                "label": "System login",
                                "i18n": {"zh": {"label": "系统登录"}},
                            },
                        ),
                    },
                    {
                        "id": "quality",
                        "label": "Quality mode",
                        "i18n": {"zh": {"label": "画质模式"}},
                        "kind": "radio-group",
                        "placement": "settings",
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
