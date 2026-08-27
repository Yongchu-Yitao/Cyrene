"""Tools for creating PluginPack contributions for the unified framework."""

from __future__ import annotations

from agent.plugin import Plugin, PluginPack

from . import tools


def _plugin(name, description, schema, handler, *, read_only):
    zh = {
        "PluginAuthoringGuide": ("插件开发指南", "读取当前统一 PluginPack 协议与开发流程。"),
        "PluginScaffold": ("创建插件脚手架", "创建独立工具、工具包、模型、上下文、应用、UI 或组合插件。"),
        "PluginValidate": ("验证插件", "不执行插件代码，静态验证独立插件或 PluginPack。"),
        "PluginInstall": ("安装插件", "将验证通过的插件安装到用户插件目录并重载。"),
        "PluginReload": ("重载插件", "重新扫描用户插件目录并报告需重启的应用贡献。"),
    }[name]
    return Plugin(
        name=name,
        description=description,
        input_schema=schema,
        handler=handler,
        allow_parallel=read_only,
        timeout_seconds=120.0,
        metadata={
            "read_only": read_only,
            "main_only": not read_only,
            "resource_keys": ("plugins:registry",),
            "i18n": {"zh": {"name": zh[0], "description": zh[1]}},
        },
    )


_PATH = {"type": "string", "minLength": 1, "description": "Workspace-relative Plugin source path."}

plugin_pack = PluginPack(
    id="cyrene_plugin_development",
    description="Create, validate, install, and reload editable Cyrene PluginPacks.",
    plugins=(
        _plugin(
            "PluginAuthoringGuide",
            "Load the unified Cyrene PluginPack authoring, Workbench view, and RPC contract.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            tools.authoring_guide,
            read_only=True,
        ),
        _plugin(
            "PluginScaffold",
            "Create any supported Plugin source: standalone tool, tool pack, model Provider, context Hook, application service, sandboxed UI, or a full composable pack.",
            {
                "type": "object",
                "properties": {
                    "path": _PATH,
                    "plugin_type": {"type": "string", "enum": list(tools.SCAFFOLD_TYPES)},
                    "pack_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "plugin_name": {"type": "string", "minLength": 1, "description": "Optional executable tool identifier."},
                    "model_plugin_name": {"type": "string", "minLength": 1, "description": "Optional executable model Provider identifier."},
                    "provider_id": {"type": "string", "minLength": 1, "description": "Optional model Provider catalog id."},
                },
                "required": ["path", "plugin_type", "pack_id", "name"],
                "additionalProperties": False,
            },
            tools.scaffold,
            read_only=False,
        ),
        _plugin(
            "PluginValidate",
            "Validate standalone Plugin or PluginPack syntax, component declarations, view assets, i18n metadata, and references without executing plugin code.",
            {"type": "object", "properties": {"path": _PATH}, "required": ["path"], "additionalProperties": False},
            tools.validate,
            read_only=True,
        ),
        _plugin(
            "PluginInstall",
            "Install a validated standalone Plugin or PluginPack into Cyrene's editable user plugin directory and reload the unified registry.",
            {"type": "object", "properties": {"path": _PATH, "replace": {"type": "boolean"}}, "required": ["path"], "additionalProperties": False},
            tools.install,
            read_only=False,
        ),
        _plugin(
            "PluginReload",
            "Reload the unified editable Plugin registry and report application contributions that require a restart.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            tools.reload_plugins,
            read_only=False,
        ),
    ),
    metadata={
        "i18n": {
            "en": {"name": "Plugin development", "description": "Create, validate, install, and reload editable Cyrene PluginPacks."},
            "zh": {"name": "插件开发", "description": "创建、验证、安装并重载可编辑的 Cyrene PluginPack。"},
        }
    },
)


__all__ = ["plugin_pack"]
