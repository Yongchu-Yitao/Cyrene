"""Code analysis tools — linting, formatting, and code review."""

import asyncio
import ast
import json
import logging

from agent.plugin import PluginContext
from agent.plugin.native_runtime import (
    plugin_localized,
    plugin_localized_plural,
    resolve_workspace_path,
)

logger = logging.getLogger(__name__)

# ── Ruff helpers ──

async def _run_ruff_check(path: str, context: PluginContext) -> list[dict]:
    """Run ruff check on a path and return structured results."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff", "check", "--output-format=json", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 and not stdout.strip():
            return []
        try:
            return json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[:500]
            return [{
                "error": plugin_localized(
                    context,
                    "Ruff returned output that could not be parsed.",
                    "Ruff 返回了无法解析的输出。",
                ),
                "detail": detail,
            }]
    except FileNotFoundError:
        return [{"error": plugin_localized(
            context,
            "Ruff is not installed or is unavailable.",
            "Ruff 未安装或当前不可用。",
        )}]
    except Exception:
        logger.warning("Ruff lint execution failed", exc_info=True)
        return [{"error": plugin_localized(
            context,
            "Ruff could not lint the requested path.",
            "Ruff 无法检查所请求的路径。",
        )}]


async def _run_ruff_format(
    path: str,
    context: PluginContext,
    check_only: bool = False,
) -> dict:
    """Run ruff format on a path."""
    args = ["ruff", "format"]
    if check_only:
        args.append("--check")
        args.append("--diff")
    args.append(path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_s = stdout.decode("utf-8", errors="replace")
        stderr_s = stderr.decode("utf-8", errors="replace")
        return {
            "changed": proc.returncode != 0 if check_only else False,
            "diff": stdout_s if check_only else "",
            "output": stdout_s if not check_only else "",
            "error": stderr_s if proc.returncode != 0 and not check_only else "",
        }
    except FileNotFoundError:
        return {"error": plugin_localized(
            context,
            "Ruff is not installed or is unavailable.",
            "Ruff 未安装或当前不可用。",
        )}
    except Exception:
        logger.warning("Ruff format execution failed", exc_info=True)
        return {"error": plugin_localized(
            context,
            "Ruff could not format the requested path.",
            "Ruff 无法格式化所请求的路径。",
        )}


# ── AST analysis ──

def analyze_structure(path: str, context: PluginContext) -> dict:
    """Analyze Python file structure: functions, classes, complexity, imports."""
    resolved = resolve_workspace_path(path, context)
    try:
        source = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"error": plugin_localized(
            context,
            "Cannot read file: {path}",
            "无法读取文件：{path}",
            path=path,
        )}

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        location = f"{path}:{e.lineno or '?'}:{e.offset or '?'}"
        return {"error": plugin_localized(
            context,
            "Syntax error in {location}.",
            "{location} 中存在语法错误。",
            location=location,
        )}

    functions = []
    classes = []
    imports = []
    total_lines = len(source.splitlines())

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_lines = node.end_lineno - node.lineno + 1 if node.end_lineno else 0
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "lines": func_lines,
                "args": len(node.args.args),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            cls_lines = node.end_lineno - node.lineno + 1 if node.end_lineno else 0
            methods = [
                n.name for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "lines": cls_lines,
                "methods": len(methods),
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    # Find long functions (> 50 lines)
    long_funcs = [f for f in functions if f["lines"] > 50]

    return {
        "file": path,
        "total_lines": total_lines,
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "function_count": len(functions),
        "class_count": len(classes),
        "long_functions": long_funcs,
    }


# ── Tool handlers ──

async def _tool_lint_code(
    args: dict,
    context: PluginContext,
) -> str:
    path = str(args.get("path", "."))
    try:
        resolved = resolve_workspace_path(path, context)
    except (RuntimeError, ValueError):
        return json.dumps({"error": plugin_localized(
            context,
            "The requested path is outside the active workspace.",
            "请求的路径不在当前工作区内。",
        )}, ensure_ascii=False)
    results = await _run_ruff_check(str(resolved), context)
    return json.dumps({"status": "ok", "file": str(resolved), "issues": results}, ensure_ascii=False)


async def _tool_format_code(
    args: dict,
    context: PluginContext,
) -> str:
    path = str(args.get("path", "."))
    check_only = bool(args.get("check_only", False))
    try:
        resolved = resolve_workspace_path(path, context)
    except (RuntimeError, ValueError):
        return json.dumps({"error": plugin_localized(
            context,
            "The requested path is outside the active workspace.",
            "请求的路径不在当前工作区内。",
        )}, ensure_ascii=False)
    result = await _run_ruff_format(
        str(resolved),
        context,
        check_only=check_only,
    )
    return json.dumps({"status": "ok", "file": str(resolved), **result}, ensure_ascii=False)


async def _tool_code_review(
    args: dict,
    context: PluginContext,
) -> str:
    path = str(args.get("path", "."))
    try:
        resolved = resolve_workspace_path(path, context)
    except (RuntimeError, ValueError):
        return json.dumps({"error": plugin_localized(
            context,
            "The requested path is outside the active workspace.",
            "请求的路径不在当前工作区内。",
        )}, ensure_ascii=False)

    # Run lint, format check, and structure analysis in parallel
    lint_task = _run_ruff_check(str(resolved), context)
    format_task = _run_ruff_format(str(resolved), context, check_only=True)

    lint_results, format_results = await asyncio.gather(lint_task, format_task)

    # Structure analysis (sync, but fast)
    structure = analyze_structure(str(resolved), context)

    suggestions = []
    if lint_results:
        suggestions.append(plugin_localized_plural(
            context,
            "Found {count} lint issue.",
            "Found {count} lint issues.",
            "发现 {count} 个代码检查问题。",
            count=len(lint_results),
        ))
    if format_results.get("changed"):
        suggestions.append(plugin_localized(
            context,
            "Code needs formatting (ruff format).",
            "代码需要格式化（ruff format）。",
        ))
    if structure.get("long_functions"):
        names = [f["name"] for f in structure["long_functions"]]
        suggestions.append(plugin_localized(
            context,
            "Long functions (>50 lines): {names}",
            "过长函数（超过 50 行）：{names}",
            names=", ".join(names),
        ))

    return json.dumps({
        "status": "ok",
        "file": str(resolved),
        "lint_issues": lint_results,
        "format_diff": format_results.get("diff", ""),
        "needs_formatting": format_results.get("changed", False),
        "structure": structure,
        "suggestions": suggestions,
    }, ensure_ascii=False)


# ── Tool definitions ──

LINT_CODE_DEF = {
    "type": "function",
    "function": {
        "name": "LintCode",
        "description": "Run the Ruff linter on a file or directory. Returns structured lint results with file, line number, error code, and message.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory path to lint (relative to workspace).",
                },
            },
            "required": ["path"],
        },
    },
}

FORMAT_CODE_DEF = {
    "type": "function",
    "function": {
        "name": "FormatCode",
        "description": "Run the Ruff formatter on a file or directory. Use check_only=True to see what would change without actually modifying files.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory path to format.",
                },
                "check_only": {
                    "type": "boolean",
                    "description": "If true, only check what would be formatted without making changes (default: false).",
                },
            },
            "required": ["path"],
        },
    },
}

CODE_REVIEW_DEF = {
    "type": "function",
    "function": {
        "name": "CodeReview",
        "description": "Perform a comprehensive code review: runs linter, format check, and structural analysis (functions, classes, complexity, imports). Returns a report with issues and suggestions.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to review.",
                },
            },
            "required": ["path"],
        },
    },
}

PLUGIN_DECLARATIONS = (
    (LINT_CODE_DEF, _tool_lint_code),
    (FORMAT_CODE_DEF, _tool_format_code),
    (CODE_REVIEW_DEF, _tool_code_review),
)

__all__ = ["PLUGIN_DECLARATIONS", "analyze_structure"]
