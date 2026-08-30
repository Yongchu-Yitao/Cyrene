"""Authoring tools for the unified editable PluginPack framework."""

from __future__ import annotations

import ast
import difflib
import hashlib
import html
import json
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from cyrene.core.plugin import PluginContext, application_plugin_scope
from cyrene.plugins.native_runtime import (
    plugin_language,
    plugin_localized,
    resolve_workspace_path,
)


SCAFFOLD_TYPES = (
    "standalone_tool",
    "tool_pack",
    "model_plugin",
    "context_plugin",
    "application_plugin",
    "ui_plugin",
    "full_pack",
)


AUTHORING_GUIDE = """# Create a Cyrene Plugin

## Fast path

1. Call `PluginScaffold` with a new workspace path, `pack_id`, display `name`, and one type below.
2. Use `PluginSourceManager` to read/edit generated text files.
3. Call `PluginValidate`; fix every reported error.
4. Call `PluginInstall`. Use `PluginManager` only to list, enable, disable, or delete installed contributions.

Do not create `plugin.json`. A standalone file exports `plugin`; a pack directory exports
`plugin_pack` from `__init__.py`. Keep stable ids ASCII and add English/Chinese `metadata.i18n`.

## Choose exactly one scaffold type

- `standalone_tool`: one model-callable `.py` tool.
- `tool_pack`: a pack with a tool and room for more tools.
- `model_plugin`: an OpenAI-compatible model Provider with model discovery and completion.
- `context_plugin`: a `SessionStart` Hook that mounts Agent context.
- `application_plugin`: routes, services, startup, and shutdown.
- `ui_plugin`: application backend plus sandboxed Workbench UI and RPC example.
- `full_pack`: tool + model Provider + context Hook + application backend + UI in one pack.

Generated pack files are intentionally small: `tool.py` exposes `TOOL_PLUGIN`, `model.py`
exposes `MODEL_PLUGIN`, `context.py` registers the Hook, `application.py` registers backend
services/RPC, and `ui/index.html` is the sandboxed view. `__init__.py` composes them.

For UI, keep assets inside the pack; declare `frontend_views` and `project_tools` in pack
metadata, contribute a typed `WORKBENCH_SURFACE` when the view should participate in dynamic
split panes, and register backend calls with `provide_frontend_method`. Python/tool/model
changes reload live; application contribution changes may require restart.

For standalone automatic triggers outside a pack, call `HookManager(action="generate")` with
`hook={name, event, action_instruction, matcher?}`. For `PreToolUse`/`PostToolUse`, set
`matcher` to an exact runtime tool name; omit it or use `*` to match every tool. System source changes require exact diff review;
system Hook changes additionally require the matching one-time confirmation token.
"""

AUTHORING_GUIDE_ZH = """# 创建 Cyrene 插件

## 最短流程

1. 调用 `PluginScaffold`，提供新的工作区路径、`pack_id`、显示名称和下列一种类型。
2. 用 `PluginSourceManager` 读取或编辑生成的文本源码。
3. 调用 `PluginValidate`，修复全部错误。
4. 调用 `PluginInstall`；安装后只用 `PluginManager` 查看、启用、停用或删除。

不要创建 `plugin.json`。独立文件必须导出 `plugin`；插件包目录必须从 `__init__.py`
导出 `plugin_pack`。稳定 ID 使用 ASCII，并提供中英文 `metadata.i18n`。

## 选择一种脚手架

- `standalone_tool`：单个可被模型调用的 `.py` 工具。
- `tool_pack`：包含一个工具、可继续扩展多个工具的插件包。
- `model_plugin`：支持模型发现与补全的 OpenAI-compatible Provider。
- `context_plugin`：通过 `SessionStart` Hook 挂载 Agent 上下文。
- `application_plugin`：路由、服务、启动与关闭逻辑。
- `ui_plugin`：应用后端、沙箱化 Workbench UI 和 RPC 示例。
- `full_pack`：在同一个包中包含工具、模型 Provider、Context Hook、应用后端和 UI。

生成文件保持精简：`tool.py` 导出 `TOOL_PLUGIN`，`model.py` 导出 `MODEL_PLUGIN`，
`context.py` 注册 Hook，`application.py` 注册后端服务/RPC，`ui/index.html` 提供沙箱
视图，最后由 `__init__.py` 组合。

UI 资源必须位于插件包内；在 metadata 声明 `frontend_views` 与 `project_tools`，并用
typed `WORKBENCH_SURFACE` contribution 接入动态分屏，再用 `provide_frontend_method`
注册后端调用。工具和模型代码可重载，应用贡献修改可能需要重启。

若要创建包外的自动触发，调用 `HookManager(action="generate")`，并传入
`hook={name, event, action_instruction, matcher?}`。对于 `PreToolUse`/`PostToolUse`，
`matcher` 填运行时工具名称；省略或填 `*` 表示所有工具。系统源码修改必须先审核完整差异；系统 Hook
修改还必须携带与该修改完全匹配的一次性确认令牌。
"""


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


_VALIDATION_ZH = {
    "standalone Plugin must be a Python file": "独立 Plugin 必须是 Python 文件",
    "standalone module must construct Plugin and expose it as plugin": "独立模块必须构造 Plugin，并通过 plugin 导出",
    "Plugin kind must be tool or model": "Plugin kind 必须是 tool 或 model",
    "metadata is dynamic; i18n requires runtime validation": "metadata 为动态值；i18n 需要在运行时验证",
    "Plugin metadata.i18n must be an object": "Plugin metadata.i18n 必须是对象",
    "standalone Plugin filename cannot start with . or _": "独立 Plugin 文件名不能以 . 或 _ 开头",
    "PluginPack directory requires __init__.py": "PluginPack 目录必须包含 __init__.py",
    "__init__.py must construct PluginPack and expose it as plugin_pack": "__init__.py 必须构造 PluginPack，并通过 plugin_pack 导出",
    "metadata is dynamic; frontend contributions require runtime validation": "metadata 为动态值；前端贡献需要在运行时验证",
    "__init__.py must expose the PluginPack as plugin_pack": "__init__.py 必须通过 plugin_pack 导出 PluginPack",
    "PluginPack id contains unsupported characters": "PluginPack id 包含不支持的字符",
    "metadata.frontend_views must be an array": "metadata.frontend_views 必须是数组",
    "each frontend view must be an object": "每个前端视图都必须是对象",
    "metadata.project_tools must be an array": "metadata.project_tools 必须是数组",
    "each project tool must be an object": "每个项目工具都必须是对象",
    "Plugin source does not exist": "Plugin 源码不存在",
}


