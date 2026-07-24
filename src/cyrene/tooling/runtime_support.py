"""Shared runtime helpers for native tool implementations.

This module owns path resolution, shell guards, and approval requests. It has no
model-facing tool catalog and contains no tool handlers.
"""

import asyncio
import json
import logging
import os
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cyrene.attachments import (
    analyze_attachment,
    build_public_attachment_payload,
    is_exported_attachment_path,
    is_uploaded_attachment_path,
    register_generated_attachment,
)
from cyrene import db
from cyrene.config import (
    DATA_DIR,
    STATE_FILE,
    WORKSPACE_DIR,
)
from cyrene.llm import _truncate
from cyrene.schedule_spec import compute_next_run
from cyrene.search import deep_search
from cyrene.shells import close_shell as _close_shell_session
from cyrene.shells import list_shells as _list_shell_sessions
from cyrene.shells import send_shell as _send_shell_session
from cyrene.shells import start_shell as _start_shell_session
from cyrene.skills_registry import (
    build_skills as _build_skills,
    install_skill_from_path as _install_skill,
    uninstall_skill as _uninstall_skill,
)
from cyrene.subagent import register as _reg_subagent, can_receive, _run_subagent, _spawn_subagent_task
from cyrene.inbox import send_message as _send_inbox
from cyrene.workbench_context import resolve_project_data_key_for_session

