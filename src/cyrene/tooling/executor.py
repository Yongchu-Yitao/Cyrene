"""Tool execution dispatch for Cyrene."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from cyrene.agent.context import current_assistant_meta, current_run_context
from cyrene.tooling.catalog import TOOL_HANDLERS
from cyrene.tooling.execution_context import (
    is_system_initiated_round as _is_system_initiated_round,
)
from cyrene.runtime.secret_redaction import redact_text, redact_value
from cyrene.runtime.task_lifecycle import drain_or_cancel, track_task
from cyrene.tooling.types import ToolExecutionContext

# Set to True to suppress background action recording (used by RunLearnedSkill replay).
_skip_action_recording: ContextVar[bool] = ContextVar("_skip_action_recording", default=False)
# The main-agent dispatcher sets this while executing a concrete LLM tool call.
# Keeping the id in task-local context lets the completion event update the
# already-rendered UI row without changing the long-standing _execute_tool API.
_active_tool_call_id: ContextVar[str] = ContextVar("_active_tool_call_id", default="")

logger = logging.getLogger(__name__)

_PROCESS_EXECUTION_TOOLS = frozenset({"Bash", "StartShell", "SendShell"})
_OPAQUE_SHELL_EXECUTABLES = frozenset({
    "bash", "sh", "zsh", "fish", "dash",
    "python", "python3", "node", "ruby", "perl",
    "eval", "env", "xargs",
})
_NETWORK_SHELL_EXECUTABLES = frozenset({
    "curl", "wget", "ssh", "scp", "sftp", "ftp",
    "nc", "ncat", "socat", "telnet",
})


def _shell_command_needs_explicit_review(arguments: dict[str, Any]) -> bool:
    """Flag shell calls whose scope cannot be kept inside the workspace."""
    command = str(arguments.get("command") or "").strip()
    cwd = str(arguments.get("cwd") or "").strip()
    from cyrene.agent.context import active_workspace_dir

    workspace = active_workspace_dir().resolve()
    if cwd:
        candidate = Path(cwd).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        resolved_cwd = candidate.resolve()
        if resolved_cwd != workspace and workspace not in resolved_cwd.parents:
            return True
    if not command:
        return False
    if "$(" in command or "`" in command:
        return True
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return True
    expect_executable = True
    separators = {"|", "||", "&&", ";", "&"}
    for token in tokens:
        if token in separators:
            expect_executable = True
            continue
        if expect_executable:
            executable = Path(token).name.lower()
            if executable in _OPAQUE_SHELL_EXECUTABLES | _NETWORK_SHELL_EXECUTABLES:
                return True
            expect_executable = False
            continue
        if token.startswith("-") or token in {">", ">>", "<", "2>", "2>>"}:
            continue
        expanded = Path(
            re.sub(r"^\$HOME(?=/|$)", str(Path.home()), token)
        ).expanduser()
        if not expanded.is_absolute():
            continue
        resolved = expanded.resolve()
        if resolved != workspace and workspace not in resolved.parents:
            return True
    return False

_pending_action_record_tasks: set[asyncio.Task[Any]] = set()
_pending_timed_out_tool_tasks: set[asyncio.Task[Any]] = set()


class ToolExecutionBinding:
    """Reset handle for one tool-execution ContextVar assignment."""

    def __init__(self, variable: ContextVar[Any], token: Any):
        self._variable = variable
        self._token = token

    def reset(self) -> None:
        if self._token is None:
            return
        token, self._token = self._token, None
        self._variable.reset(token)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.reset()


def bind_active_tool_call(tool_call_id: str) -> ToolExecutionBinding:
    return ToolExecutionBinding(
        _active_tool_call_id,
        _active_tool_call_id.set(str(tool_call_id or "")),
    )


async def publish_tool_progress(
    *,
    current: int,
    total: int,
    label: str = "",
) -> None:
    """Publish bounded numeric progress for the currently executing tool."""
    from cyrene.agent.context import publish_runtime_event

    run_context = current_run_context()
    tool_call_id = _active_tool_call_id.get()
    if not tool_call_id:
        return
    safe_total = max(0, int(total))
    safe_current = max(0, min(int(current), safe_total)) if safe_total else 0
    await publish_runtime_event({
        "type": "tool_call_progress",
        "tool_call_id": tool_call_id,
        "current": safe_current,
        "total": safe_total,
        "progress": (
            1.0 if safe_total == 0 else min(1.0, safe_current / safe_total)
        ),
        "label": str(label or "")[:160],
        "round_id": run_context.round_id,
        "session_id": run_context.session_id,
    })


def suspend_action_recording() -> ToolExecutionBinding:
    return ToolExecutionBinding(
        _skip_action_recording,
        _skip_action_recording.set(True),
    )


def _tool_call_event(name: str, arguments: dict[str, Any], result: Any) -> dict[str, Any]:
    """Build a completion event, preserving the originating tool-call id."""
    run_context = current_run_context()

    event: dict[str, Any] = {
        "type": "tool_call",
        "caller": run_context.caller,
        "tool": name,
        "args": redact_value(arguments),
        "result": redact_text(str(result)),
        "round_id": run_context.round_id,
    }
    tool_call_id = _active_tool_call_id.get()
    if tool_call_id:
        event["tool_call_id"] = tool_call_id
    return event


def _record_action_background(*args: Any, **kwargs: Any) -> None:
    """Record behavior-learning telemetry without delaying tool results."""
    if _skip_action_recording.get():
        return
    try:
        from cyrene.learning import record_action

        task = asyncio.create_task(record_action(*args, **kwargs))
    except Exception:
        logger.debug("failed to schedule behavior action telemetry", exc_info=True)
        return

    track_task(
        task,
        _pending_action_record_tasks,
        logger=logger,
        label="behavior action telemetry",
    )


async def shutdown_background_tasks() -> None:
    """Flush telemetry and timed-out tool cleanup before the loop closes."""
    await drain_or_cancel(_pending_action_record_tasks, grace_seconds=2.0)
    _pending_action_record_tasks.clear()
    await drain_or_cancel(_pending_timed_out_tool_tasks, grace_seconds=0.5)
    _pending_timed_out_tool_tasks.clear()


async def flush_behavior_action_tasks(grace_seconds: float = 2.0) -> None:
    """Wait for action telemetry already queued by the current agent turn."""
    pending = [task for task in list(_pending_action_record_tasks) if not task.done()]
    if not pending:
        return
    done, still_pending = await asyncio.wait(pending, timeout=max(0.0, grace_seconds))
    for task in done:
        try:
            task.result()
        except Exception:
            logger.debug("behavior action telemetry task failed during flush", exc_info=True)
    if still_pending:
        logger.warning("Timed out waiting for %d behavior telemetry tasks", len(still_pending))

_BROWSER_TOOL_NAMES = {
    "browser_navigate",
    "browser_snapshot",
    "browser_screenshot",
    "browser_click",
    "browser_click_ref",
    "browser_click_text",
    "browser_click_at",
    "browser_type",
    "browser_type_ref",
    "browser_upload_files",
    "browser_wait",
    "browser_network_log",
    "browser_request_takeover",
}

_DEFAULT_TOOL_TIMEOUT_SECONDS = 180.0
_HIGH_QUALITY_IMAGE_TOOL_TIMEOUT_SECONDS = 420.0
_TOOL_TIMEOUT_SECONDS = {
    "Read": 30.0,
    "Write": 30.0,
    "Edit": 30.0,
    "Glob": 30.0,
    "Grep": 30.0,
    # visual_click and visual_type may perform two independently bounded
    # 60-second vision passes plus desktop capture and verification RPCs.
    "app_use": 180.0,
}


def _tool_timeout_seconds(name: str, arguments: dict[str, Any]) -> float:
    """Return a hard wall-clock budget for every tool invocation."""
    if name == "Bash":
        try:
            requested = max(1.0, float(arguments.get("timeout_ms", 120000)) / 1000)
        except (TypeError, ValueError):
            requested = 120.0
        # Allow a small cleanup margin beyond Bash's own command deadline.
        return min(requested + 10.0, 310.0)
    if name == "browser_request_takeover":
        return 900.0
    if name == "GenerateImage":
        quality = str(arguments.get("quality") or "medium").strip().lower()
        if quality == "high":
            # The OAuth/Codex image runtime needs room around the provider's
            # generation budget for startup, capability checks, cancellation,
            # image validation, and attachment delivery.
            return _HIGH_QUALITY_IMAGE_TOOL_TIMEOUT_SECONDS
    return _TOOL_TIMEOUT_SECONDS.get(name, _DEFAULT_TOOL_TIMEOUT_SECONDS)


async def _run_with_tool_timeout(
    name: str,
    arguments: dict[str, Any],
    awaitable: Any,
) -> Any:
    timeout = _tool_timeout_seconds(name, arguments)
    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.cancel()
        raise
    if done:
        return await task

    # Do not let a connector that suppresses cancellation hold the agent loop
    # beyond its wall-clock budget. Keep ownership of the task so shutdown can
    # make one last bounded drain attempt.
    task.cancel()
    track_task(
        task,
        _pending_timed_out_tool_tasks,
        logger=logger,
        label=f"timed-out tool {name}",
    )
    return f"Tool failed: {name} timed out after {timeout:g} seconds."


def _proactive_tool_refusal(name: str, arguments: dict[str, Any]) -> str | None:
    """Hard-stop non-incremental filesystem tools during proactive rounds."""
    if not _is_system_initiated_round():
        return None
    if name == "Edit":
        return (
            "Tool unavailable: proactive system-initiated rounds may only do "
            "incremental file work. Editing existing files is forbidden."
        )
    if name in {"Bash", "SendShell", "StartShell"}:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return None
        try:
            from cyrene.tooling.runtime_api import (
                classify_destructive_shell_command,
                command_is_file_deletion,
                shell_command_requires_write_guard,
            )

            writes = shell_command_requires_write_guard(command)
            destructive = classify_destructive_shell_command(command) is not None
            deletes = command_is_file_deletion(command)
        except Exception:
            writes = any(token in command for token in (">", ">>"))
            destructive = False
            deletes = False
        if writes or destructive or deletes:
            return (
                "Tool unavailable: proactive system-initiated rounds cannot run "
                "shell commands that write, overwrite, move, or delete files. "
                "Use read-only shell commands, or use Write for a brand-new file path."
            )
    return None


async def _execute_tool(name: str, arguments: dict[str, Any], bot: Any, chat_id: int, db_path: str, notify_state: dict[str, bool] | None) -> str:
    proactive_refusal = _proactive_tool_refusal(name, arguments)
    if proactive_refusal is not None:
        return proactive_refusal
    if name == "ask_user":
        assistant_meta = current_assistant_meta()
        if isinstance(assistant_meta, dict) and assistant_meta.get("system_initiated"):
            return (
                "Tool unavailable: proactive system-initiated rounds cannot ask "
                "the user to clarify or pause for an answer."
            )
    if name == "spawn_subagent":
        from cyrene.runtime.settings_store import get_spawn_policy
        if get_spawn_policy() == "off":
            return "Subagent spawning is disabled by the current spawn policy (`off`). Stay in single-agent mode unless the user explicitly changes this setting."
    if name in _BROWSER_TOOL_NAMES:
        from cyrene.runtime.settings_store import is_tool_pack_enabled
        if not is_tool_pack_enabled("browser_tools"):
            return "Browser automation tools are disabled in settings. Re-enable browser tools before using this action."
    handler = TOOL_HANDLERS.get(name)
    run_context = current_run_context()
    if (
        name in _PROCESS_EXECUTION_TOOLS
        and run_context.permission_mode != "full_access"
        and (
            run_context.permission_mode == "auto"
            or _shell_command_needs_explicit_review(arguments)
        )
    ):
        from cyrene.tooling.runtime_api import request_scope_elevation

        command_preview = str(arguments.get("command") or "").strip()[:500]
        permission_result = await request_scope_elevation(
            tool_name=name,
            path_hint=str(arguments.get("cwd") or ""),
            operation="执行本地进程或 Shell 命令",
            reason=f"命令：{command_preview or '[启动交互式 shell]'}",
            permission_kind="process_execution",
            options=["允许执行这一次", "拒绝"],
            scope_hint="进程执行的 ",
        )
        if permission_result is not None:
            return permission_result
    if handler is None:
        from cyrene.observability import debug as _debug
        from cyrene.tooling.backends.mcp_manager import get_manager as _get_mcp_mgr

        from cyrene.tooling.runtime_api import request_scope_elevation

        manager = _get_mcp_mgr()
        has_tool = getattr(manager, "has_tool", None)
        if callable(has_tool) and not has_tool(name):
            raise ValueError(f"Unknown tool: {name}")
        safe_arguments = redact_value(arguments)
        permission_result = await request_scope_elevation(
            tool_name=name,
            path_hint="",
            operation="调用外部 MCP/集成工具",
            reason=(
                "外部工具参数："
                + json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)[:800]
            ),
            permission_kind="external_tool_execution",
            options=["允许调用这一次", "拒绝"],
            scope_hint="外部工具调用的 ",
        )
        if permission_result is not None:
            return permission_result

        _t0 = time.monotonic()
        try:
            result = await _run_with_tool_timeout(
                name, arguments, manager.execute_tool(name, arguments)
            )
            if _debug.VERBOSE:
                _debug.log_tool_call(run_context.caller, name, redact_value(arguments), redact_text(result), (time.monotonic() - _t0) * 1000)
            await _debug.publish_event(
                _tool_call_event(name, arguments, result),
                session_id=run_context.session_id,
            )
            tool_success = not str(result).lower().startswith("tool failed:")
            _record_action_background(
                name,
                redact_value(arguments),
                run_context.caller,
                run_context.round_id,
                (time.monotonic() - _t0) * 1000,
                result=redact_text(result),
                success=tool_success,
                error="" if tool_success else redact_text(str(result)),
            )
            return result
        except ValueError:
            raise ValueError(f"Unknown tool: {name}")
        except Exception as e:
            run_context = current_run_context()
            _record_action_background(
                name,
                redact_value(arguments),
                run_context.caller,
                run_context.round_id,
                (time.monotonic() - _t0) * 1000,
                result=redact_text(f"Tool {name} failed: {e}"),
                success=False,
                error=redact_text(str(e)),
            )
            return f"Tool {name} failed: {e}"

    _t0 = time.monotonic()
    try:
        result = await _run_with_tool_timeout(
            name,
            arguments,
            handler(arguments, bot, chat_id, db_path, notify_state),
        )
    except Exception as e:
        from cyrene.observability import debug
        run_context = current_run_context()
        await debug.publish_event(
            _tool_call_event(name, arguments, f"Tool failed: {e}"),
            session_id=run_context.session_id,
        )
        _record_action_background(
            name,
            redact_value(arguments),
            run_context.caller,
            run_context.round_id,
            (time.monotonic() - _t0) * 1000,
            result=redact_text(f"Tool failed: {e}"),
            success=False,
            error=redact_text(str(e)),
        )
        raise
    from cyrene.observability import debug
    run_context = current_run_context()
    if debug.VERBOSE:
        debug.log_tool_call(run_context.caller, name, redact_value(arguments), redact_text(result), (time.monotonic() - _t0) * 1000)
    await debug.publish_event(
        _tool_call_event(name, arguments, result),
        session_id=run_context.session_id,
    )
    tool_success = not str(result).lower().startswith("tool failed:")
    _record_action_background(
        name,
        redact_value(arguments),
        run_context.caller,
        run_context.round_id,
        (time.monotonic() - _t0) * 1000,
        result=redact_text(result),
        success=tool_success,
        error="" if tool_success else redact_text(str(result)),
    )
    return result


async def execute_concrete_tool(
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    """Execute one catalog-selected concrete capability in a typed context."""
    return await _execute_tool(
        name,
        arguments,
        context.bot,
        context.chat_id,
        context.db_path,
        context.notify_state,
    )


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    bot: Any,
    chat_id: int,
    database_path: str,
    notify_state: dict[str, bool] | None,
) -> str:
    """Public compatibility boundary for direct execution consumers."""
    return await _execute_tool(
        name,
        arguments,
        bot,
        chat_id,
        database_path,
        notify_state,
    )
