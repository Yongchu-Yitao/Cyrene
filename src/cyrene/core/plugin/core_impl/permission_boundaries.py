"""Deterministic permission boundaries owned by fixed filesystem Plugins.

These checks are the Plugin-kernel equivalent of the 0.7.13 tool-side guards:
ordinary workspace operations return ``None``; only an actual scope elevation
or irreversible operation returns a permission request for the review Plugin.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from ..plugin import PluginContext


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolved_path(raw_path: Any, context: PluginContext) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("path cannot be empty")
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if context.workspace is None:
        raise ValueError("a workspace is required for relative paths")
    return (Path(context.workspace).expanduser().resolve() / candidate).resolve()


def path_boundary(
    raw_path: Any,
    context: PluginContext,
    *,
    kind: str,
    operation: str,
) -> dict[str, Any] | None:
    path = resolved_path(raw_path, context)
    workspace = (
        Path(context.workspace).expanduser().resolve()
        if context.workspace is not None
        else None
    )
    if workspace is not None and _within(path, workspace):
        return None
    return {
        "kind": kind,
        "operation": operation,
        "path_hint": str(path),
        "reason": f"目标路径位于当前 workspace 之外：{path}",
        "scope_hint": "workspace 之外的 ",
        "requires_human": False,
    }


def _first_command(raw: str) -> str:
    try:
        first = shlex.split(str(raw or ""), posix=True)[0]
    except (IndexError, ValueError):
        first = str(raw or "").strip().split(" ", 1)[0]
    return Path(first.lstrip("\\")).name.lower().strip("'\"")


def _dangerous_subshell(command: str) -> bool:
    return bool(re.search(r"\$\(", command) or "`" in command)


def _is_safe_review_directory_refresh(
    command: str,
    context: PluginContext,
) -> bool:
    segments = re.split(r"\s*&&\s*", str(command or ""), maxsplit=2)
    if len(segments) < 2:
        return False
    try:
        delete_tokens = shlex.split(segments[0], posix=True)
        clone_tokens = shlex.split(segments[1], posix=True)
    except ValueError:
        return False
    if len(delete_tokens) != 3 or delete_tokens[:2] != ["rm", "-rf"]:
        return False
    if len(clone_tokens) < 4 or clone_tokens[:2] != ["git", "clone"]:
        return False
    target_text = delete_tokens[2]
    if clone_tokens[-1] != target_text:
        return False
    if not any(token.startswith("https://") for token in clone_tokens[2:-1]):
        return False
    if not target_text or any(token in target_text for token in ("*", "?", "[", "]", "$", "`")):
        return False
    workspace = (
        Path(context.workspace).expanduser().resolve()
        if context.workspace is not None
        else None
    )
    if workspace is None:
        return False
    target = Path(target_text)
    target = target if target.is_absolute() else workspace / target
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return False
    allowed_roots = [(workspace / ".cyrene" / "scratch").resolve()]
    try:
        from cyrene.runtime.paths import TEMP_DIR

        allowed_roots.append((TEMP_DIR / "reviews").resolve())
    except Exception:
        pass
    return (
        any(root in resolved.parents for root in allowed_roots)
        and resolved.name.casefold().endswith(("-review", "_review"))
        and not target.is_symlink()
    )


def _destructive_shell(
    command: str,
    context: PluginContext,
) -> dict[str, str] | None:
    raw = str(command or "").strip()
    lowered = raw.lower()
    first = _first_command(raw)
    if _is_safe_review_directory_refresh(raw, context):
        return None
    if first in {"rm", "rmdir", "unlink"} or re.search(
        r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:\\|/[\w./-]+/)?(?:rm|rmdir|unlink)\b",
        lowered,
    ) or re.search(r"(?:^|\s)(?:rm|rmdir|unlink)(?:\$IFS|\$\{IFS\})", raw):
        return {
            "operation": "文件删除操作",
            "kind": "file_delete",
            "detail": f"命令：{raw[:240]}",
        }
    if re.search(r"\bgit\s+reset\b[^\n;&|]*\s--hard\b", lowered):
        return {
            "operation": "Git 硬重置",
            "kind": "git_reset_hard",
            "detail": f"命令：{raw[:240]}",
        }
    if re.search(r"\bgit\s+clean\b[^\n;&|]*\s-[^\s;&|]*f", lowered):
        return {
            "operation": "Git 清理未跟踪文件",
            "kind": "git_clean_force",
            "detail": f"命令：{raw[:240]}",
        }
    if first in {"mkfs", "shred"} or re.search(
        r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:mkfs(?:\.[\w-]+)?|shred)\b",
        lowered,
    ):
        return {
            "operation": "磁盘/文件破坏性操作",
            "kind": "destructive_system_command",
            "detail": f"命令：{raw[:240]}",
        }
    if first == "dd" or re.search(r"(?:^|[;&|]\s*)(?:sudo\s+)?dd\b", lowered):
        return {
            "operation": "低级别写入操作",
            "kind": "dd_write",
            "detail": f"命令：{raw[:240]}",
        }
    if first == "truncate" or re.search(
        r"(?:^|[;&|]\s*)(?:sudo\s+)?truncate\b", lowered
    ):
        return {
            "operation": "截断文件操作",
            "kind": "file_truncate",
            "detail": f"命令：{raw[:240]}",
        }
    if re.search(
        r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:mv|cp|install)\b[^\n;&|]*(?:\s-f\b|\s--force\b)",
        lowered,
    ):
        return {
            "operation": "覆盖文件操作",
            "kind": "file_overwrite",
            "detail": f"命令：{raw[:240]}",
        }
    return None


def _requires_write_guard(command: str) -> bool:
    lowered = str(command or "").strip().lower()
    if any(token in lowered for token in (
        " rm ", "rm -", "mv ", "cp ", "mkdir ", "touch ", "tee ",
        "sed -i", "truncate ", "install ", "rmdir ", "unlink ", ">",
    )):
        return True
    if re.search(r"\b(?:rm|mv|cp|dd|tee)(?:\$IFS|\$\{IFS\})", lowered):
        return True
    if " ln -f " in f" {lowered} " or " ln --force " in f" {lowered} ":
        return True
    return _first_command(lowered) in {
        "rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install",
        "rmdir", "unlink", "dd", "sed", "ln",
    }


def _write_targets(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError("Shell write command could not be parsed safely") from exc
    write_commands = {
        "rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install",
        "rmdir", "unlink", "dd", "ln",
    }
    directory_commands = {"cd", "pushd"}
    separators = {"&&", "||", "|", ";"}
    active = ""
    candidates: list[str] = []
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        normalized = _first_command(value)
        if value in separators:
            active = ""
            continue
        if normalized in write_commands or normalized in directory_commands:
            active = normalized
            continue
        if value.startswith((">", ">>")):
            target = value.lstrip(">").strip()
            if target:
                candidates.append(target)
            active = ""
            continue
        if active and not value.startswith("-"):
            if active in {"cp", "mv"} or active in directory_commands or (
                value.startswith(("/", "./", "../"))
                or "/" in value
                or re.search(r"\.[A-Za-z0-9]{1,8}$", value)
            ):
                candidates.append(value)
    candidates.extend(
        match.group(2)
        for match in re.finditer(r"(?:^|\s)(\d*)>>?\s*([^\s;&|]+)", command)
        if not match.group(2).startswith("&")
    )
    candidates.extend(
        match.group(1)
        for match in re.finditer(r"(?:^|\s)&\s*>\s*([^\s;&|]+)", command)
    )
    return candidates


def bash_boundary(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    command = str(arguments.get("command") or "").strip()
    if _dangerous_subshell(command):
        return {
            "kind": "subshell_elevation",
            "operation": "包含命令替换的 Shell 操作",
            "reason": f"命令包含 $() 或反引号，其展开路径无法静态验证。\n命令：{command[:240]}",
            "requires_human": False,
        }
    destructive = _destructive_shell(command, context)
    if destructive is not None:
        return {
            "kind": "destructive_confirmation",
            "operation": destructive["operation"],
            "reason": destructive["detail"],
            "requires_human": True,
            "single_use": False,
        }
    if not _requires_write_guard(command):
        return None
    workspace = (
        Path(context.workspace).expanduser().resolve()
        if context.workspace is not None
        else None
    )
    if workspace is None:
        return {
            "kind": "write_permission_request",
            "operation": "Shell 写入/删除操作",
            "reason": command[:240],
            "requires_human": False,
        }
    try:
        targets = _write_targets(command)
    except ValueError:
        targets = ["<无法静态解析>"]
    outside: list[str] = []
    for token in targets:
        expanded = os.path.expandvars(os.path.expanduser(token.strip("'\"")))
        if expanded.rstrip("/") == os.devnull:
            continue
        candidate = Path(expanded)
        resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
        if not _within(resolved, workspace):
            outside.append(token)
    if not outside:
        return None
    return {
        "kind": "write_permission_request",
        "operation": "Shell 写入/删除操作",
        "path_hint": ", ".join(outside[:5]),
        "reason": command[:240],
        "requires_human": False,
    }


__all__ = ["bash_boundary", "path_boundary", "resolved_path"]
