"""Example sandboxed Workbench view for the unified PluginPack framework."""

from agent.plugin import PluginPack

from .application import setup_application


plugin_pack = PluginPack(
    id="model_usage_example",
    description="Show Cyrene token usage in a sandboxed Workbench pane.",
    plugins=(),
    application_setup=setup_application,
    metadata={
        "i18n": {
            "en": {"name": "Model usage example", "description": "Show Cyrene token usage in a sandboxed Workbench pane."},
            "zh": {"name": "模型用量示例", "description": "在沙箱化 Workbench 分屏中显示 Cyrene Token 用量。"},
        },
        "frontend_views": ({
            "id": "usage", "entry": "ui/index.html", "title": "Model usage",
            "i18n": {"zh": {"title": "模型用量"}},
        },),
        "project_tools": ({
            "id": "usage", "view": "usage", "title": "Model usage",
            "subtitle": "Last 7 days", "icon_text": "Σ",
            "i18n": {"zh": {"title": "模型用量", "subtitle": "最近 7 天"}},
        },),
    },
)

__all__ = ["plugin_pack", "setup_application"]
