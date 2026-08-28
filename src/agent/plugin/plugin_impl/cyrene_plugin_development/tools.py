"""Authoring tools for the unified editable PluginPack framework."""

from __future__ import annotations

import ast
import html
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent.plugin import PluginContext, active_plugin_application_host
from agent.plugin.native_runtime import (
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


AUTHORING_GUIDE = """# Cyrene Plugin authoring contract

Cyrene has one Plugin framework. A user Plugin is either one Python file exposing
`plugin`, or one directory exposing `plugin_pack` from `__init__.py`. Packs may
contain model-visible tools, session setup, application routes/services/lifecycle,
and sandboxed Workbench views. Do not create plugin.json or use the deleted
cyrene.view/cyrene.projectTool protocol.

## Scaffold types

- `standalone_tool`: one directly registered tool `.py` file.
- `tool_pack`: a directory containing one or more ordinary tools.
- `model_plugin`: a configurable model Provider with discovery and completion.
- `context_plugin`: a SessionStart Hook that mounts context into the Agent tree.
- `application_plugin`: process-level routes, services, startup and shutdown.
- `ui_plugin`: an application contribution plus a sandboxed Workbench split view.
- `full_pack`: a composable example containing tool, model, application, and UI.

Every generated component includes English and Chinese metadata. `plugin_type` is
required by PluginScaffold. A standalone tool path must end in `.py`; every other
type uses a directory. `plugin_name` optionally controls the stable executable
identifier, while `name` is the localized display title.

## Workbench view contribution

Declare views and project-tool launchers in `PluginPack.metadata`:

```python
plugin_pack = PluginPack(
    id="example_dashboard",
    description="Example dashboard.",
    plugins=(),
    application_setup=setup_application,
    metadata={
        "frontend_views": ({
            "id": "main", "entry": "ui/index.html", "title": "Dashboard",
            "i18n": {"en": {"title": "Dashboard"}, "zh": {"title": "仪表盘"}},
        },),
        "project_tools": ({
            "id": "dashboard", "view": "main", "title": "Dashboard",
            "subtitle": "Plugin view", "icon_text": "◇",
            "i18n": {
                "en": {"title": "Dashboard", "subtitle": "Plugin view"},
                "zh": {"title": "仪表盘", "subtitle": "插件视图"},
            },
        },),
    },
)
```

Entries are relative to the pack directory. Cyrene serves only directories that
contain declared view entries. The iframe is sandboxed and automatically participates
in split, drag, restore, and detached-window behavior.

## View backend RPC

Register pack-scoped methods from `application_setup`:

```python
async def load(arguments, request_context):
    return {"ok": True, "project_id": request_context["project_id"]}

def setup_application(context):
    context.provide_frontend_method("dashboard.load", load)
```

The iframe sends `{source:'cyrene-plugin', type:'call', requestId, method, args}`
with `postMessage` and receives `cyrene-host` init/response messages. Application
contribution changes require a Cyrene restart; HTML/CSS/JS assets are read live.

Use PluginScaffold, edit with normal file tools, run PluginValidate, then
PluginInstall. PluginReload refreshes tool/model definitions and reports whether
application changes require restart.
"""

AUTHORING_GUIDE_ZH = """# Cyrene Plugin 开发约定

Cyrene 只使用一套 Plugin 框架：单个 Python 文件通过 `plugin` 导出插件，目录则从
`__init__.py` 通过 `plugin_pack` 导出 PluginPack。PluginPack 可以包含模型可见工具、
会话初始化、应用路由与服务、生命周期以及沙箱化的 Workbench 视图。不要创建
`plugin.json`，也不要使用已经移除的 cyrene.view/cyrene.projectTool 协议。

## 脚手架类型

- `standalone_tool`：一个直接注册的 `.py` 工具文件。
- `tool_pack`：包含一个或多个普通工具的目录。
- `model_plugin`：支持发现与补全的可配置模型 Provider。
- `context_plugin`：在 SessionStart Hook 中向 Agent 树挂载上下文。
- `application_plugin`：进程级路由、服务、启动与关闭逻辑。
- `ui_plugin`：应用贡献与沙箱化 Workbench 分栏视图。
- `full_pack`：组合工具、模型、应用和 UI 的完整示例。

生成的组件包含英文和中文元数据。PluginScaffold 必须提供 `plugin_type`；独立工具的
路径必须以 `.py` 结尾，其他类型使用目录。`plugin_name` 可指定稳定的可执行标识，
`name` 则是本地化显示名称。

Workbench 视图应在 `PluginPack.metadata` 中声明 `frontend_views` 和
`project_tools`，并为标题和副标题提供 `i18n.zh`。入口路径必须位于插件包目录内；
iframe 会在沙箱中运行，并自动参与分栏、拖动、恢复和独立窗口行为。应用初始化可通过
`provide_frontend_method` 注册包级 RPC 方法。应用贡献变更需要重启 Cyrene，HTML、
CSS 和 JavaScript 资源则会实时读取。

建议流程：先运行 PluginScaffold，使用普通文件工具编辑，再运行 PluginValidate，最后
运行 PluginInstall。PluginReload 会刷新工具与模型定义，并报告哪些应用变更需要重启。
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

from agent.plugin import Plugin, PluginContext


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

from agent.plugin import Plugin, PluginContext


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

from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginSetupContext


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
    imports = ["from agent.plugin import PluginPack"]
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
    return f'''"""PluginPack generated by Cyrene."""

{chr(10).join(imports)}


plugin_pack = PluginPack(
    id={pack_id!r},
    description={description!r},
    plugins=({plugins}),
    setup={setup},
    application_setup={application_setup},
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

from agent.plugin import PluginApplicationContext


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
    host = active_plugin_application_host()
    if host is None:
        return _json({"ok": False, "error": plugin_localized(
            context,
            "The Plugin application host is unavailable.",
            "Plugin 应用宿主当前不可用。",
        )})
    source_type = str(validation.get("source_type") or "")
    identity = str(validation.get("pack_id") or validation.get("plugin_name") or source.stem)
    target = host.plugin_directory / (identity if source_type == "pack" else source.name)
    replace = bool(arguments.get("replace"))
    if target.exists() and not replace:
        return _json({
            "ok": False,
            "error": plugin_localized(
                context,
                "Plugin source already exists; set replace=true to replace it.",
                "Plugin 源码已存在；如需替换，请设置 replace=true。",
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
    host = active_plugin_application_host()
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


__all__ = [
    "AUTHORING_GUIDE",
    "AUTHORING_GUIDE_ZH",
    "SCAFFOLD_TYPES",
    "authoring_guide",
    "install",
    "reload_plugins",
    "scaffold",
    "validate",
    "validate_pack_directory",
    "validate_plugin_source",
    "validate_standalone_plugin",
]
