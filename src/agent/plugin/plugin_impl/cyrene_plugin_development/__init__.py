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
        "PluginManager": ("管理插件", "查看、启用、停用或删除已安装的插件与插件包。"),
        "PluginSourceManager": ("管理插件源码", "读取或精确修改任意可编辑插件源码；系统源码必须由用户审核。"),
        "HookManager": ("管理自动触发", "创建、修改或删除用户 Hook，并在用户确认后覆盖系统 Hook。"),
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
_RUNNER = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["command", "script"]},
        "executable": {"type": "string"},
        "path": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}},
        "env": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["type"],
    "additionalProperties": False,
}
_HOOK_ACTION = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["plugin", "command", "script"]},
        "executable": {"type": "string"},
        "path": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}},
        "env": {"type": "object", "additionalProperties": {"type": "string"}},
        "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60},
    },
    "required": ["type"],
    "additionalProperties": False,
}
_HOOK = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "event": {"type": "string", "enum": ["PreToolUse", "PostToolUse", "SessionStart", "TurnStart", "SessionEnd", "Stop"]},
        "new_hook_id": {"type": "string", "description": "New identity for a system Hook."},
        "new_event": {"type": "string", "enum": ["PreToolUse", "PostToolUse", "SessionStart", "TurnStart", "SessionEnd", "Stop", "ContextChange", "ContextUsed"], "description": "New trigger event for a system Hook."},
        "new_plugin_id": {"type": "string", "description": "New handler Plugin id for a system Hook."},
        "action_instruction": {"type": "string", "description": "Natural-language action used by Agent generation."},
        "matcher": {
            "type": ["string", "null"],
            "description": "Exact runtime tool name or glob for PreToolUse/PostToolUse. Null, empty, or '*' matches every tool.",
        },
        "enabled": {"type": "boolean"},
        "root_only": {"type": "boolean"},
        "priority": {"type": "integer", "minimum": -10000, "maximum": 10000},
        "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60},
        "failure_policy": {"type": "string", "enum": ["open", "block", "closed"]},
        "created_at": {"type": "string"},
        "config": {"type": "object"},
        "runner": _RUNNER,
        "action": _HOOK_ACTION,
    },
    "additionalProperties": False,
}

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
            {"type": "object", "properties": {"path": _PATH}, "required": ["path"], "additionalProperties": False},
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
        _plugin(
            "PluginManager",
            "List, enable, disable, or delete installed standalone Plugins and PluginPacks. Version update and rollback are intentionally outside this tool; use PluginSourceManager for source edits and PluginInstall for installation.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "enable", "disable", "delete"]},
                    "kind": {"type": "string", "enum": ["pack", "plugin"]},
                    "id": {"type": "string", "description": "PluginPack id or Plugin canonical name."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            tools.manage_plugins,
            read_only=False,
        ),
        _plugin(
            "PluginSourceManager",
            "List, read, write, or delete editable Plugin source files. Every mutation passes through the central reviewer. Built-in, seeded, and core source changes require an exact user-reviewed diff before user_confirmed may be true.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read", "write", "delete"]},
                    "path": {"type": "string", "description": "Path relative to the editable Plugin directory, or @core/<path> for a core tool source."},
                    "content": {"type": "string", "description": "Complete replacement file content for write."},
                    "expected_sha256": {"type": "string", "description": "Required current sha256 when replacing or deleting an existing file."},
                    "user_confirmed": {"type": "boolean", "description": "Set true only after the user reviewed the exact system-source diff and explicitly approved it."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            tools.manage_plugin_source,
            read_only=False,
        ),
        _plugin(
            "HookManager",
            "List, create, update, delete, or disable automatic triggers. User Hooks use reviewed command/script actions. System Hook mutations require the user to review the exact change before user_confirmed may be true.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "generate", "regenerate", "update", "delete", "enable", "disable", "test", "approve_proposal", "reject_proposal"]},
                    "scope": {"type": "string", "enum": ["user", "system", "all"], "default": "user"},
                    "hook_id": {"type": "string"},
                    "event": {"type": "string", "description": "Current system Hook event used to disambiguate duplicate ids."},
                    "plugin_id": {"type": "string", "description": "Current system Hook Plugin id used to disambiguate duplicate ids."},
                    "proposal_id": {"type": "string"},
                    "hook": _HOOK,
                    "user_confirmed": {"type": "boolean", "description": "Set true only after the user reviewed and explicitly approved the exact system Hook change."},
                    "confirmation_token": {"type": "string", "description": "One-time token returned with the exact system Hook preview."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            tools.manage_hooks,
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
