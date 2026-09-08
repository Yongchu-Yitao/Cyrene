"""Static validation of editable plugin sources; does not import plugin code."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from cyrene.core.plugin.extensions import SETUP_SYNC_ERROR

LEGACY_PROTOCOL_ERROR = "Legacy plugin.json protocol is unsupported; migrate to Plugin / PluginPack using PluginAuthoringGuide."

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


def _setup_definition(module: Path, expression: ast.AST, trees: dict[Path, ast.AST],
                      seen: frozenset[tuple[Path, str]] = frozenset()) -> ast.AST | None:
    if not isinstance(expression, ast.Name) or module not in trees:
        return None
    name = expression.id
    key = (module, name)
    if key in seen:
        return None
    seen = seen | {key}
    for node in trees[module].body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                if (alias.asname or alias.name) != name:
                    continue
                base = module.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base.joinpath(*(node.module or "").split("."))
                target = target.with_suffix(".py") if node.module else target / "__init__.py"
                if target not in trees:
                    target = base.joinpath(*(node.module or "").split(".")) / "__init__.py"
                return _setup_definition(target, ast.Name(id=alias.name), trees, seen)
    return None


def _setup_errors(trees: dict[Path, ast.AST]) -> list[str]:
    errors = []
    for module, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callbacks = []
            if _constructor_name(node) == "PluginPack":
                callbacks = [kw.value for kw in node.keywords
                             if kw.arg in {"setup", "application_setup"}]
            elif _constructor_name(node) == "ExtensionContribution" and len(node.args) >= 2:
                point = node.args[0]
                if getattr(point, "id", getattr(point, "attr", "")) in {"APPLICATION_SETUP", "SESSION_SETUP"}:
                    callbacks = [node.args[1]]
            for callback in callbacks:
                if isinstance(_setup_definition(module, callback, trees), ast.AsyncFunctionDef):
                    errors.append(SETUP_SYNC_ERROR)
    return list(dict.fromkeys(errors))


def validate_pack_directory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    initializer = root / "__init__.py"
    if not initializer.is_file():
        return {"ok": False, "path": str(root), "errors": [LEGACY_PROTOCOL_ERROR if (root / "plugin.json").is_file() else "PluginPack directory requires __init__.py"], "warnings": []}
    trees: dict[Path, ast.AST] = {}
    for source in sorted(root.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        try:
            trees[source] = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{source.relative_to(root)}: {exc}")
    errors.extend(_setup_errors(trees))
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