def _validation_message(message: Any, context: PluginContext) -> str:
    english = str(message or "")
    chinese = _VALIDATION_ZH.get(english)
    dynamic = (
        ("invalid or duplicate frontend view id: ", "前端视图 id 无效或重复："),
        ("frontend view entry does not exist inside the pack: ", "PluginPack 内不存在该前端视图入口："),
        ("invalid or duplicate project tool id: ", "项目工具 id 无效或重复："),
    )
    if chinese is None:
        for prefix, translated in dynamic:
            if english.startswith(prefix):
                chinese = translated + english[len(prefix):]
                break
    if chinese is None and english.startswith("frontend view ") and english.endswith(" i18n must be an object"):
        identity = english[len("frontend view "):-len(" i18n must be an object")]
        chinese = f"前端视图 {identity} 的 i18n 必须是对象"
    if chinese is None and english.startswith("project tool "):
        chinese = "项目工具验证失败：" + english[len("project tool "):]
    if chinese is None:
        chinese = "Plugin 验证详情：" + english
    return plugin_localized(context, english, chinese)


def _localized_validation(value: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    result = dict(value)
    for field in ("errors", "warnings"):
        items = result.get(field)
        if isinstance(items, list):
            result[field] = [_validation_message(item, context) for item in items]
    return result


def _find_pack_call(tree: ast.AST) -> ast.Call | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "PluginPack":
            return node
        if isinstance(function, ast.Attribute) and function.attr == "PluginPack":
            return node
    return None


def _constructor_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _assigned_constructor(tree: ast.AST, variable: str, constructor: str) -> ast.Call | None:
    for node in getattr(tree, "body", ()):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == variable for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and _constructor_name(value) == constructor:
            return value
    return None


def _literal_keyword(call: ast.Call, name: str, default: Any = None) -> Any:
    for keyword in call.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                return default
    return default


def _plugin_calls(trees: dict[Path, ast.AST]) -> tuple[ast.Call, ...]:
    return tuple(
        node
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _constructor_name(node) == "Plugin"
    )


def validate_standalone_plugin(source: Path) -> dict[str, Any]:
    source = source.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not source.is_file() or source.suffix != ".py":
        return {
            "ok": False,
            "installable": False,
            "path": str(source),
            "errors": ["standalone Plugin must be a Python file"],
            "warnings": [],
        }
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return {
            "ok": False,
            "installable": False,
            "path": str(source),
            "errors": [str(exc)],
            "warnings": [],
        }
    call = _assigned_constructor(tree, "plugin", "Plugin")
    if call is None:
        errors.append("standalone module must construct Plugin and expose it as plugin")
        plugin_name = source.stem
        plugin_kind = "tool"
    else:
        plugin_name = str(_literal_keyword(call, "name", source.stem) or source.stem)
        plugin_kind = str(_literal_keyword(call, "kind", "tool") or "tool")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", plugin_name):
            errors.append(f"Plugin name contains unsupported characters: {plugin_name}")
        if plugin_kind not in {"tool", "model"}:
            errors.append("Plugin kind must be tool or model")
        metadata = _literal_keyword(call, "metadata", None)
        if metadata is None:
            warnings.append("metadata is dynamic; i18n requires runtime validation")
        elif not isinstance(metadata, dict) or not isinstance(metadata.get("i18n", {}), dict):
            errors.append("Plugin metadata.i18n must be an object")
    if source.name.startswith((".", "_")):
        errors.append("standalone Plugin filename cannot start with . or _")
    return {
        "ok": not errors,
        "installable": not errors,
        "path": str(source),
        "source_type": "standalone",
        "plugin_name": plugin_name,
        "plugin_kind": plugin_kind,
        "errors": errors,
        "warnings": warnings,
    }


def validate_pack_directory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    initializer = root / "__init__.py"
    if not initializer.is_file():
        return {"ok": False, "path": str(root), "errors": ["PluginPack directory requires __init__.py"], "warnings": []}
    trees: dict[Path, ast.AST] = {}
    for source in sorted(root.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        try:
            trees[source] = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{source.relative_to(root)}: {exc}")
    tree = trees.get(initializer)
    call = _find_pack_call(tree) if tree is not None else None
    if call is None:
        errors.append("__init__.py must construct PluginPack and expose it as plugin_pack")
        pack_id = root.name
        metadata: Any = {}
    else:
        pack_id = str(_literal_keyword(call, "id", root.name) or root.name)
        metadata = _literal_keyword(call, "metadata", None)
        if metadata is None:
            warnings.append("metadata is dynamic; frontend contributions require runtime validation")
            metadata = {}
    assigned_pack = _assigned_constructor(tree, "plugin_pack", "PluginPack") if tree is not None else None
    if assigned_pack is None:
        errors.append("__init__.py must expose the PluginPack as plugin_pack")
    if not pack_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in pack_id):
        errors.append("PluginPack id contains unsupported characters")
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", pack_id):
        errors.append("PluginPack id contains unsupported characters")
    views = metadata.get("frontend_views", ()) if isinstance(metadata, dict) else ()
    tools = metadata.get("project_tools", ()) if isinstance(metadata, dict) else ()
    view_ids: set[str] = set()
    if not isinstance(views, (list, tuple)):
        errors.append("metadata.frontend_views must be an array")
        views = ()
    for raw in views:
        if not isinstance(raw, dict):
            errors.append("each frontend view must be an object")
            continue
        view_id = str(raw.get("id") or "")
        entry = str(raw.get("entry") or "").replace("\\", "/")
        if not view_id or view_id in view_ids:
            errors.append(f"invalid or duplicate frontend view id: {view_id}")
        view_ids.add(view_id)
        candidate = (root / entry).resolve()
        if not entry or (candidate != root and root not in candidate.parents) or not candidate.is_file():
            errors.append(f"frontend view entry does not exist inside the pack: {entry}")
        if not isinstance(raw.get("i18n", {}), dict):
            errors.append(f"frontend view {view_id} i18n must be an object")
    if not isinstance(tools, (list, tuple)):
        errors.append("metadata.project_tools must be an array")
        tools = ()
    tool_ids: set[str] = set()
    for raw in tools:
        if not isinstance(raw, dict):
            errors.append("each project tool must be an object")
            continue
        tool_id = str(raw.get("id") or "")
        view_id = str(raw.get("view") or "")
        if not tool_id or tool_id in tool_ids:
            errors.append(f"invalid or duplicate project tool id: {tool_id}")
        tool_ids.add(tool_id)
        if view_id not in view_ids:
            errors.append(f"project tool {tool_id} references missing view: {view_id}")
        if not isinstance(raw.get("i18n", {}), dict):
            errors.append(f"project tool {tool_id} i18n must be an object")
    plugin_calls = _plugin_calls(trees)
    for item in plugin_calls:
        component_name = str(_literal_keyword(item, "name", "") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", component_name):
            errors.append(f"Plugin name contains unsupported characters: {component_name}")
        component_metadata = _literal_keyword(item, "metadata", None)
        if component_metadata is None:
            warnings.append(f"Plugin {component_name or '<dynamic>'} metadata is dynamic")
        elif not isinstance(component_metadata, dict) or not isinstance(
            component_metadata.get("i18n", {}), dict
        ):
            errors.append(f"Plugin {component_name} metadata.i18n must be an object")
    tool_count = sum(
        1 for item in plugin_calls if str(_literal_keyword(item, "kind", "tool")) == "tool"
    )
    model_count = sum(
        1 for item in plugin_calls if str(_literal_keyword(item, "kind", "tool")) == "model"
    )
    has_setup = bool(call and any(keyword.arg == "setup" for keyword in call.keywords))
    has_application = bool(
        call and any(keyword.arg == "application_setup" for keyword in call.keywords)
    )
    return {
        "ok": not errors,
        "installable": not errors,
        "path": str(root),
        "source_type": "pack",
        "pack_id": pack_id,
        "tool_count": tool_count,
        "model_count": model_count,
        "has_context_setup": has_setup,
        "has_application_setup": has_application,
        "frontend_view_count": len(view_ids),
        "project_tool_count": len(tool_ids),
        "errors": errors,
        "warnings": warnings,
    }


def validate_plugin_source(source: Path) -> dict[str, Any]:
    source = source.resolve()
    if source.is_file():
        return validate_standalone_plugin(source)
    if source.is_dir():
        return validate_pack_directory(source)
    return {
        "ok": False,
        "installable": False,
        "path": str(source),
        "errors": ["Plugin source does not exist"],
        "warnings": [],
    }


def _component_identifier(value: str, suffix: str = "") -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    base = "".join(word[:1].upper() + word[1:] for word in words) or "Custom"
    if base[0].isdigit():
        base = f"Plugin{base}"
    return f"{base}{suffix}"


def _plugin_i18n(name: str, description: str) -> str:
    return repr({
        "en": {"name": name, "description": description},
        "zh": {"name": name, "description": description},
    })


def _tool_source(plugin_name: str, name: str, description: str, *, standalone: bool) -> str:
    export = "plugin" if standalone else "TOOL_PLUGIN"
    return f'''"""Tool Plugin generated by Cyrene."""

from cyrene.core.plugin import Plugin, PluginContext


async def run(arguments: dict, context: PluginContext) -> dict:
    """Replace this starter behavior with the tool implementation."""
    return {{
        "ok": True,
        "message": str(arguments.get("message") or ""),
        "workspace": str(context.workspace or ""),
    }}


{export} = Plugin(
    name={plugin_name!r},
    description={description!r},
    input_schema={{
        "type": "object",
        "properties": {{"message": {{"type": "string"}}}},
        "required": ["message"],
        "additionalProperties": False,
    }},
    handler=run,
    allow_parallel=True,
    metadata={{
        "agent_exposure": "discoverable",
        "i18n": {_plugin_i18n(name, description)},
    }},
)

__all__ = [{export!r}, "run"]
'''


def _model_source(plugin_name: str, provider_id: str, name: str, description: str) -> str:
    return f'''"""OpenAI-compatible model Provider generated by Cyrene."""

from __future__ import annotations

import time
from collections.abc import Mapping

import httpx

from cyrene.core.plugin import Plugin, PluginContext


def _connection(context: PluginContext) -> Mapping:
    value = context.services.get("model_connection") or context.data.get("model_connection")
    return value if isinstance(value, Mapping) else {{}}


async def run(arguments: dict, context: PluginContext) -> dict:
    connection = _connection(context)
    base_url = str(connection.get("base_url") or "http://localhost:8000/v1").rstrip("/")
    api_key = str(connection.get("api_key") or "")
    headers = {{"Content-Type": "application/json"}}
    if api_key:
        headers["Authorization"] = f"Bearer {{api_key}}"
    operation = str(arguments.get("operation") or "complete")
    timeout = float(connection.get("timeout") or 180)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if operation == "list_models":
            response = await client.get(f"{{base_url}}/models", headers=headers)
            response.raise_for_status()
            body = response.json()
            items = body.get("data", []) if isinstance(body, Mapping) else []
            models = [
                {{"id": str(item.get("id")), "model": str(item.get("id")), "name": str(item.get("id"))}}
                for item in items if isinstance(item, Mapping) and item.get("id")
            ]
            return {{"provider": {provider_id!r}, "models": models}}
        payload = {{
            "model": str(arguments.get("model") or connection.get("model") or ""),
            "messages": arguments["messages"],
            "stream": False,
        }}
        for key in ("tools", "tool_choice", "max_tokens", "temperature", "response_format"):
            if arguments.get(key) is not None:
                payload[key] = arguments[key]
        started = time.perf_counter()
        endpoint = f"{{base_url}}/chat/completions"
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    choices = body.get("choices", []) if isinstance(body, Mapping) else []
    choice = choices[0] if choices and isinstance(choices[0], Mapping) else {{}}
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {{}}
    return {{
        "content": str(message.get("content") or ""),
        "reasoning": str(message.get("reasoning_content") or ""),
        "tool_calls": list(message.get("tool_calls") or []),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "usage": dict(body.get("usage") or {{}}),
        "model": str(body.get("model") or payload["model"]),
        "response_id": str(body.get("id") or ""),
        "latency_ms": (time.perf_counter() - started) * 1000,
        "endpoint": endpoint,
    }}


MODEL_PLUGIN = Plugin(
    name={plugin_name!r},
    description={description!r},
    kind="model",
    handler=run,
    timeout_seconds=180,
    input_schema={{
        "type": "object",
        "properties": {{
            "operation": {{"type": "string", "enum": ["complete", "list_models"], "default": "complete"}},
            "messages": {{"type": "array"}}, "model": {{"type": "string"}},
            "tools": {{"type": "array"}}, "tool_choice": {{}},
            "max_tokens": {{"type": "integer"}}, "temperature": {{"type": "number"}},
            "response_format": {{"type": "object"}},
        }},
        "anyOf": [
            {{"required": ["messages"], "properties": {{"operation": {{"const": "complete"}}}}}},
            {{"required": ["operation"], "properties": {{"operation": {{"const": "list_models"}}}}}},
        ],
        "additionalProperties": False,
    }},
    metadata={{
        "provider": {{
            "id": {provider_id!r}, "name": {name!r}, "adapter": "openai_compatible",
            "default_base_url": "http://localhost:8000/v1", "auth_type": "optional",
            "capabilities": ["chat", "tools"], "supports_discovery": True,
        }},
        "i18n": {_plugin_i18n(name, description)},
    }},
)

__all__ = ["MODEL_PLUGIN", "run"]
'''


def _context_source(pack_id: str, name: str) -> str:
    hook_id = f"{pack_id}-session-start"
    plugin_id = f"{pack_id}.mount"
    mounted = f"## {name}\nReplace this text with plugin context."
    return f'''"""Session context Plugin generated by Cyrene."""

from cyrene.core.hook import SESSION_START, HookEvent
from cyrene.core.plugin import PluginSetupContext


def setup_context(context: PluginSetupContext) -> None:
    async def mount(_event: HookEvent) -> dict[str, str]:
        # Keep SessionStart output stable. Use TurnStart for per-turn values;
        # attach a cache fingerprint provider for mutable stable dependencies.
        return {{"context": {mounted!r}}}

    existing = {{item.id for item in context.hooks.list()}}
    if {hook_id!r} in existing:
        context.hooks.bind_plugin({plugin_id!r}, mount, replace=True)
        return
    context.hooks.register(
        SESSION_START,
        mount,
        plugin_id={plugin_id!r},
        hook_id={hook_id!r},
        root_only=True,
        failure_policy="open",
    )


__all__ = ["setup_context"]
'''


def _initializer_source(
    pack_id: str,
    name: str,
    description: str,
    *,
    include_tool: bool,
    include_model: bool,
    include_context: bool,
    include_application: bool,
    include_ui: bool,
) -> str:
    imports = ["from cyrene.core.plugin import PluginPack"]
    plugin_names: list[str] = []
    exports = ["plugin_pack"]
    if include_tool:
        imports.append("from .tool import TOOL_PLUGIN")
        plugin_names.append("TOOL_PLUGIN")
        exports.append("TOOL_PLUGIN")
    if include_model:
        imports.append("from .model import MODEL_PLUGIN")
        plugin_names.append("MODEL_PLUGIN")
        exports.append("MODEL_PLUGIN")
    if include_context:
        imports.append("from .context import setup_context")
        exports.append("setup_context")
    if include_application:
        imports.append("from .application import setup_application")
        exports.append("setup_application")
    metadata_lines = [
        '"i18n": {',
        f'    "en": {{"name": {name!r}, "description": {description!r}}},',
        f'    "zh": {{"name": {name!r}, "description": {description!r}}},',
        "},",
    ]
    if include_ui:
        imports[0] = "from cyrene.core.plugin import ExtensionContribution, PluginPack"
        imports.append(
            "from cyrene.plugins import WORKBENCH_SURFACE, WorkbenchSurfaceContribution, WorkbenchSurfaceRenderer"
        )
        metadata_lines.extend([
            '"frontend_views": ({',
            f'    "id": "main", "entry": "ui/index.html", "title": {name!r},',
            f'    "i18n": {{"en": {{"title": {name!r}}}, "zh": {{"title": {name!r}}}}},',
            "},),",
            '"project_tools": ({',
            f'    "id": "main", "view": "main", "title": {name!r},',
            '    "subtitle": "Plugin view", "icon_text": "◇",',
            f'    "i18n": {{"en": {{"title": {name!r}, "subtitle": "Plugin view"}}, "zh": {{"title": {name!r}, "subtitle": "插件视图"}}}},',
            "},),",
        ])
    setup = "setup_context" if include_context else "None"
    application_setup = "setup_application" if include_application else "None"
    plugins = ", ".join(plugin_names)
    if len(plugin_names) == 1:
        plugins += ","
    contributions = (
        "(ExtensionContribution(WORKBENCH_SURFACE, WorkbenchSurfaceContribution(\n"
        "        id=\"main\",\n"
        "        title=" + repr(name) + ",\n"
        "        renderer=WorkbenchSurfaceRenderer(\"plugin_view\", \"main\"),\n"
        "        resource_kinds=(\"file\", \"directory\",),\n"
        "        preferred_side=\"right\",\n"
        "    )),)"
        if include_ui
        else "()"
    )
    return f'''"""PluginPack generated by Cyrene."""

{chr(10).join(imports)}


plugin_pack = PluginPack(
    id={pack_id!r},
    description={description!r},
    plugins=({plugins}),
    setup={setup},
    application_setup={application_setup},
    contributions={contributions},
    metadata={{
        {chr(10).join(metadata_lines).replace(chr(10), chr(10) + "        ")}
    }},
)

__all__ = {exports!r}
'''


def _application_source(pack_id: str, *, include_ui: bool) -> str:
    frontend_registration = (
        '    context.provide_frontend_method("ping", ping)\n'
        if include_ui
        else ""
    )
    return f'''"""Application contribution for the PluginPack."""

from cyrene.plugins.context import PluginApplicationContext


async def ping(arguments, request_context):
    return {{"ok": True, "echo": arguments, "project_id": request_context["project_id"]}}


async def startup() -> None:
    pass


async def shutdown() -> None:
    pass


def setup_application(context: PluginApplicationContext) -> None:
    @context.router.get({f'/{pack_id}/status'!r})
    async def status():
        return {{"ok": True}}

    context.provide({f'{pack_id}.starter_service'!r}, {{"ready": True}})
    context.on_startup(startup)
    context.on_shutdown(shutdown)
{frontend_registration.rstrip()}
'''


def _ui_source(name: str) -> str:
    safe = json.dumps(name, ensure_ascii=False)
    title = html.escape(name, quote=True)
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui,sans-serif; }}
    body {{ margin:0; padding:24px; color:CanvasText; background:transparent; }}
    main {{ padding:20px; border:1px solid color-mix(in srgb,CanvasText 16%,transparent); border-radius:16px; }}
    button {{ font:inherit; padding:8px 14px; }} pre {{ white-space:pre-wrap; }}
  </style>
