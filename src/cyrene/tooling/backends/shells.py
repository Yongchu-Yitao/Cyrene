"""Persistent independent shell sessions for long-running agent workflows."""

import asyncio
import os
import signal
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.observability import debug
from cyrene.config import WORKSPACE_DIR
from cyrene.tooling.backends.shell_registry import (
    external_shells as _external_shells,
)
from cyrene.tooling.backends.shell_runtime import command_argv, interactive_argv, resolve_shell

_shells: dict[str, dict[str, Any]] = {}
_shell_lock = asyncio.Lock()
_shell_counter = 0
# Historical no-op lock retained for callers that imported or inspected it.
_ext_lock = asyncio.Lock()

def _resolve_cwd(
    path_str: str,
    workspace_root: str | Path | None = None,
) -> tuple[Path, Path]:
    root = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root
        else WORKSPACE_DIR.resolve()
    )
    candidate = Path(path_str or ".")
    path = candidate if candidate.is_absolute() else root / candidate
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path escapes workspace: {path_str}")
    return resolved, root


def _short_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")
    except Exception:
        return "—"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m:02d}:{s:02d}"


async def _publish_shell_update(shell_id: str) -> None:
    snap = get_shell_snapshot(shell_id)
    if not snap:
        return
    await debug.publish_event({
        "type": "shell_update",
        "shell_id": shell_id,
        "status": snap.get("status", ""),
        "title": snap.get("title", ""),
        "cwd": snap.get("cwd", ""),
        "round_id": snap.get("roundId", ""),
    })


