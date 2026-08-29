"""Git integration tools — status, diff, log, commit, branch."""

import asyncio
import json
import logging

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    plugin_localized,
    resolve_workspace_path,
    workspace_root,
)

logger = logging.getLogger(__name__)


async def _run_git(
    args: list[str],
    context: PluginContext,
    timeout: float = 30.0,
) -> dict:
    """Run a git command and return {stdout, stderr, returncode}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(workspace_root(context)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        result = {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "returncode": proc.returncode or 0,
        }
        if result["returncode"]:
            result["error"] = (
                str(
                    result["stderr"]
                    or result["stdout"]
                    or plugin_localized(
                        context,
                        "The Git command failed.",
                        "Git 命令执行失败。",
                    )
                )
                .strip()
            )
        return result
    except asyncio.TimeoutError:
        return {"error": plugin_localized(
            context,
            "The Git command timed out.",
            "Git 命令执行超时。",
        )}
    except FileNotFoundError:
        return {"error": plugin_localized(
            context,
            "Git is not installed or is unavailable.",
            "Git 未安装或当前不可用。",
        )}
    except Exception:
        logger.warning("Git command execution failed", exc_info=True)
        return {"error": plugin_localized(
            context,
            "The Git command could not be completed.",
            "无法完成 Git 命令。",
        )}


def _parse_status(porcelain: str) -> list[dict]:
    """Parse git status --porcelain output.

    Returns entries with the full XY status code, e.g. "M " (staged modify),
    " M" (unstaged modify), "??" (untracked), "MM" (both staged and unstaged).
    """
    results = []
    for line in porcelain.strip().split("\n"):
        if not line.strip():
            continue
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:].strip()
        # Handle rename: "R  old -> new"
        if " -> " in path:
            parts = path.split(" -> ")
            path = parts[-1]
        staged = xy[0] != " "
        results.append({"path": path, "status": xy, "staged": staged})
    return results


def _parse_log(oneline: str) -> list[dict]:
    """Parse git log --oneline output."""
    results = []
    for line in oneline.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        results.append({
            "hash": parts[0],
            "message": parts[1] if len(parts) > 1 else "",
        })
    return results


# ── Tool handlers ──

async def _tool_git_status(args: dict, context: PluginContext) -> str:
    result = await _run_git(["status", "--porcelain"], context)
    if result.get("error"):
        return json.dumps({"error": result["error"]}, ensure_ascii=False)
    files = _parse_status(result["stdout"])
    return json.dumps({
        "status": "ok",
        "files": files,
        "changed_count": len(files),
        "is_clean": len(files) == 0,
    }, ensure_ascii=False)


async def _tool_git_diff(args: dict, context: PluginContext) -> str:
    cmd = ["diff"]
    if args.get("staged"):
        cmd.append("--staged")
    path = args.get("path", "")
    if path:
        cmd.append("--")
        try:
            resolved = resolve_workspace_path(str(path), context)
        except (RuntimeError, ValueError):
            return json.dumps({"error": plugin_localized(
                context,
                "The requested path is outside the active workspace.",
                "请求的路径不在当前工作区内。",
            )}, ensure_ascii=False)
        cmd.append(str(resolved.relative_to(workspace_root(context))))
    result = await _run_git(cmd, context, timeout=60.0)
    if result.get("error"):
        return json.dumps({"error": result["error"]}, ensure_ascii=False)
    diff_text = result["stdout"]
    return json.dumps({
        "status": "ok",
        "diff": diff_text,
        "has_changes": bool(diff_text.strip()),
    }, ensure_ascii=False)


async def _tool_git_log(args: dict, context: PluginContext) -> str:
    try:
        count = int(args.get("count", 10))
    except (TypeError, ValueError):
        return json.dumps({"error": plugin_localized(
            context,
            "count must be an integer.",
            "count 必须是整数。",
        )}, ensure_ascii=False)
    if count < 1:
        return json.dumps({"error": plugin_localized(
            context,
            "count must be greater than zero.",
            "count 必须大于零。",
        )}, ensure_ascii=False)
    result = await _run_git(["log", "--oneline", f"-n{count}"], context)
    if result.get("error"):
        return json.dumps({"error": result["error"]}, ensure_ascii=False)
    commits = _parse_log(result["stdout"])
    return json.dumps({
        "status": "ok",
        "commits": commits,
        "count": len(commits),
    }, ensure_ascii=False)


async def _tool_git_commit(args: dict, context: PluginContext) -> str:
    """Stage and commit changes after the central PreToolUse review."""
    message = str(args.get("message", "")).strip()
    files = args.get("files", [])

    if not message:
        return json.dumps({"error": plugin_localized(
            context,
            "Commit message is required.",
            "必须提供提交信息。",
        )}, ensure_ascii=False)

    # Stage files first (if specific files given, add only those)
    if files:
        try:
            root = workspace_root(context)
            pathspecs = [
                str(resolve_workspace_path(str(path), context).relative_to(root))
                for path in files
            ]
        except (RuntimeError, ValueError):
            return json.dumps({"error": plugin_localized(
                context,
                "One or more requested files are outside the active workspace.",
                "一个或多个请求的文件不在当前工作区内。",
            )}, ensure_ascii=False)
        add_result = await _run_git(["add", "--", *pathspecs], context)
    else:
        add_result = await _run_git(["add", "-A"], context)
    if add_result.get("error"):
        return json.dumps({"error": plugin_localized(
            context,
            "Failed to stage files: {error}",
            "暂存文件失败：{error}",
            error=add_result["error"],
        )}, ensure_ascii=False)

    cmd = ["commit", "-m", message]
    result = await _run_git(cmd, context)
    if result.get("error"):
        return json.dumps({"error": result["error"]}, ensure_ascii=False)
    return json.dumps({
        "status": "ok",
        "output": result["stdout"] or result["stderr"],
    }, ensure_ascii=False)


async def _tool_git_branch(args: dict, context: PluginContext) -> str:
    new_branch = args.get("create", "")
    if new_branch:
        result = await _run_git(["branch", new_branch], context)
        if result.get("error"):
            return json.dumps({"error": result["error"]}, ensure_ascii=False)
        return json.dumps({
            "status": "ok",
            "created": new_branch,
            "output": result["stdout"] or result["stderr"],
        }, ensure_ascii=False)

    # List branches
    result = await _run_git(["branch"], context)
    if result.get("error"):
        return json.dumps({"error": result["error"]}, ensure_ascii=False)
    branches = []
    for line in result["stdout"].strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        current = line.startswith("*")
        name = line.lstrip("* ").strip()
        branches.append({"name": name, "current": current})
    return json.dumps({
        "status": "ok",
        "branches": branches,
    }, ensure_ascii=False)


# ── Tool definitions ──

GIT_STATUS_DEF = {
    "type": "function",
    "function": {
        "name": "GitStatus",
        "description": "Show the working tree status. Returns a list of changed files with their status (M=modified, A=added, D=deleted, ??=untracked) and whether each is staged.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

GIT_DIFF_DEF = {
    "type": "function",
    "function": {
        "name": "GitDiff",
        "description": "Show changes in the working tree. Use staged=True to see staged changes. Use path to limit to a specific file.",
        "parameters": {
            "type": "object",
            "properties": {
                "staged": {"type": "boolean", "description": "Show staged changes instead of working tree changes."},
                "path": {"type": "string", "description": "Limit diff to a specific file path."},
            },
            "required": [],
        },
    },
}

GIT_LOG_DEF = {
    "type": "function",
    "function": {
        "name": "GitLog",
        "description": "Show recent commit history. Returns commit hashes and messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of commits to show (default: 10)."},
            },
            "required": [],
        },
    },
}

GIT_COMMIT_DEF = {
    "type": "function",
    "function": {
        "name": "GitCommit",
        "description": "Stage and commit changes. Requires user confirmation before committing. Use this to save work with a descriptive message.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of specific files to commit (stages all changes if omitted).",
                },
            },
            "required": ["message"],
        },
    },
}

GIT_BRANCH_DEF = {
    "type": "function",
    "function": {
        "name": "GitBranch",
        "description": "List local branches or create a new one. Pass create='name' to create a new branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "create": {"type": "string", "description": "Name of a new branch to create. Omit to list existing branches."},
            },
            "required": [],
        },
    },
}

PLUGIN_DECLARATIONS = (
    (GIT_STATUS_DEF, _tool_git_status),
    (GIT_DIFF_DEF, _tool_git_diff),
    (GIT_LOG_DEF, _tool_git_log),
    (GIT_COMMIT_DEF, _tool_git_commit),
    (GIT_BRANCH_DEF, _tool_git_branch),
)

__all__ = ["PLUGIN_DECLARATIONS"]