</head>
<body>
  <main><h1 id="title"></h1><button id="ping">Test backend</button><pre id="output">Ready</pre></main>
  <script>
    document.querySelector('#title').textContent = {safe};
    const pending = new Map();
    function call(method,args) {{
      const requestId = crypto.randomUUID();
      parent.postMessage({{source:'cyrene-plugin',type:'call',requestId,method,args}}, '*');
      return new Promise((resolve,reject) => pending.set(requestId,{{resolve,reject}}));
    }}
    addEventListener('message', event => {{
      const message = event.data || {{}};
      if (message.source !== 'cyrene-host') return;
      if (message.type === 'init') window.pluginContext = message.context;
      if (message.type !== 'response') return;
      const request = pending.get(message.requestId); if (!request) return;
      pending.delete(message.requestId);
      message.ok ? request.resolve(message.result) : request.reject(new Error(message.error));
    }});
    document.querySelector('#ping').addEventListener('click', async () => {{
      try {{ document.querySelector('#output').textContent = JSON.stringify(await call('ping',{{from:'ui'}}),null,2); }}
      catch (error) {{ document.querySelector('#output').textContent = String(error); }}
    }});
  </script>
</body>
</html>
'''


async def authoring_guide(_arguments: dict[str, Any], _context: PluginContext) -> str:
    return _json({
        "ok": True,
        "protocol": "PluginPack",
        "guide": (
            AUTHORING_GUIDE_ZH
            if plugin_language(_context) == "zh"
            else AUTHORING_GUIDE
        ),
    })


async def scaffold(arguments: dict[str, Any], context: PluginContext) -> str:
    target = resolve_workspace_path(str(arguments.get("path") or ""), context)
    plugin_type = str(arguments.get("plugin_type") or "").strip()
    if plugin_type not in SCAFFOLD_TYPES:
        return _json({"ok": False, "error": plugin_localized(
            context,
            "plugin_type must be one of: {values}",
            "plugin_type 必须是以下值之一：{values}",
            values=", ".join(SCAFFOLD_TYPES),
        )})
    pack_id = str(arguments.get("pack_id") or "").strip()
    name = str(arguments.get("name") or pack_id).strip()
    description = str(arguments.get("description") or name).strip()
    if not pack_id or not name:
        return _json({"ok": False, "error": plugin_localized(
            context,
            "pack_id and name are required.",
            "必须提供 pack_id 和 name。",
        )})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", pack_id):
        return _json({"ok": False, "error": plugin_localized(
            context,
            "pack_id contains unsupported characters.",
            "pack_id 包含不支持的字符。",
        )})
    tool_name = str(arguments.get("plugin_name") or _component_identifier(pack_id, "Tool")).strip()
    model_name = str(arguments.get("model_plugin_name") or _component_identifier(pack_id, "Model")).strip()
    provider_id = str(arguments.get("provider_id") or pack_id).strip().lower()
    for label, value in (("plugin_name", tool_name), ("model_plugin_name", model_name)):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            return _json({"ok": False, "error": plugin_localized(
                context,
                "{label} contains unsupported characters.",
                "{label} 包含不支持的字符。",
                label=label,
            )})
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", provider_id):
        return _json({"ok": False, "error": plugin_localized(
            context,
            "provider_id contains unsupported characters.",
            "provider_id 包含不支持的字符。",
        )})

    if plugin_type == "standalone_tool":
        if target.suffix != ".py":
            return _json({"ok": False, "error": plugin_localized(
                context,
                "A standalone_tool path must end in .py.",
                "standalone_tool 路径必须以 .py 结尾。",
            )})
        if target.exists():
            return _json({"ok": False, "error": plugin_localized(context, "The scaffold target must be new.", "脚手架目标必须是新文件。")})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_tool_source(tool_name, name, description, standalone=True), encoding="utf-8")
        validation = _localized_validation(validate_standalone_plugin(target), context)
        return _json({
            "ok": validation["ok"], "plugin_type": plugin_type,
            "path": str(target), "files": [target.name], "validation": validation,
        })

    root = target
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        return _json({"ok": False, "error": plugin_localized(context, "The scaffold target must be new or empty.", "脚手架目标必须是新目录或空目录。")})
    flags = {
        "tool_pack": (True, False, False, False, False),
        "model_plugin": (False, True, False, False, False),
        "context_plugin": (False, False, True, False, False),
        "application_plugin": (False, False, False, True, False),
        "ui_plugin": (False, False, False, True, True),
        "full_pack": (True, True, True, True, True),
    }
    include_tool, include_model, include_context, include_application, include_ui = flags[plugin_type]
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "__init__.py": _initializer_source(
            pack_id, name, description,
            include_tool=include_tool,
            include_model=include_model,
            include_context=include_context,
            include_application=include_application,
            include_ui=include_ui,
        ),
    }
    if include_tool:
        files["tool.py"] = _tool_source(tool_name, name, description, standalone=False)
    if include_model:
        files["model.py"] = _model_source(model_name, provider_id, name, description)
    if include_context:
        files["context.py"] = _context_source(pack_id, name)
    if include_application:
        files["application.py"] = _application_source(pack_id, include_ui=include_ui)
    if include_ui:
        files["ui/index.html"] = _ui_source(name)
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    validation = _localized_validation(validate_pack_directory(root), context)
    return _json({
        "ok": validation["ok"], "plugin_type": plugin_type,
        "path": str(root), "files": list(files), "validation": validation,
    })


async def validate(arguments: dict[str, Any], context: PluginContext) -> str:
    source = resolve_workspace_path(str(arguments.get("path") or ""), context)
    return _json(_localized_validation(validate_plugin_source(source), context))


async def install(arguments: dict[str, Any], context: PluginContext) -> str:
    source = resolve_workspace_path(str(arguments.get("path") or ""), context)
    validation = _localized_validation(validate_plugin_source(source), context)
    if not validation.get("ok"):
        return _json(validation)
    host = application_plugin_scope()
    if host is None:
        return _json({"ok": False, "error": plugin_localized(
            context,
            "The Plugin application host is unavailable.",
            "Plugin 应用宿主当前不可用。",
        )})
    source_type = str(validation.get("source_type") or "")
    identity = str(validation.get("pack_id") or validation.get("plugin_name") or source.stem)
    target = host.plugin_directory / (identity if source_type == "pack" else source.name)
    if target.exists():
        return _json({
            "ok": False,
            "error": plugin_localized(
                context,
                "Plugin source already exists; edit or delete it instead.",
                "插件源码已存在；请改用编辑或删除。",
            ),
            "path": str(target),
        })
    staging_root = Path(tempfile.mkdtemp(prefix=f".{identity}.install-", dir=host.plugin_directory))
    staged = staging_root / target.name
    backup = host.plugin_directory / f".{target.name}.backup"
    try:
        if source_type == "pack":
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        else:
            shutil.copy2(source, staged)
        if backup.exists():
            shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
        if target.exists():
            target.rename(backup)
        staged.rename(target)
        if backup.is_dir():
            shutil.rmtree(backup, ignore_errors=True)
        elif backup.exists():
            backup.unlink()
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    seed, failures = await host.reload_user_plugins()
    return _json({
        "ok": not failures,
        "source_type": source_type,
        "identity": identity,
        "path": str(target),
        "failures": [{
            "path": str(item.path),
            "error": plugin_localized(
                context,
                "Plugin source failed to load: {detail}",
                "Plugin 源码加载失败：{detail}",
                detail=item.error,
            ),
        } for item in failures],
        "restart_required": source_type == "pack" and host.pack_restart_required(identity),
        "seeded": {"created": [str(path) for path in seed.created], "updated": [str(path) for path in seed.updated]},
    })


async def reload_plugins(_arguments: dict[str, Any], _context: PluginContext) -> str:
    host = application_plugin_scope()
    if host is None:
        return _json({"ok": False, "error": plugin_localized(
            _context,
            "The Plugin application host is unavailable.",
            "Plugin 应用宿主当前不可用。",
        )})
    _seed, failures = await host.reload_user_plugins()
    return _json({
        "ok": not failures,
        "failures": [{
            "path": str(item.path),
            "error": plugin_localized(
                _context,
                "Plugin source failed to load: {detail}",
                "Plugin 源码加载失败：{detail}",
                detail=item.error,
            ),
        } for item in failures],
        "restart_required_packs": list(host.restart_required_packs),
    })


async def manage_plugins(arguments: dict[str, Any], context: PluginContext) -> str:
    """Manage the installed state without introducing update/rollback semantics."""

    from cyrene.plugins.native_tools import mark_builtin_plugin_deleted
    from cyrene.platform import settings_store

    host = application_plugin_scope()
    if host is None:
        return _json({"ok": False, "error": plugin_localized(
            context, "Plugin application host is unavailable.", "插件应用宿主当前不可用。"
        )})
    action = str(arguments.get("action") or "list").strip().lower()
    kind = str(arguments.get("kind") or "").strip().lower()
    identity = str(arguments.get("id") or "").strip()
    registry = host.registry
    if action == "list":
        packs = [{
            "kind": "pack",
            "id": pack.id,
            "description": pack.description,
            "enabled": registry.pack_enabled(pack.id),
            "locked": registry.pack_locked(pack.id),
            "source": registry.pack_source(pack.id),
            "plugins": [plugin.canonical_name for plugin in pack.plugins],
        } for pack in registry.list_packs()]
        plugins = [{
            "kind": "plugin",
            "id": item.plugin.canonical_name,
            "name": item.plugin.name,
            "plugin_kind": item.plugin.kind,
            "pack_id": item.pack_id or "",
            "enabled": registry.plugin_enabled(item.plugin.name),
            "locked": registry.plugin_locked(item.plugin.name),
            "source": item.source,
        } for item in registry.list_plugins()]
        return _json({"ok": True, "packs": packs, "plugins": plugins})
    if action not in {"enable", "disable", "delete"}:
        return _json({"ok": False, "error": "action must be list, enable, disable, or delete"})
    if kind not in {"pack", "plugin"} or not identity:
        return _json({"ok": False, "error": "kind and id are required"})
    if action in {"enable", "disable"}:
        enabled = action == "enable"
        try:
            if kind == "pack":
                registry.set_pack_enabled(identity, enabled)
            else:
                match = next(
                    (
                        item for item in registry.list_plugins()
                        if item.plugin.canonical_name == identity or item.plugin.name == identity
                    ),
                    None,
                )
                if match is None:
                    raise ValueError("Plugin not found")
                registry.set_plugin_enabled(match.plugin.name, enabled)
            snapshot = registry.activation.snapshot()
            settings_store.save_enabled_plugins(snapshot.plugins)
            settings_store.save_enabled_plugin_packs(snapshot.packs)
            await host.reconcile_activation()
        except Exception as exc:
            return _json({"ok": False, "error": str(exc)})
        return _json({"ok": True, "action": action, "kind": kind, "id": identity})

    plugin_root = Path(host.plugin_directory).resolve()
    if kind == "pack":
        try:
            source = registry.pack_source(identity)
        except Exception as exc:
            return _json({"ok": False, "error": str(exc)})
        source_path = Path(source).resolve() if source != "core" else Path()
        if source == "core" or source_path.parent != plugin_root:
            return _json({"ok": False, "error": "PluginPack is not a managed installed pack"})
    else:
        match = next(
            (
                item for item in registry.list_plugins()
                if item.plugin.canonical_name == identity or item.plugin.name == identity
            ),
            None,
        )
        if match is None:
            return _json({"ok": False, "error": "Plugin not found"})
        if match.pack_id is not None:
            return _json({
                "ok": False,
                "error": "This Plugin belongs to a PluginPack; delete the pack instead.",
                "pack_id": match.pack_id,
            })
        source_path = Path(match.source).resolve() if match.source != "core" else Path()
        if match.source == "core" or source_path.parent != plugin_root:
            return _json({"ok": False, "error": "Plugin is not a managed installed Plugin"})
    try:
        mark_builtin_plugin_deleted(plugin_root, source_path.name)
        if source_path.is_dir() and not source_path.is_symlink():
            shutil.rmtree(source_path)
        else:
            source_path.unlink()
        _seed, failures = await host.reload_user_plugins()
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})
    return _json({
        "ok": not failures,
        "action": "delete",
        "kind": kind,
        "id": identity,
        "restart_required": bool(host.restart_required_packs),
        "failures": [{"path": str(item.path), "error": item.error} for item in failures],
    })


_EDITABLE_SUFFIXES = frozenset({
    ".py", ".json", ".html", ".css", ".js", ".jsx", ".mjs", ".md", ".txt", ".svg",
})
_SYSTEM_HOOK_CONFIRMATION_TTL_SECONDS = 600
_PENDING_SYSTEM_HOOK_CONFIRMATIONS: dict[str, tuple[str, float]] = {}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return _sha256(encoded)


def _issue_system_hook_confirmation(value: Any) -> str:
    now = time.monotonic()
    for token, (_digest, expires_at) in tuple(
        _PENDING_SYSTEM_HOOK_CONFIRMATIONS.items()
    ):
        if expires_at <= now:
            _PENDING_SYSTEM_HOOK_CONFIRMATIONS.pop(token, None)
    token = secrets.token_urlsafe(24)
    _PENDING_SYSTEM_HOOK_CONFIRMATIONS[token] = (
        _stable_digest(value),
        now + _SYSTEM_HOOK_CONFIRMATION_TTL_SECONDS,
    )
    return token


def _consume_system_hook_confirmation(token: Any, value: Any) -> bool:
    normalized = str(token or "").strip()
    pending = _PENDING_SYSTEM_HOOK_CONFIRMATIONS.pop(normalized, None)
    if pending is None:
        return False
    digest, expires_at = pending
    return expires_at > time.monotonic() and secrets.compare_digest(
        digest,
        _stable_digest(value),
    )


def _source_target(raw_path: Any) -> tuple[Path, Path, str, bool]:
    value = str(raw_path or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("path is required")
    host = application_plugin_scope()
    if host is None:
        raise RuntimeError("Plugin application host is unavailable")
    is_core = value.startswith("@core/")
    if is_core:
        root = (Path(__file__).resolve().parents[2] / "core_impl").resolve()
        relative = value.removeprefix("@core/")
        system = True
    else:
        root = Path(host.plugin_directory).resolve()
        relative = value
        manifest = root / ".upstream-hashes.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            seeded = payload.get("files") if isinstance(payload, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            seeded = {}
        parts = Path(relative).parts
        top = parts[0] if parts else ""
        system = isinstance(seeded, dict) and any(
            str(item).replace("\\", "/").split("/", 1)[0] == top
            for item in seeded
        )
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise ValueError("path must stay inside the selected Plugin source root")
    if target.suffix.lower() not in _EDITABLE_SUFFIXES:
        raise ValueError("file type is not editable through PluginSourceManager")
    relative_public = target.relative_to(root).as_posix()
    return root, target, f"@core/{relative_public}" if is_core else relative_public, system


def _confirmation_required(
    context: PluginContext,
    *,
    target: str,
    kind: str,
    preview: Any = None,
    confirmation_token: str = "",
) -> str:
    return _json({
        "ok": False,
        "code": "user_confirmation_required",
        "requires_user_review": True,
        "target": target,
        "target_kind": kind,
        "preview": preview,
        "confirmation_token": confirmation_token,
        "error": plugin_localized(
            context,
            "This system-level change requires the user to review the exact diff and confirm it.",
            "这是系统级修改，必须先由用户审核具体差异并明确确认。",
        ),
    })


async def manage_plugin_source(arguments: dict[str, Any], context: PluginContext) -> str:
    """Read or precisely mutate editable Plugin source under central review."""

    action = str(arguments.get("action") or "list").strip().lower()
    host = application_plugin_scope()
    if host is None:
        return _json({"ok": False, "error": plugin_localized(
            context, "Plugin application host is unavailable.", "Plugin 应用宿主当前不可用。"
        )})
    if action == "list":
        user_root = Path(host.plugin_directory).resolve()
        core_root = (Path(__file__).resolve().parents[2] / "core_impl").resolve()
        items: list[dict[str, Any]] = []
        for root, prefix in ((user_root, ""), (core_root, "@core/")):
            if not root.is_dir():
                continue
            for candidate in sorted(root.rglob("*")):
                if len(items) >= 1000:
                    break
                if not candidate.is_file() or candidate.suffix.lower() not in _EDITABLE_SUFFIXES or "__pycache__" in candidate.parts:
                    continue
                _, _, public_path, system = _source_target(prefix + candidate.relative_to(root).as_posix())
                items.append({
                    "path": public_path,
                    "system": system,
                    "size": candidate.stat().st_size,
                    "sha256": _sha256(candidate.read_bytes()),
                })
        return _json({"ok": True, "count": len(items), "items": items})
    try:
        _root, target, public_path, system = _source_target(arguments.get("path"))
    except (TypeError, ValueError, RuntimeError) as exc:
        return _json({"ok": False, "error": str(exc)})
    if action == "read":
        if not target.is_file():
            return _json({"ok": False, "code": "plugin_source_not_found", "path": public_path})
        data = target.read_bytes()
        return _json({
            "ok": True, "path": public_path, "system": system,
            "sha256": _sha256(data), "content": data.decode("utf-8"),
        })
    if action not in {"write", "delete"}:
        return _json({"ok": False, "error": "action must be list, read, write, or delete"})
    existing = target.read_bytes() if target.is_file() else None
    expected = str(arguments.get("expected_sha256") or "").strip()
    if existing is not None and (not expected or expected != _sha256(existing)):
        return _json({
            "ok": False, "code": "source_revision_mismatch", "path": public_path,
            "current_sha256": _sha256(existing),
            "error": plugin_localized(
                context,
                "Read the current file and submit its exact sha256 before modifying it.",
                "修改前必须先读取当前文件，并提交完全匹配的 sha256。",
            ),
        })
    if system and arguments.get("user_confirmed") is not True:
        proposed = "" if action == "delete" else str(arguments.get("content") or "")
        current_text = existing.decode("utf-8", errors="replace") if existing is not None else ""
        diff = "".join(difflib.unified_diff(
            current_text.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{public_path}",
            tofile=f"b/{public_path}",
        ))
        return _confirmation_required(
            context,
            target=public_path,
            kind="system_plugin_source",
            preview={
                "action": action,
                "current_sha256": _sha256(existing) if existing is not None else "",
                "proposed_sha256": _sha256(proposed.encode("utf-8")),
                "diff": diff[:20000],
                "diff_truncated": len(diff) > 20000,
            },
        )
    if action == "delete":
        if existing is None:
            return _json({"ok": False, "code": "plugin_source_not_found", "path": public_path})
        target.unlink()
    else:
        content = arguments.get("content")
        if not isinstance(content, str):
            return _json({"ok": False, "error": "content must be a string"})
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            return _json({"ok": False, "error": "content exceeds 2 MB"})
        if target.suffix.lower() == ".py":
            try:
                ast.parse(content, filename=str(target))
            except SyntaxError as exc:
                return _json({"ok": False, "code": "python_syntax_invalid", "error": str(exc)})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    failures: list[dict[str, str]] = []
    restart_required = system and public_path.startswith("@core/")
    if not restart_required:
        _seed, loaded_failures = await host.reload_user_plugins()
        failures = [{"path": str(item.path), "error": item.error} for item in loaded_failures]
    return _json({
        "ok": not failures,
        "action": action,
        "path": public_path,
        "system": system,
        "sha256": _sha256(target.read_bytes()) if target.is_file() else "",
        "restart_required": restart_required,
        "failures": failures,
    })


async def manage_hooks(arguments: dict[str, Any], context: PluginContext) -> str:
    """Manage user Hooks and reviewed system-Hook overrides."""

    from cyrene.plugins.builtin.cyrene_cli.hooks import CliHookService, public_hook
    from cyrene.workbench.core_adapter.hook_listing import runtime_hook_listing, update_runtime_hook

    action = str(arguments.get("action") or "list").strip().lower()
    scope = str(arguments.get("scope") or "user").strip().lower()
    if scope not in {"user", "system", "all"}:
        return _json({"ok": False, "error": "scope must be user, system, or all"})
    host = application_plugin_scope()
    if host is None:
        return _json({"ok": False, "error": "Plugin application host is unavailable"})
    service = context.services.get("cli")
    user_hooks = getattr(service, "hooks", None) or CliHookService()
    if action == "list":
        result: dict[str, Any] = {"ok": True}
        if scope in {"user", "all"}:
            listing = service.hook_listing() if callable(
                getattr(service, "hook_listing", None)
            ) else {}
            result["user_hooks"] = listing.get("hooks") or [
                public_hook(item) for item in user_hooks.list()
            ]
            result["proposals"] = list(listing.get("proposals") or [])
            result["configuration_results"] = dict(
                listing.get("configuration_results") or {}
            )
        if scope in {"system", "all"}:
            result["system_hooks"] = runtime_hook_listing(host.db_path)
        return _json(result)
    mutation = arguments.get("hook", {})
    if not isinstance(mutation, dict):
        return _json({"ok": False, "error": "hook must be an object"})
    hook_id = str(arguments.get("hook_id") or mutation.get("id") or "").strip()
    if scope == "system":
        if action not in {"update", "disable"}:
            return _json({"ok": False, "error": "system Hooks support update or disable"})
        current_event = str(arguments.get("event") or "").strip()
        current_plugin_id = str(arguments.get("plugin_id") or "").strip()
        matches = [
            item for item in runtime_hook_listing(host.db_path)
            if item["id"] == hook_id
            and (not current_event or item["event"] == current_event)
            and (not current_plugin_id or item["plugin_id"] == current_plugin_id)
        ]
        if len(matches) != 1:
            return _json({"ok": False, "code": "system_hook_not_found_or_ambiguous"})
        current = matches[0]
        payload = {
            **dict(mutation),
            "event": current["event"],
            "plugin_id": current["plugin_id"],
            "enabled": False if action == "disable" else mutation.get("enabled", current["enabled"]),
            "acknowledge_risk": True,
        }
        proposed = dict(current)
        for key in (
            "enabled", "root_only", "matcher", "failure_policy", "config",
            "created_at", "action",
        ):
            if key in payload:
                proposed[key] = payload[key]
        proposed["id"] = str(payload.get("new_hook_id", current["id"]))
        proposed["event"] = str(payload.get("new_event", current["event"]))
        proposed["plugin_id"] = str(
            payload.get("new_plugin_id", current["plugin_id"])
        )
        confirmation_value = {
            "target": {
                "id": current["id"],
                "event": current["event"],
                "plugin_id": current["plugin_id"],
            },
            "action": action,
            "current": current,
            "proposed": proposed,
            "payload": payload,
        }
        if arguments.get("user_confirmed") is not True:
            current_text = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)
            proposed_text = json.dumps(proposed, ensure_ascii=False, indent=2, sort_keys=True)
            diff = "".join(difflib.unified_diff(
                current_text.splitlines(keepends=True),
                proposed_text.splitlines(keepends=True),
                fromfile="current-system-hook.json",
                tofile="proposed-system-hook.json",
            ))
            token = _issue_system_hook_confirmation(confirmation_value)
            return _confirmation_required(
                context,
                target=hook_id,
                kind="system_hook",
                preview={
                    "action": action,
                    "current": current,
                    "proposed": proposed,
                    "diff": diff,
                },
                confirmation_token=token,
            )
        if not _consume_system_hook_confirmation(
            arguments.get("confirmation_token"),
            confirmation_value,
        ):
            return _json({
                "ok": False,
                "code": "system_hook_confirmation_invalid",
                "error": plugin_localized(
                    context,
                    "The confirmation is missing, expired, or does not match this exact change.",
                    "确认令牌缺失、已过期，或与本次具体修改不一致。",
                ),
            })
        return _json(update_runtime_hook(host.db_path, hook_id, payload))
    if scope != "user":
        return _json({"ok": False, "error": "mutations require scope=user or scope=system"})
    if action in {"approve_proposal", "reject_proposal"}:
        if not callable(getattr(service, "decide_hook_proposal", None)):
            return _json({"ok": False, "error": "CLI Hook proposal service is unavailable"})
        proposal_id = str(arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return _json({"ok": False, "error": "proposal_id is required"})
        return _json(service.decide_hook_proposal(
            proposal_id,
            action == "approve_proposal",
        ))
    if action == "generate":
        if not callable(getattr(service, "request_hook_generation", None)):
            return _json({"ok": False, "error": "Hook generation service is unavailable"})
        return _json(service.request_hook_generation(mutation))
    if action == "regenerate":
        if not hook_id or not callable(getattr(service, "retry_hook_generation", None)):
            return _json({"ok": False, "error": "Hook regeneration service is unavailable"})
        return _json(service.retry_hook_generation(hook_id, mutation))
    if action == "create":
        if not mutation:
            return _json({"ok": False, "error": "hook is required for create"})
        if callable(getattr(service, "save_hook", None)):
            return _json(service.save_hook(mutation))
        return _json({"ok": True, "hook": public_hook(user_hooks.save(mutation, actor="agent"))})
    if action == "update":
        if not mutation:
            return _json({"ok": False, "error": "hook is required for update"})
        if not hook_id or user_hooks.get(hook_id) is None:
            return _json({"ok": False, "code": "user_hook_not_found"})
        existing = user_hooks.get(hook_id) or {}
        if existing.get("configured_by_agent") is True and (
            set(mutation) - {"timeout_seconds", "priority"}
        ):
            unsupported = set(mutation) - {
                "name", "event", "action_instruction", "description",
                "timeout_seconds", "priority",
            }
            if unsupported:
                return _json({
                    "ok": False,
                    "error": "Agent-generated Hooks must change behavior through action_instruction",
                    "unsupported": sorted(unsupported),
                })
            if not callable(getattr(service, "retry_hook_generation", None)):
                return _json({"ok": False, "error": "Hook regeneration service is unavailable"})
            return _json(service.retry_hook_generation(hook_id, mutation))
        if callable(getattr(service, "save_hook", None)):
            return _json(service.save_hook(mutation, hook_id=hook_id))
        return _json({"ok": True, "hook": public_hook(user_hooks.save({**mutation, "id": hook_id}, actor="agent"))})
    if action in {"enable", "disable"}:
        if not hook_id or user_hooks.get(hook_id) is None:
            return _json({"ok": False, "code": "user_hook_not_found"})
        enabled = action == "enable"
        if callable(getattr(service, "set_hook_enabled", None)):
            return _json(service.set_hook_enabled(hook_id, enabled))
        return _json({"ok": True, "hook": public_hook(
            user_hooks.set_enabled(hook_id, enabled, actor="agent")
        )})
    if action == "test":
        if not hook_id or not callable(getattr(service, "test_hook", None)):
            return _json({"ok": False, "error": "Hook test service is unavailable"})
        return _json(await service.test_hook(hook_id, mutation))
    if action == "delete":
        if callable(getattr(service, "delete_hook", None)):
            return _json(service.delete_hook(hook_id))
        return _json({"ok": user_hooks.delete(hook_id, actor="agent")})
    return _json({"ok": False, "error": "unsupported user Hook action"})


__all__ = [
    "AUTHORING_GUIDE",
    "AUTHORING_GUIDE_ZH",
    "SCAFFOLD_TYPES",
    "authoring_guide",
    "install",
    "manage_hooks",
    "manage_plugins",
    "manage_plugin_source",
    "reload_plugins",
    "scaffold",
    "validate",
    "validate_pack_directory",
    "validate_plugin_source",
    "validate_standalone_plugin",
]
