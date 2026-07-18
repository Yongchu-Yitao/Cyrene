"""Tool execution dispatch for Cyrene."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Any

from cyrene.registry_tools import TOOL_HANDLERS
from cyrene.secret_redaction import redact_text, redact_value
from cyrene.task_lifecycle import drain_or_cancel, track_task

# Set to True to suppress background action recording (used by RunLearnedSkill replay).
_skip_action_recording: ContextVar[bool] = ContextVar("_skip_action_recording", default=False)
# The main-agent dispatcher sets this while executing a concrete LLM tool call.
# Keeping the id in task-local context lets the completion event update the
# already-rendered UI row without changing the long-standing _execute_tool API.
_active_tool_call_id: ContextVar[str] = ContextVar("_active_tool_call_id", default="")

logger = logging.getLogger(__name__)

_pending_action_record_tasks: set[asyncio.Task[Any]] = set()
_pending_timed_out_tool_tasks: set[asyncio.Task[Any]] = set()


def _tool_call_event(name: str, arguments: dict[str, Any], result: Any) -> dict[str, Any]:
    """Build a completion event, preserving the originating tool-call id."""
    from cyrene.agent.state import _caller_type, _current_round_id

    event: dict[str, Any] = {
        "type": "tool_call",
        "caller": _caller_type.get(),
        "tool": name,
        "args": redact_value(arguments),
        "result": redact_text(str(result)),
        "round_id": _current_round_id.get(),
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
        from cyrene.pattern import record_action

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


def _is_system_initiated_round() -> bool:
    try:
        from cyrene.agent.state import _ui_round_assistant_meta

        meta = _ui_round_assistant_meta.get()
        return isinstance(meta, dict) and bool(meta.get("system_initiated"))
    except Exception:
        return False


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
            from cyrene.tool_legacy import (
                _classify_destructive_shell_command,
                _command_is_file_deletion,
                _shell_command_requires_write_guard,
            )

            writes = _shell_command_requires_write_guard(command)
            destructive = _classify_destructive_shell_command(command) is not None
            deletes = _command_is_file_deletion(command)
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
        from cyrene.agent.state import _ui_round_assistant_meta

        assistant_meta = _ui_round_assistant_meta.get()
        if isinstance(assistant_meta, dict) and assistant_meta.get("system_initiated"):
            return (
                "Tool unavailable: proactive system-initiated rounds cannot ask "
                "the user to clarify or pause for an answer."
            )
    if name == "spawn_subagent":
        from cyrene.settings_store import get_spawn_policy
        if get_spawn_policy() == "off":
            return "Subagent spawning is disabled by the current spawn policy (`off`). Stay in single-agent mode unless the user explicitly changes this setting."
    if name in _BROWSER_TOOL_NAMES:
        from cyrene.settings_store import is_tool_enabled
        if not is_tool_enabled(name):
            return "Browser automation tools are disabled in settings. Re-enable browser tools before using this action."
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        from cyrene import debug as _debug
        from cyrene.agent.state import _caller_type, _current_round_id, _current_session_id
        from cyrene.mcp_manager import get_manager as _get_mcp_mgr

        _t0 = time.monotonic()
        try:
            manager = _get_mcp_mgr()
            result = await _run_with_tool_timeout(
                name, arguments, manager.execute_tool(name, arguments)
            )
            if _debug.VERBOSE:
                _debug.log_tool_call(_caller_type.get(), name, redact_value(arguments), redact_text(result), (time.monotonic() - _t0) * 1000)
            await _debug.publish_event(
                _tool_call_event(name, arguments, result),
                session_id=_current_session_id.get(),
            )
            tool_success = not str(result).lower().startswith("tool failed:")
            _record_action_background(
                name,
                redact_value(arguments),
                _caller_type.get(),
                _current_round_id.get(),
                (time.monotonic() - _t0) * 1000,
                result=redact_text(result),
                success=tool_success,
                error="" if tool_success else redact_text(str(result)),
            )
            return result
        except ValueError:
            raise ValueError(f"Unknown tool: {name}")
        except Exception as e:
            _record_action_background(
                name,
                redact_value(arguments),
                _caller_type.get(),
                _current_round_id.get(),
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
        from cyrene import debug
        from cyrene.agent.state import _caller_type, _current_round_id, _current_session_id
        await debug.publish_event(
            _tool_call_event(name, arguments, f"Tool failed: {e}"),
            session_id=_current_session_id.get(),
        )
        _record_action_background(
            name,
            redact_value(arguments),
            _caller_type.get(),
            _current_round_id.get(),
            (time.monotonic() - _t0) * 1000,
            result=redact_text(f"Tool failed: {e}"),
            success=False,
            error=redact_text(str(e)),
        )
        raise
    from cyrene import debug
    if debug.VERBOSE:
        from cyrene.agent.state import _caller_type
        debug.log_tool_call(_caller_type.get(), name, redact_value(arguments), redact_text(result), (time.monotonic() - _t0) * 1000)
    from cyrene.agent.state import _caller_type, _current_round_id, _current_session_id
    await debug.publish_event(
        _tool_call_event(name, arguments, result),
        session_id=_current_session_id.get(),
    )
    tool_success = not str(result).lower().startswith("tool failed:")
    _record_action_background(
        name,
        redact_value(arguments),
        _caller_type.get(),
        _current_round_id.get(),
        (time.monotonic() - _t0) * 1000,
        result=redact_text(result),
        success=tool_success,
        error="" if tool_success else redact_text(str(result)),
    )
    return result