async def _append_lines(shell_id: str, kind: str, text: str) -> None:
    text = str(text or "")
    if not text:
        return
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is None:
            return
        for raw_line in text.splitlines():
            shell["line_seq"] += 1
            shell["lines"].append(
                {
                    "seq": shell["line_seq"],
                    "kind": kind,
                    "text": raw_line,
                }
            )
        shell["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _publish_shell_update(shell_id)


async def _pump_stream(shell_id: str, stream: asyncio.StreamReader | None, kind: str) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.readline()
            if not chunk:
                break
            await _append_lines(shell_id, kind, chunk.decode("utf-8", errors="replace").rstrip("\n"))
    except Exception:
        await _append_lines(shell_id, "err", f"[{kind} stream error]")


async def _watch_shell(shell_id: str) -> None:
    proc = None
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is not None:
            proc = shell.get("proc")
    if proc is None:
        return
    try:
        code = await proc.wait()
    except Exception:
        code = -1
    # Drain both pipes before building the wake snapshot.  Fast one-shot jobs
    # can exit before their pump tasks have appended the final output lines.
    async with _shell_lock:
        current = _shells.get(shell_id) or {}
        pump_tasks = [
            task
            for task in (current.get("stdout_task"), current.get("stderr_task"))
            if task is not None
        ]
    if pump_tasks:
        await asyncio.gather(*pump_tasks, return_exceptions=True)
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is None:
            return
        shell["status"] = "done" if code == 0 else "err"
        shell["exit_code"] = code
        shell["updated_at"] = datetime.now(timezone.utc).isoformat()
        wake_on_exit = bool(shell.get("wake_on_exit"))
    await _publish_shell_update(shell_id)
    if wake_on_exit:
        try:
            from cyrene.runtime.shell_wake import notify_shell_exit

            await notify_shell_exit(
                shell_id,
                status="done" if code == 0 else "err",
                exit_code=code,
                snapshot=get_shell_snapshot(shell_id),
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to dispatch shell-exit wake for %s", shell_id
            )


async def start_shell(
    command: str = "",
    cwd: str = ".",
    title: str = "",
    round_id: str = "",
    *,
    wake_on_exit: bool = False,
    wake_chat_id: str = "",
    wake_note: str = "",
    workspace_root: str | Path | None = None,
    interactive: bool = True,
    survive_interrupt: bool = False,
) -> dict[str, Any]:
    """Start a persistent shell, or a watched one-shot initial command."""
    global _shell_counter
    # A watched initial command is a job, not a persistent terminal.  Run it as
    # the shell process itself so command completion also ends the process and
    # reliably reaches ``_watch_shell``.  Previously the command was written to
    # an interactive shell which returned to its prompt after the command and
    # therefore never emitted the promised exit wake.
    one_shot_command = bool(wake_on_exit and command.strip())
    if one_shot_command:
        shell_kind, _executable = resolve_shell(unix_fallback="/bin/bash")
        shell_argv = command_argv(command)
    elif interactive:
        shell_kind, shell_argv = interactive_argv()
    else:
        shell_kind, executable = resolve_shell(unix_fallback="/bin/bash")
        if shell_kind == "bash":
            shell_argv = [executable]
        elif shell_kind == "powershell":
            shell_argv = [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "-",
            ]
        else:
            shell_argv = [executable, "/d", "/q"]
    resolved_cwd, confined_root = _resolve_cwd(cwd, workspace_root)
    env = dict(os.environ)
    # npm injects this into Electron development launches; nvm treats it as an
    # incompatible user override and prints a warning in every child shell.
    env.pop("npm_config_prefix", None)
    env.pop("NPM_CONFIG_PREFIX", None)
    env["PS1"] = ""
    env.setdefault("TERM", "dumb")
    proc = await asyncio.create_subprocess_exec(
        *shell_argv,
        cwd=str(resolved_cwd),
        env=env,
        stdin=(asyncio.subprocess.DEVNULL if one_shot_command else asyncio.subprocess.PIPE),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    if (
        survive_interrupt
        and os.name != "nt"
        and shell_kind == "bash"
        and proc.stdin is not None
    ):
        # A non-interactive shell normally exits when its process group gets
        # SIGINT. Keep the shell alive while child commands retain the default
        # disposition, matching the useful part of an interactive Ctrl+C.
        proc.stdin.write(b"trap ':' INT\n")
        await proc.stdin.drain()

    _shell_counter += 1
    shell_id = f"shell_{int(time.time() * 1000)}_{_shell_counter}"
    now = datetime.now(timezone.utc).isoformat()
    want_wake = bool(wake_on_exit)
    chat_id = str(wake_chat_id or "").strip()
    note = str(wake_note or "").strip()
    wake_record: dict[str, Any] | None = None
    if want_wake and chat_id:
        from cyrene.runtime.shell_wake import get_shell_wake_service

        wake_record = await get_shell_wake_service().register_wake(
            shell_id=shell_id,
            chat_id=chat_id,
            note=note,
            title=title.strip() or "independent shell",
            round_id=round_id,
        )
    elif want_wake and not chat_id:
        want_wake = False

    async with _shell_lock:
        _shells[shell_id] = {
            "id": shell_id,
            "title": title.strip() or "independent shell",
            "cwd": (
                str(resolved_cwd.relative_to(confined_root))
                if resolved_cwd != confined_root
                else "."
            ),
            "pid": proc.pid,
            "status": "running",
            "round_id": round_id,
            "created_at": now,
            "updated_at": now,
            "exit_code": None,
            "proc": proc,
            "execution_mode": "one_shot" if one_shot_command else "persistent",
            "lines": deque(maxlen=240),
            "line_seq": 0,
            "wake_on_exit": want_wake,
            "wake_chat_id": chat_id if want_wake else "",
            "wake_note": note if want_wake else "",
            "wake_id": str((wake_record or {}).get("wake_id") or ""),
        }
    await _append_lines(shell_id, "meta", f"[shell started: {shell_kind} ({shell_argv[0]})]")
    if want_wake:
        await _append_lines(
            shell_id,
            "meta",
            f"[wake_on_exit registered for chat {chat_id}"
            + (f"; note={note[:120]}" if note else "")
            + "]",
        )
    if one_shot_command:
        await _append_lines(shell_id, "prompt", f"$ {command}")
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is not None:
            shell["stdout_task"] = asyncio.create_task(_pump_stream(shell_id, proc.stdout, "out"))
            shell["stderr_task"] = asyncio.create_task(_pump_stream(shell_id, proc.stderr, "err"))
            shell["watch_task"] = asyncio.create_task(_watch_shell(shell_id))
    if command.strip() and not one_shot_command:
        await send_shell(shell_id, command)
    return get_shell_snapshot(shell_id) or {}


async def send_shell(shell_id: str, command: str, wait_ms: int = 700) -> dict[str, Any]:
    """Send a command to a running shell and return the updated snapshot."""
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is None:
            raise ValueError(f"Unknown shell: {shell_id}")
        proc = shell.get("proc")
        if proc is None or proc.stdin is None:
            raise ValueError(f"Shell {shell_id} is not writable")
        if shell.get("status") != "running":
            raise ValueError(f"Shell {shell_id} is not running")
        proc.stdin.write((command.rstrip("\n") + "\n").encode("utf-8"))
        await proc.stdin.drain()
        shell["line_seq"] += 1
        shell["lines"].append(
            {
                "seq": shell["line_seq"],
                "kind": "prompt",
                "text": f"$ {command}",
            }
        )
        shell["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _publish_shell_update(shell_id)
    await asyncio.sleep(max(0, wait_ms) / 1000)
    return get_shell_snapshot(shell_id) or {}


async def close_shell(shell_id: str) -> dict[str, Any]:
    """Terminate a persistent shell session."""
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is None:
            raise ValueError(f"Unknown shell: {shell_id}")
        proc = shell.get("proc")
        if proc and proc.returncode is None:
            proc.terminate()
    await asyncio.sleep(0.1)
    return get_shell_snapshot(shell_id) or {}


async def interrupt_shell(shell_id: str) -> dict[str, Any]:
    """Interrupt the active command without intentionally closing its shell."""
    async with _shell_lock:
        shell = _shells.get(shell_id)
        if shell is None:
            raise ValueError(f"Unknown shell: {shell_id}")
        proc = shell.get("proc")
        if proc is None or proc.returncode is not None:
            raise ValueError(f"Shell {shell_id} is not running")
        pid = int(proc.pid)
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            # Shells are created in their own session. Signalling the process
            # group reaches both the shell and its currently running child.
            os.killpg(pid, signal.SIGINT)
        shell["updated_at"] = datetime.now(timezone.utc).isoformat()
    await _append_lines(shell_id, "meta", "^C")
    await asyncio.sleep(0.1)
    return get_shell_snapshot(shell_id) or {}


def get_shell_snapshot(shell_id: str) -> dict[str, Any] | None:
    shell = _shells.get(shell_id)
    if shell is None:
        return None
    created_at = shell.get("created_at")
    elapsed = "—"
    if created_at:
        try:
            created_dt = datetime.fromisoformat(str(created_at)).astimezone(timezone.utc)
            elapsed = _format_duration((datetime.now(timezone.utc) - created_dt).total_seconds())
        except Exception:
            elapsed = "—"
    snapshot = {
        "id": shell_id,
        "title": shell.get("title", "independent shell"),
        "cwd": shell.get("cwd", "."),
        "pid": shell.get("pid", "—"),
        "status": shell.get("status", "running"),
        "exitCode": shell.get("exit_code"),
        "roundId": shell.get("round_id", ""),
        "createdAt": _short_time(shell.get("created_at")),
        "updatedAt": _short_time(shell.get("updated_at")),
        "elapsed": elapsed,
        "lines": list(shell.get("lines", [])),
        "nextCursor": int(shell.get("line_seq") or 0),
        "wakeOnExit": bool(shell.get("wake_on_exit")),
        "wakeChatId": shell.get("wake_chat_id", ""),
        "wakeId": shell.get("wake_id", ""),
        "executionMode": shell.get("execution_mode", "persistent"),
    }
    return snapshot


def list_shells(include_exited: bool = False) -> list[dict[str, Any]]:
    items = []
    for shell_id, shell in _shells.items():
        if not include_exited and shell.get("status") != "running":
            continue
        snap = get_shell_snapshot(shell_id)
        if snap:
            items.append(snap)
    for shell_id, shell in _external_shells.items():
        if not include_exited and shell.get("status") != "running":
            continue
        snap = _external_shell_snapshot(shell_id, shell)
        if snap:
            items.append(snap)
    items.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return items


def set_cc_since(since: str) -> None:
    """Set a timestamp filter for CC preview lines.

    Only CC transcript entries after this timestamp will appear in
    the shell card preview.
    """
    for shell in _external_shells.values():
        if shell.get("kind") == "cc":
            shell["cc_since"] = since


def _external_shell_snapshot(shell_id: str, shell: dict[str, Any]) -> dict[str, Any] | None:
    shell_kind = str(shell.get("kind") or "")
    latest_jsonl = ""
    cc_lines: list = []
    if shell_kind == "cc":
        try:
            from cyrene.tooling.backends.claude_code_bridge import get_cc_preview
            from pathlib import Path

            preview = get_cc_preview(
                Path(str(shell.get("cwd") or ".")).resolve(),
                min_updated_at=str(shell.get("created_at") or "").strip(),
                since=str(shell.get("cc_since") or "").strip(),
            )
            cc_lines = list(preview.get("lines") or [])
            latest_jsonl = str(preview.get("latest_jsonl") or "")
            updated_at = str(preview.get("updated_at") or "").strip()
            if updated_at:
                shell["updated_at"] = updated_at
            shell["title"] = "Claude Code"
        except Exception:
            pass

    created_at = shell.get("created_at")
    elapsed = "—"
    if created_at:
        try:
            created_dt = datetime.fromisoformat(str(created_at)).astimezone(timezone.utc)
            elapsed = _format_duration((datetime.now(timezone.utc) - created_dt).total_seconds())
        except Exception:
            elapsed = "—"
    snapshot: dict[str, Any] = {
        "id": shell_id,
        "title": shell.get("title", "external shell"),
        "cwd": shell.get("cwd", "."),
        "pid": shell.get("pid", "—"),
        "status": shell.get("status", "running"),
        "roundId": shell.get("round_id", ""),
        "createdAt": _short_time(shell.get("created_at")),
        "updatedAt": _short_time(shell.get("updated_at")),
        "elapsed": elapsed,
        "lines": cc_lines if shell_kind == "cc" else list(shell.get("lines", [])),
    }
    # Pass through extra metadata (e.g. tmuxSession, kind for CC shells)
    for key in ("kind", "tmuxSession"):
        if key in shell:
            snapshot[key] = shell[key]
    if latest_jsonl:
        snapshot["latestJsonl"] = latest_jsonl
    return snapshot