logger = logging.getLogger(__name__)
_CC_PROJECT_DIR = WORKSPACE_DIR.parent
__all__ = [
    "DATA_DIR",
    "STATE_FILE",
    "WORKSPACE_DIR",
    "_CC_PROJECT_DIR",
    "_build_skills",
    "_classify_destructive_shell_command",
    "_close_shell_session",
    "_command_is_file_deletion",
    "_destructive_operation_fingerprint",
    "_guard_nonbash_shell_command",
    "_guard_shell_command_workspace_write",
    "_install_skill",
    "_is_dangerous_subshell",
    "_json_result",
    "_list_shell_sessions",
    "_reg_subagent",
    "_request_delete_confirmation",
    "_request_destructive_confirmation",
    "_request_external_delivery_confirmation",
    "_request_external_upload_confirmation",
    "_request_read_elevation",
    "_request_scope_elevation",
    "_request_write_elevation",
    "_resolve_exportable_path",
    "_resolve_tool_path",
    "_resolve_workspace_path",
    "_resolve_workspace_write_target",
    "_run_subagent",
    "_send_inbox",
    "_send_shell_session",
    "_shell_command_requires_write_guard",
    "_spawn_subagent_task",
    "_start_shell_session",
    "_truncate",
    "_uninstall_skill",
    "analyze_attachment",
    "asyncio",
    "build_public_attachment_payload",
    "can_receive",
    "compute_next_run",
    "datetime",
    "db",
    "deep_search",
    "httpx",
    "json",
    "logger",
    "register_generated_attachment",
    "resolve_project_data_key_for_session",
    "time",
    "timezone",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_workspace_path(path_str: str) -> Path:
    from cyrene.agent.state import active_workspace_dir
    workspace = active_workspace_dir()
    candidate = Path(path_str)
    path = candidate if candidate.is_absolute() else workspace / candidate
    resolved = path.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError(
            f"⛔ 已禁止：路径超出 workspace 范围。\n"
            f"  请求路径：{path_str}\n"
            f"  完整路径：{resolved}\n"
            f"  Workspace：{workspace}\n"
            f"  Agent 没有访问此路径的权限。"
        )
    return resolved


def _workspace_permission_error() -> str:
    return "Write and delete permissions are limited to the current workspace."


def _has_full_path_access() -> bool:
    from cyrene.agent.state import _permission_mode, _temporary_full_access
    from cyrene.settings_store import get_write_permission_mode

    return (
        _permission_mode.get() == "full_access"
        or _temporary_full_access.get()
        or get_write_permission_mode() == "full_access"
    )


def _resolve_workspace_write_target(path_str: str) -> Path:
    from cyrene.agent.state import active_workspace_dir
    if _has_full_path_access():
        candidate = Path(path_str)
        path = candidate if candidate.is_absolute() else active_workspace_dir() / candidate
        return path.resolve()
    try:
        return _resolve_workspace_path(path_str)
    except Exception as exc:
        raise ValueError(_workspace_permission_error()) from exc


async def _request_scope_elevation(
    *,
    tool_name: str,
    path_hint: str,
    operation: str,
    reason: str = "",
    permission_kind: str = "scope_elevation",
    options: list[str] | None = None,
    scope_hint: str = "workspace 之外的 ",
    meta_extra: dict[str, Any] | None = None,
) -> str | None:
    """Resolve a permission boundary according to the active permission mode.

    Returns ``None`` when the operation is **allowed** (caller should proceed),
    or a ``str`` otherwise:

    - ``default`` mode → creates a pending question and returns the
      ``awaiting_user`` JSON; the agent loop pauses until the user answers.
    - ``auto`` mode → a review agent decides autonomously. Approve → sets
      temporary full access and returns ``None``; deny → returns a denial
      message string for the agent to see.
    - ``full_access`` mode → returns ``None`` (normally short-circuited earlier).

    Args:
        tool_name: The tool being used (e.g. "Read", "Write").
        path_hint: The target path the agent wants to access.
        operation: Human-readable description of the operation.
        reason: Why the agent needs to access this path.
        permission_kind: Meta field to identify the permission type.
        options: Custom options for the question UI.
    """
    import cyrene.agent.state as _state
    from cyrene.agent.state import (
        _current_agent_id,
        _current_client_request_id,
        _current_command,
        _current_round_id,
        _publish_runtime_event,
    )
    from cyrene.agent.session import (
        _upsert_pending_question,
        get_session_labels,
    )
    if _current_agent_id.get() != "main":
        return (
            f"⛔ 已禁止：{operation} 超出 workspace 范围。\n"
            f"Subagent 无权申请权限提升，请向主 agent 报告。"
        )
    round_id = str(_current_round_id.get() or "").strip()
    if not round_id:
        return (
            f"⛔ 已禁止：{operation} 超出 workspace 范围。\n"
            f"当前不在活动对话轮次中，无法申请权限。"
        )

    mode = _state._permission_mode.get()
    # 破坏性/不可逆操作必须由真人确认，不能被 full_access 或 auto mode 短路。
    requires_human_confirmation = permission_kind in {
        "destructive_confirmation",
        "external_upload_confirmation",
    }
    # 完全访问模式：工具层通常已用 _temporary_full_access 短路，这里保险直接放行。
    if mode == "full_access" and not requires_human_confirmation:
        return None
    # 自动模式：审核 agent 自主裁决，从不打扰用户。
    if mode == "auto" and not requires_human_confirmation:
        from cyrene.agent.auto_review import review_elevation
        approved, rationale = await review_elevation(
            tool_name=tool_name,
            operation=operation,
            path_hint=path_hint,
            reason=reason,
        )
        await _publish_runtime_event({
            "type": "auto_review",
            "approved": approved,
            "operation": operation,
            "tool_name": tool_name,
            "path_hint": path_hint,
            "rationale": rationale,
            "round_id": round_id,
        })
        if approved:
            _state._temporary_full_access.set(True)
            return None
        return (
            f"⛔ 审核 agent 拒绝了此操作（{operation}）。\n"
            f"原因：{rationale}\n"
            f"请改用更安全的方式（如限制在 workspace 内、避免破坏性命令），或向用户说明此操作的必要性。"
        )

    # 默认模式（计划模式同意后也已回退为 default）：弹出提问让用户授权。
    labels = get_session_labels(round_id)
    detail = f"\n📂 目标路径：{path_hint}" if path_hint else ""
    why = f"\n💡 原因：{reason}" if reason else ""
    effective_options = options or ["允许这次", "拒绝"]
    meta = {
        "kind": permission_kind,
        "tool_name": tool_name,
        "path_hint": path_hint,
        "reason": reason,
        "operation": operation,
        "command": _current_command.get() or "",
    }
    if meta_extra:
        meta.update(meta_extra)
    question = await _upsert_pending_question({
        "text": (
            f"⚠️ Agent 尝试执行 {scope_hint}{operation}\n\n"
            f"工具：{tool_name}{detail}{why}\n\n"
            f"请确认是否允许此操作。如果不允许，Agent 将仅能在当前 workspace 内工作。"
        ),
        "round_id": round_id,
        "round_title": labels.get("round_title", ""),
        "client_request_id": str(_current_client_request_id.get() or "").strip(),
        "options": effective_options,
        "allow_custom": True,
        "meta": meta,
    })
    return _json_result({
        "status": "awaiting_user",
        "question_id": question.get("id", ""),
        "permission": permission_kind,
        "tool": tool_name,
        "path": path_hint,
        "operation": operation,
    })


async def _request_write_elevation(
    *,
    tool_name: str,
    path_hint: str,
    reason: str = "",
) -> str | None:
    return await _request_scope_elevation(
        tool_name=tool_name,
        path_hint=path_hint,
        operation="写入/删除操作",
        reason=reason,
        permission_kind="write_permission_request",
        options=["仅这次允许", "始终允许", "保持仅限 workspace"],
    )


async def _request_read_elevation(
    *,
    tool_name: str,
    path_hint: str,
    reason: str = "",
) -> str | None:
    return await _request_scope_elevation(
        tool_name=tool_name,
        path_hint=path_hint,
        operation="读取操作",
        reason=reason,
        permission_kind="read_elevation",
        options=["允许这次读取", "拒绝"],
    )


def _command_is_file_deletion(command: str) -> bool:
    """Check if a shell command includes file deletion operations."""
    raw = str(command or "").strip()
    if not raw:
        return False
    # Extract the first command word (handles /bin/rm, 'rm', \rm, etc.)
    first = _extract_first_command(raw)
    if first in ("rm", "rmdir"):
        return True
    # Also detect rm$IFS and rm${IFS} (word splitting tricks)
    if re.search(r'(?:^|\s)(?:rm|rmdir)\$IFS', raw):
        return True
    if re.search(r'(?:^|\s)(?:rm|rmdir)\$\{IFS\}', raw):
        return True
    return False


async def _request_delete_confirmation(
    *,
    tool_name: str,
    command: str,
) -> str | None:
    """Request user confirmation before a destructive file operation in the workspace."""
    cmd_preview = command[:240]
    return await _request_destructive_confirmation(
        tool_name=tool_name,
        operation="文件删除操作",
        detail=f"Agent 尝试删除文件。\n命令：{cmd_preview}",
        destructive_kind="file_delete",
    )


def _destructive_operation_fingerprint(
    *,
    tool_name: str,
    operation: str,
    detail: str = "",
    path_hint: str = "",
    destructive_kind: str = "",
) -> str:
    payload = {
        "tool": str(tool_name or "").strip(),
        "operation": str(operation or "").strip(),
        "detail": str(detail or "").strip()[:500],
        "path": str(path_hint or "").strip(),
        "kind": str(destructive_kind or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def _request_destructive_confirmation(
    *,
    tool_name: str,
    operation: str,
    detail: str = "",
    path_hint: str = "",
    destructive_kind: str = "destructive_operation",
    risk_level: str = "high",
) -> str | None:
    """Require human confirmation before irreversible/destructive side effects."""
    from cyrene.agent import state as _state

    fingerprint = _destructive_operation_fingerprint(
        tool_name=tool_name,
        operation=operation,
        detail=detail,
        path_hint=path_hint,
        destructive_kind=destructive_kind,
    )
    if _state._destructive_confirmation_allow_all.get():
        return None
    if fingerprint in _state._destructive_confirmation_fingerprints.get():
        return None

    await _state._publish_runtime_event({
        "type": "destructive_confirmation",
        "decision": "requested",
        "tool_name": tool_name,
        "operation": operation,
        "destructive_kind": destructive_kind,
        "risk_level": risk_level,
        "path_hint": path_hint,
        "fingerprint": fingerprint,
    })
    return await _request_scope_elevation(
        tool_name=tool_name,
        path_hint=path_hint,
        operation=operation,
        reason=detail,
        permission_kind="destructive_confirmation",
        options=["允许这次", "本次会话内总是允许", "拒绝"],
        scope_hint="破坏性/不可逆的 ",
        meta_extra={
            "fingerprint": fingerprint,
            "destructive_kind": destructive_kind,
            "risk_level": risk_level,
        },
    )


async def _request_external_delivery_confirmation(
    *,
    tool_name: str,
    operation: str,
    detail: str = "",
    path_hint: str = "",
) -> str | None:
    """Ask before external delivery in default mode; auto/full-access may allow it.

    Sending a file outside Cyrene is an irreversible side effect, but it is not
    a destructive filesystem operation. Full-access mode promises not to
    interrupt, and auto mode should let the review agent decide.
    """
    from cyrene.agent import state as _state

    if _state._permission_mode.get() == "full_access" or _state._temporary_full_access.get():
        return None
    return await _request_scope_elevation(
        tool_name=tool_name,
        path_hint=path_hint,
        operation=operation,
        reason=detail,
        permission_kind="external_delivery_request",
        options=["允许这次", "本次会话内总是允许", "拒绝"],
        scope_hint="外部通信/文件外发的 ",
    )


async def _request_external_upload_confirmation(
    *,
    fingerprint: str,
    target: dict[str, Any],
    files: list[dict[str, Any]],
    reason: str = "",
) -> str | None:
    """Require a human, single-use approval before exposing files to a website."""
    from cyrene.agent import state as _state

    normalized_fingerprint = str(fingerprint or "").strip()
    if normalized_fingerprint in _state._external_upload_confirmation_fingerprints.get():
        return None

    safe_target = {
        "id": str(target.get("id") or ""),
        "tab_id": str(target.get("tabId") or ""),
        "origin": str(target.get("origin") or ""),
        "top_url": str(target.get("topUrl") or "")[:1000],
        "frame_url": str(target.get("frameUrl") or "")[:1000],
        "accept": str(target.get("accept") or "")[:240],
        "multiple": bool(target.get("multiple")),
    }
    safe_files = [
        {
            "name": str(item.get("name") or "")[:240],
            "size": int(item.get("size") or 0),
            "sha256": str(item.get("sha256") or "")[:64],
            "content_type": str(item.get("content_type") or "application/octet-stream")[:160],
        }
        for item in files
    ]
    await _state._publish_runtime_event({
        "type": "external_upload_confirmation",
        "decision": "requested",
        "tool_name": "browser_upload_files",
        "fingerprint": normalized_fingerprint,
        "target": safe_target,
        "files": safe_files,
    })
    file_lines = "\n".join(
        f"- {item['name']} ({item['size']} bytes, {item['content_type']}, SHA-256 {item['sha256']})"
        for item in safe_files
    )
    detail = (
        f"接收站点：{safe_target['origin'] or safe_target['frame_url']}\n"
        f"页面：{safe_target['top_url']}\n"
        f"文件输入限制：accept={safe_target['accept'] or '(未声明)'}, multiple={safe_target['multiple']}\n"
        f"文件：\n{file_lines}\n"
        "注意：设置文件后，网页可能立即开始上传，无需再次点击提交。"
    )
    if reason:
        detail += f"\nAgent 说明：{str(reason)[:500]}"
    return await _request_scope_elevation(
        tool_name="browser_upload_files",
        path_hint=", ".join(item["name"] for item in safe_files),
        operation=f"向 {safe_target['origin'] or '外部网页'} 上传本地文件",
        reason=detail,
        permission_kind="external_upload_confirmation",
        options=["允许这次上传", "拒绝"],
        scope_hint="本地数据外发的 ",
        meta_extra={
            "fingerprint": normalized_fingerprint,
            "target": safe_target,
            "files": safe_files,
        },
    )


def _classify_destructive_shell_command(command: str) -> dict[str, str] | None:
    """Best-effort shell destructive-operation classifier."""
    raw = str(command or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    first = _extract_first_command(raw)
    if first in {"rm", "rmdir", "unlink"} or re.search(r'(?:^|[;&|]\s*)(?:sudo\s+)?(?:\\|/[\w./-]+/)?(?:rm|rmdir|unlink)\b', lowered):
        return {"operation": "文件删除操作", "kind": "file_delete", "detail": f"命令：{raw[:240]}"}
    if re.search(r'\bgit\s+reset\b[^\n;&|]*\s--hard\b', lowered):
        return {"operation": "Git 硬重置", "kind": "git_reset_hard", "detail": f"命令：{raw[:240]}"}
    if re.search(r'\bgit\s+clean\b[^\n;&|]*\s-[^\s;&|]*f', lowered):
        return {"operation": "Git 清理未跟踪文件", "kind": "git_clean_force", "detail": f"命令：{raw[:240]}"}
    if first in {"mkfs", "shred"} or re.search(r'(?:^|[;&|]\s*)(?:sudo\s+)?(?:mkfs(?:\.[\w-]+)?|shred)\b', lowered):
        return {"operation": "磁盘/文件破坏性操作", "kind": "destructive_system_command", "detail": f"命令：{raw[:240]}"}
    if first == "dd" or re.search(r'(?:^|[;&|]\s*)(?:sudo\s+)?dd\b', lowered):
        return {"operation": "低级别写入操作", "kind": "dd_write", "detail": f"命令：{raw[:240]}"}
    if first == "truncate" or re.search(r'(?:^|[;&|]\s*)(?:sudo\s+)?truncate\b', lowered):
        return {"operation": "截断文件操作", "kind": "file_truncate", "detail": f"命令：{raw[:240]}"}
    if re.search(r'(?:^|[;&|]\s*)(?:sudo\s+)?(?:mv|cp|install)\b[^\n;&|]*(?:\s-f\b|\s--force\b)', lowered):
        return {"operation": "覆盖文件操作", "kind": "file_overwrite", "detail": f"命令：{raw[:240]}"}
    return None


def _extract_first_command(raw: str) -> str:
    """Extract the first command word, stripping quotes and path prefixes.

    Handles: rm, /bin/rm, 'rm', "rm", \rm, 'rm' -rf, etc.
    """
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        first = shlex.split(raw, posix=True)[0]
    except Exception:
        first = raw.split()[0] if raw.split() else ""
    # Strip leading path: /bin/rm → rm
    first = re.sub(r'^.*/', '', first)
    # Strip leading backslash or quotes
    first = first.lstrip("\\").lstrip("'").lstrip('"').rstrip("'").rstrip('"')
    return first.lower()


def _shell_command_requires_write_guard(command: str) -> bool:
    raw = str(command or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    # Quick substring check first (fast path)
    if any(token in lowered for token in (
        " rm ", "rm -", "mv ", "cp ", "mkdir ", "touch ", "tee ",
        "sed -i", "truncate ", "install ", "rmdir ", "unlink ", ">",
    )):
        return True
    # Check normalized first command word
    first = _extract_first_command(raw)
    WRITE_COMMANDS = {"rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install", "rmdir", "unlink", "dd", "sed", "ln"}
    if first in WRITE_COMMANDS:
        return True
    # Check for IFS variants: dd$IFS, rm$IFS, etc.
    if re.search(r'\b(?:rm|mv|cp|dd|tee)\$IFS\b', lowered):
        return True
    if re.search(r'\b(?:rm|mv|cp|dd|tee)\$\{IFS\}', lowered):
        return True
    # Check for ln -f / ln --force
    if " ln -f " in lowered or " ln --force " in lowered:
        return True
    return False


def _is_dangerous_subshell(command: str) -> bool:
    """Shell 命令替换 ($(...) 或反引号) 的路径无法静态预测，必须拦截并询问用户。"""
    raw = str(command or "").strip()
    if re.search(r'\$\(', raw):
        return True
    if '`' in raw:
        return True
    return False


def _check_command_substitution(command: str) -> None:
    """Raise ValueError with clear message if command contains unpredictable shell substitution."""
    if _is_dangerous_subshell(command):
        raise ValueError(
            f"⛔ 已禁止：Shell 命令包含命令替换 ($(...) 或反引号)。\n"
            f"  命令：{command[:240]}\n"
            f"  命令替换的路径无法提前验证，请使用明确的路径。"
        )


# Commands/cmdlets that write or delete under PowerShell or cmd. The workspace
# write/delete guards assume POSIX syntax and command names, so under a non-bash
# shell they cannot reason about a command — we fail closed instead. This list is
# deliberately over-broad: a false positive only costs a clear "install bash"
# message, whereas a false negative is a workspace sandbox escape.
_NONBASH_WRITE_TOKENS = (
    # PowerShell cmdlets
    "remove-item", "set-content", "add-content", "out-file", "new-item",
    "move-item", "copy-item", "clear-content", "set-itemproperty", "rename-item",
    "export-csv", "export-clixml", "tee-object",
    # cmd builtins / Windows utilities
    "del", "erase", "copy", "move", "ren", "rename", "md", "mkdir",
    "rd", "rmdir", "xcopy", "robocopy", "fsutil", "takeown", "icacls", "attrib",
    # POSIX names (also aliased inside PowerShell, and valid in Git-less setups)
    "rm", "mv", "cp", "tee", "touch", "dd", "truncate", "ln",
)


def _nonbash_command_writes(command: str) -> bool:
    """Heuristically detect a write/delete command under a non-POSIX shell."""
    raw = str(command or "")
    if not raw.strip():
        return False
    lowered = raw.lower()
    for token in _NONBASH_WRITE_TOKENS:
        if re.search(r'(?:^|[\s;&|(])' + re.escape(token) + r'(?=[\s;&|)]|$)', lowered):
            return True
    # File redirect (> / >>) writes a file in every shell. Skip handle dups (2>&1).
    for match in re.finditer(r'>>?\s*(\S)', raw):
        if match.group(1) != "&":
            return True
    return False


def _guard_nonbash_shell_command(command: str, shell_kind: str) -> str | None:
    """Fail-closed guard for non-POSIX shells (PowerShell/cmd).

    Returns a refusal payload if the command looks like it writes or deletes, or
    ``None`` to allow it (read-only commands pass through). The POSIX workspace
    guards cannot protect a PowerShell/cmd command, so rather than run it
    unguarded — which would bypass the workspace sandbox — we refuse.
    """
    if not _nonbash_command_writes(command):
        return None
    return _json_result({
        "exit_code": -1,
        "stdout": "",
        "stderr": (
            f"⛔ 已拒绝：当前系统 shell 是 {shell_kind}(非 bash)，"
            f"workspace 写入/删除保护依赖 POSIX 语义，无法验证此命令是否越界。\n"
            f"  命令：{command[:200]}\n"
            f"  请安装 Git Bash 或启用 WSL 后重试；只读命令不受此限制。"
        ),
    })


def _expand_shell_path(token: str) -> str:
    """Expand $VAR, ~, and ~user in a path token so the workspace guard sees the real path."""
    expanded = os.path.expandvars(token)
    expanded = os.path.expanduser(expanded)
    return expanded


def _extract_stderr_redirect_targets(raw: str) -> list[str]:
    """Detect stderr redirects like 2>/path, 2>>/path, &>/path."""
    targets: list[str] = []
    # 2>/path or 2>>/path
    for m in re.finditer(r'(?:^|\s)(\d*)>>?\s*([^\s;&|]+)', raw):
        prefix = m.group(1)  # empty or digit
        target = m.group(2)
        # If prefix is empty or a digit like 2, and target doesn't start with & (like &1)
        if (not prefix or prefix.isdigit()) and not target.startswith("&"):
            targets.append(target)
    # &>/path (redirect both stdout and stderr)
    for m in re.finditer(r'(?:^|\s)&\s*>\s*([^\s;&|]+)', raw):
        targets.append(m.group(1))
    return targets


def _is_null_device_redirect_target(token: str) -> bool:
    """Return True for harmless redirects to the platform null device."""
    raw = str(token or "").strip().strip("'\"")
    if not raw:
        return False
    expanded = _expand_shell_path(raw).rstrip("/")
    if expanded == os.devnull:
        return True
    return expanded.lower() in {"nul", "nul:"}


def _guard_shell_command_workspace_write(command: str) -> None:
    raw = str(command or "").strip()
    if not raw or not _shell_command_requires_write_guard(raw):
        return
    # 命令替换无法预测展开后的路径，直接拦截
    _check_command_substitution(raw)
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        raise ValueError(
            f"⛔ 已禁止：Shell 命令可能包含写入操作，但无法解析。\n"
            f"  命令：{command[:200]}\n"
            f"  写入权限限定在 workspace 范围内。"
        )
    write_cmds = {"rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install", "rmdir", "unlink", "dd", "ln"}
    cd_cmds = {"cd", "pushd"}
    separators = {"&&", "||", "|", ";"}
    path_like_tokens: list[str] = []
    active_command: str = ""  # Persists across arguments until a separator

    for token in tokens:
        stripped = token.strip()
        if not stripped:
            continue
        # Separator resets command context
        if stripped in separators:
            active_command = ""
            continue
        # Detect new write command
        if stripped in write_cmds:
            active_command = stripped
            continue
        if stripped in cd_cmds:
            active_command = stripped
            continue
        # Handle -o / --output flag (for tee, sed, etc.)
        if stripped in {"-o", "--output"}:
            path_like_tokens.append(stripped)
            continue
        # Redirect token: >path or >>path (may be attached like ">/path" or separate like "> /path")
        if stripped.startswith((">", ">>")):
            candidate = stripped.lstrip(">").strip()
            if candidate:
                path_like_tokens.append(candidate)
            active_command = ""
            continue
        if active_command in write_cmds:
            # Skip flags (start with -)
            if stripped.startswith("-"):
                continue
            # Path-like token: starts with / ./, contains /, or has file extension
            if (stripped.startswith("/") or stripped.startswith("./") or stripped.startswith("../")
                    or "/" in stripped or re.search(r"\.[A-Za-z0-9]{1,8}$", stripped)):
                # For cp/mv: only the last non-flag argument is the destination
                if active_command in ("cp", "mv"):
                    path_like_tokens.append(stripped)
                else:
                    path_like_tokens.append(stripped)
            # For cp/mv with all remaining args as dest, collect them all
            elif active_command in ("cp", "mv"):
                # This could be a dest without path chars (e.g. "cp a b" where "b" is relative dest)
                path_like_tokens.append(stripped)
        elif active_command in cd_cmds:
            # cd destination — resolve from workspace
            if not stripped.startswith("-"):
                path_like_tokens.append(stripped)

    # Detect stderr redirects (2>/path, &>/path) that the loop may have missed
    stderr_targets = _extract_stderr_redirect_targets(raw)
    path_like_tokens.extend(stderr_targets)

    # Fallback: detect > redirects not caught by the loop (e.g. 2>/path where 2 is separate)
    if ">" in raw or ">>" in raw:
        redirection_targets = re.findall(r"(?:^|[^\d])>>?\s*([^\s;&|]+)", raw)
        for target in redirection_targets:
            if target not in path_like_tokens:
                path_like_tokens.append(target)

    blocked_paths: list[str] = []
    for token in path_like_tokens:
        if token.startswith("-"):
            continue
        if _is_null_device_redirect_target(token):
            continue
        try:
            # Expand $VAR and ~ before checking workspace boundary
            expanded = _expand_shell_path(token)
            if expanded != token:
                _resolve_workspace_write_target(expanded)
            else:
                _resolve_workspace_write_target(token)
        except ValueError:
            blocked_paths.append(token)
    if blocked_paths:
        raise ValueError(
            f"⛔ 已禁止：Shell 命令试图写入 workspace 之外的路径。\n"
            f"  命令：{command[:200]}\n"
            f"  被阻止的路径：{', '.join(blocked_paths[:5])}\n"
            f"  如需操作外部文件，请通过 WebUI 申请权限。"
        )


def _json_result(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False)


def _resolve_tool_path(path_str: str) -> Path:
    if is_uploaded_attachment_path(path_str) or is_exported_attachment_path(path_str):
        return Path(path_str).resolve()
    # Auto-resolve filename to the correct upload path when the agent guesses wrong paths.
    from cyrene.agent.state import _attachment_paths_by_name, active_workspace_dir
    att_map = _attachment_paths_by_name.get()
    if att_map:
        basename = Path(path_str).name
        if basename in att_map:
            return Path(att_map[basename]).resolve()
    # Honour temporary full-access grants (write-once, read-always) and permanent mode.
    if _has_full_path_access():
        candidate = Path(path_str)
        path = candidate if candidate.is_absolute() else active_workspace_dir() / candidate
        return path.resolve()
    return _resolve_workspace_path(path_str)


def _resolve_exportable_path(path_str: str) -> Path:
    # The active project workspace (Workbench task) — falls back to the global
    # WORKSPACE_DIR for legacy chat / scheduler runs, so behaviour there is
    # unchanged. This is where the agent's file tools (Write/Bash) actually
    # write, so a deliverable created inside the project workspace must be
    # sendable even when that workspace lives outside the global WORKSPACE_DIR.
    from cyrene.agent.state import active_workspace_dir
    active_ws = active_workspace_dir().resolve()
    candidate = Path(path_str)
    path = candidate if candidate.is_absolute() else active_ws / candidate
    resolved = path.resolve()
    if _has_full_path_access():
        return resolved
    allowed_roots = (active_ws, WORKSPACE_DIR.resolve(), DATA_DIR.resolve())
    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(f"Path cannot be sent to WebUI: {path_str}")
