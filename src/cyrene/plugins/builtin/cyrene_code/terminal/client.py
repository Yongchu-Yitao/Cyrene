"""Client and lifecycle bootstrap for the local Cyrene Terminal Daemon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cyrene.runtime.paths import user_data_dir

PROTOCOL_VERSION = 8
LIFECYCLE_VERSION = 1
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000


def _windows_daemon_creation_flags() -> int:
    """Detach the daemon from a kill-on-close parent Job when Windows permits it."""
    base = int(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    import ctypes
    from ctypes import wintypes

    class JobObjectBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(
        kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)
    ) or not in_job.value:
        return base
    limits = JobObjectBasicLimitInformation()
    if not kernel32.QueryInformationJobObject(
        None, 2, ctypes.byref(limits), ctypes.sizeof(limits), None
    ):
        return base
    if limits.LimitFlags & _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK:
        return base
    if limits.LimitFlags & _JOB_OBJECT_LIMIT_BREAKAWAY_OK:
        return base | _CREATE_BREAKAWAY_FROM_JOB
    return base


def terminal_state_dir() -> Path:
    override = str(os.environ.get("CYRENE_TERMINAL_STATE_DIR") or "").strip()
    return Path(override).expanduser().resolve() if override else user_data_dir() / "terminal-daemon"


class TerminalRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        retry_after_ms: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "")
        self.retry_after_ms = max(0, int(retry_after_ms or 0))


class TerminalNotFoundError(TerminalRequestError):
    pass


class TerminalDaemonConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def read(self) -> dict[str, Any]:
        line = await self.reader.readline()
        if not line:
            raise ConnectionError("terminal daemon disconnected")
        return dict(json.loads(line))

    async def send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await self.writer.drain()

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass


class TerminalDaemonClient:
    def __init__(self, *, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or terminal_state_dir()).resolve()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def connection_path(self) -> Path:
        return self.state_dir / "connection.json"

    def _connection_info(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.connection_path.read_text(encoding="utf-8"))
            if int(payload.get("version") or 0) != PROTOCOL_VERSION:
                return None
            if int(payload.get("lifecycleVersion") or 0) < LIFECYCLE_VERSION:
                return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    async def _recorded_request(
        self, payload: dict[str, Any], action: str,
    ) -> dict[str, Any] | None:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    "127.0.0.1", int(payload.get("port") or 0),
                    limit=MAX_MESSAGE_BYTES,
                ),
                timeout=0.5,
            )
            writer.write(json.dumps({
                "version": int(payload.get("version") or 0),
                "token": payload.get("token"),
                "action": action,
            }, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
            response = dict(json.loads(
                await asyncio.wait_for(reader.readline(), timeout=1.0)
            ))
            return response
        except (
            OSError, ValueError, TypeError, AttributeError, TimeoutError,
            json.JSONDecodeError,
        ):
            return None
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    def _unlink_recorded_connection(self, pid: int) -> None:
        with contextlib.suppress(OSError, ValueError, TypeError):
            current = json.loads(self.connection_path.read_text(encoding="utf-8"))
            if int(current.get("pid") or 0) == pid:
                self.connection_path.unlink()

    async def _wait_for_graceful_retirement(self, pid: int) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                current = json.loads(self.connection_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return True
            if int(current.get("pid") or 0) != pid:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _wait_for_legacy_retirement(self, payload: dict[str, Any]) -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if await self._recorded_request(payload, "ping") is None:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _retire_incompatible_daemon(self) -> bool:
        try:
            payload = dict(json.loads(self.connection_path.read_text(encoding="utf-8")))
            version = int(payload.get("version") or 0)
            pid = int(payload.get("pid") or 0)
            lifecycle_version = int(payload.get("lifecycleVersion") or 0)
        except (OSError, ValueError, TypeError):
            return False
        if (
            version == PROTOCOL_VERSION
            and lifecycle_version >= LIFECYCLE_VERSION
        ) or pid <= 1 or pid == os.getpid():
            return False
        response = await self._recorded_request(payload, "ping")
        if not response or not response.get("ok"):
            # A stale PID may have been recycled by the OS. Never signal it
            # unless the private token/version handshake proves it is Cyrene's
            # recorded daemon.
            self._unlink_recorded_connection(pid)
            return True

        shutdown = await self._recorded_request(payload, "shutdown")
        if shutdown and shutdown.get("ok"):
            if not await self._wait_for_graceful_retirement(pid):
                raise ConnectionError("terminal daemon did not finish graceful shutdown")
            return True

        # Daemons created before lifecycle version 1 do not understand the
        # shutdown request. Retire that verified legacy owner once; all current
        # and future daemons use the acknowledged IPC path above.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
        if not await self._wait_for_legacy_retirement(payload):
            raise ConnectionError("legacy terminal daemon did not stop")
        self._unlink_recorded_connection(pid)
        return True

    async def _open(self, *, start: bool = True) -> TerminalDaemonConnection:
        async with self._lifecycle_lock:
            upgrading = await self._retire_incompatible_daemon()
            info = self._connection_info()
            if info:
                try:
                    return TerminalDaemonConnection(*await asyncio.wait_for(
                        asyncio.open_connection(
                            "127.0.0.1", int(info["port"]), limit=MAX_MESSAGE_BYTES
                        ),
                        timeout=0.8,
                    ))
                except (OSError, TimeoutError, ValueError, KeyError):
                    pass
            if not start:
                raise ConnectionError("terminal daemon is unavailable")
            self._start_daemon("app_upgrade" if upgrading else "daemon_restart")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                await asyncio.sleep(0.08)
                info = self._connection_info()
                if not info:
                    continue
                try:
                    reader, writer = await asyncio.open_connection(
                        "127.0.0.1", int(info["port"]), limit=MAX_MESSAGE_BYTES
                    )
                    return TerminalDaemonConnection(reader, writer)
                except (OSError, ValueError, KeyError):
                    continue
        raise ConnectionError("terminal daemon did not start")

    def _start_daemon(self, start_reason: str = "daemon_restart") -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["CYRENE_TERMINAL_STATE_DIR"] = str(self.state_dir)
        env["CYRENE_TERMINAL_START_REASON"] = str(start_reason or "daemon_restart")
        if not getattr(sys, "frozen", False):
            source_root = next(
                (
                    candidate
                    for candidate in Path(__file__).resolve().parents
                    if (candidate / "agent").is_dir()
                    and (candidate / "cyrene").is_dir()
                ),
                Path(__file__).resolve().parents[2],
            )
            source_dir = str(source_root)
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (source_dir, env.get("PYTHONPATH", "")) if part
            )
        command = (
            [sys.executable, "--launch-terminal-daemon"]
            if getattr(sys, "frozen", False)
            else [sys.executable, str(Path(__file__).with_name("daemon.py"))]
        )
        log = (self.state_dir / "daemon.log").open("ab")
        flags = 0
        if sys.platform == "win32":  # pragma: no cover - Windows only
            flags = _windows_daemon_creation_flags()
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=env,
                cwd=str(self.state_dir),
                start_new_session=sys.platform != "win32",
                creationflags=flags,
                close_fds=True,
            )
        finally:
            log.close()

    async def _request_once(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        connection = await self._open()
        info = self._connection_info() or {}
        try:
            await connection.send({
                "version": PROTOCOL_VERSION,
                "token": info.get("token"),
                "action": action,
                **payload,
            })
            response = await connection.read()
        finally:
            await connection.close()
        if not response.get("ok"):
            message = str(response.get("error") or "terminal daemon request failed")
            if response.get("code") == "not_found":
                raise TerminalNotFoundError(message)
            raise TerminalRequestError(
                message,
                code=str(response.get("code") or ""),
                retry_after_ms=int(response.get("retryAfterMs") or 0),
            )
        return response

    async def _request(
        self, action: str, *, request_timeout: float = 12.0, **payload: Any,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self._request_once(action, payload),
                timeout=max(0.1, float(request_timeout)),
            )
        except TimeoutError as exc:
            raise TerminalRequestError(
                f"Terminal Daemon timed out while handling {action}.",
                code="daemon_timeout",
            ) from exc

    async def list(
        self, project_id: str = "", *, owner_chat_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"projectId": project_id}
        if owner_chat_id is not None:
            payload["ownerChatId"] = owner_chat_id
        return await self._request("list", request_timeout=5.0, **payload)

    async def create(
        self, project_id: str, *, title: str = "", cwd: str = "",
        cols: int = 100, rows: int = 30, ssh_target: str = "",
        remote_cwd: str = "", tmux_session: str = "",
    ) -> dict[str, Any]:
        from .manager import TerminalManager

        requested_cwd = str(cwd or "").strip()
        project_cwd = str(TerminalManager._resolve_cwd(project_id, ""))
        resolved_cwd = (
            str(TerminalManager._resolve_cwd(project_id, requested_cwd))
            if requested_cwd
            else ""
        )
        from .shell_runtime import interactive_argv

        shell, argv = interactive_argv()
        payload: dict[str, Any] = dict(
            projectId=project_id, title=title, cwd=resolved_cwd,
            defaultCwd=project_cwd,
            shell=shell, argv=list(argv), cols=cols, rows=rows,
            createdBy="user", launchMode="interactive", activate=True,
        )
        if str(ssh_target or "").strip():
            payload.update({
                "sshTarget": str(ssh_target).strip(),
                "remoteCwd": str(remote_cwd or ""),
                "tmuxSession": str(tmux_session or ""),
            })
        return await self._request(
            "create", **payload,
        )

    async def create_agent_terminal(
        self,
        project_id: str,
        *,
        owner_chat_id: str,
        title: str = "",
        cwd: str = "",
        command: str = "",
        wake_on_exit: bool = False,
        wake_note: str = "",
        owner_tool_call_id: str = "",
        cols: int = 100,
        rows: int = 30,
        ssh_target: str = "",
        remote_cwd: str = "",
        tmux_session: str = "",
    ) -> dict[str, Any]:
        from .manager import TerminalManager

        resolved = TerminalManager._resolve_cwd(project_id, cwd)
        managed_ssh = bool(str(ssh_target or "").strip())
        one_shot = bool(
            not managed_ssh and wake_on_exit and str(command or "").strip()
        )
        from . import shell_runtime
        if one_shot:
            shell, _executable = shell_runtime.resolve_shell(unix_fallback="/bin/bash")
            argv = shell_runtime.command_argv(str(command))
            launch_mode = "one_shot"
        else:
            shell, argv = shell_runtime.interactive_argv()
            launch_mode = "interactive"
        payload: dict[str, Any] = dict(
            projectId=project_id,
            title=title,
            cwd=str(resolved),
            shell=shell,
            argv=list(argv),
            cols=cols,
            rows=rows,
            ownerChatId=owner_chat_id,
            createdBy="agent",
            ownerToolCallId=owner_tool_call_id,
            launchMode=launch_mode,
            wakeOnExit=wake_on_exit,
            wakeNote=wake_note,
            activate=False,
        )
        if managed_ssh:
            payload.update({
                "sshTarget": str(ssh_target).strip(),
                "remoteCwd": str(remote_cwd or ""),
                "tmuxSession": str(tmux_session or ""),
            })
        result = await self._request(
            "create",
            **payload,
        )
        terminal = dict(result.get("terminal") or {})
        if str(command or "").strip() and not one_shot:
            if managed_ssh:
                terminal_id = str(terminal.get("id") or "")
                try:
                    await self.wait_until_connected(terminal_id)
                except TerminalRequestError as exc:
                    raise TerminalRequestError(
                        f"Managed SSH terminal {terminal_id} remains open, but its initial "
                        f"command was not sent: {exc}",
                        code=exc.code,
                    ) from exc
            written = await self.input(
                str(terminal.get("id") or ""),
                str(command).rstrip("\n") + "\n",
            )
            if written.get("terminal"):
                result["terminal"] = written["terminal"]
        return result

    async def screen(self, terminal_id: str) -> dict[str, Any]:
        return await self._request(
            "screen", terminalId=terminal_id, request_timeout=5.0,
        )

    async def scrollback(
        self,
        terminal_id: str,
        *,
        cursor: int | None = None,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "terminalId": terminal_id,
            "maxBytes": max(1, min(int(max_bytes or 64 * 1024), 512 * 1024)),
        }
        if cursor is not None:
            payload["cursor"] = max(0, int(cursor))
        return await self._request("scrollback", request_timeout=5.0, **payload)

    async def input_history(
        self, terminal_id: str, *, limit: int = 200,
    ) -> dict[str, Any]:
        return await self._request(
            "inputHistory", terminalId=terminal_id,
            limit=max(1, min(int(limit or 200), 1000)),
        )

    async def search_history(
        self,
        project_id: str,
        query: str,
        *,
        terminal_id: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self._request(
            "historySearch",
            projectId=project_id,
            query=query,
            terminalId=terminal_id,
            limit=max(1, min(int(limit or 100), 500)),
        )

    async def commands(self, terminal_id: str) -> dict[str, Any]:
        return await self._request("commands", terminalId=terminal_id)

    async def command_output(
        self, terminal_id: str, command_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "commandOutput", terminalId=terminal_id, commandId=command_id,
        )

    async def input(
        self, terminal_id: str, data: str, *, actor: str = "agent",
    ) -> dict[str, Any]:
        return await self._request(
            "input", terminalId=terminal_id, data=data, actor=actor,
        )

    async def wait_until_connected(
        self, terminal_id: str, *, timeout: float = 300.0,
    ) -> dict[str, Any]:
        bounded = max(0.1, min(float(timeout), 900.0))
        return await self._request(
            "waitConnected",
            terminalId=terminal_id,
            timeoutSeconds=bounded,
            request_timeout=bounded + 5.0,
        )

    async def interrupt(self, terminal_id: str) -> dict[str, Any]:
        return await self._request("interrupt", terminalId=terminal_id)

    async def restart(self, terminal_id: str) -> dict[str, Any]:
        return await self._request(
            "restart", terminalId=terminal_id, reason="user_restart"
        )

    async def wake_info(self, terminal_id: str) -> dict[str, Any]:
        return await self._request("wakeInfo", terminalId=terminal_id)

    async def claim_wake(
        self, consumer_id: str, *, lease_seconds: float = 30.0,
    ) -> dict[str, Any]:
        return await self._request(
            "claimWake", consumerId=consumer_id, leaseSeconds=lease_seconds,
        )

    async def settle_wake(
        self, wake_id: str, lease_token: str, outcome: str,
    ) -> dict[str, Any]:
        return await self._request(
            "settleWake", wakeId=wake_id, leaseToken=lease_token, outcome=outcome,
        )

    async def rename(self, terminal_id: str, title: str) -> dict[str, Any]:
        return await self._request("rename", terminalId=terminal_id, title=title)

    async def remove(self, terminal_id: str) -> dict[str, Any]:
        return await self._request("delete", terminalId=terminal_id)

    async def update_layout(
        self, project_id: str, order: list[str], pinned: list[str]
    ) -> dict[str, Any]:
        return await self._request(
            "layout", projectId=project_id, order=order, pinned=pinned
        )

    async def activate(self, project_id: str, terminal_id: str | None) -> dict[str, Any]:
        return await self._request(
            "activate", projectId=project_id, terminalId=terminal_id
        )

    async def connect_terminal(
        self, terminal_id: str, cursor: int = 0
    ) -> tuple[TerminalDaemonConnection, dict[str, Any]]:
        connection = await self._open()
        info = self._connection_info() or {}
        await connection.send({
            "version": PROTOCOL_VERSION,
            "token": info.get("token"),
            "action": "subscribe",
            "terminalId": terminal_id,
            "cursor": max(0, int(cursor)),
        })
        try:
            first = await asyncio.wait_for(connection.read(), timeout=8.0)
        except TimeoutError as exc:
            await connection.close()
            raise TerminalRequestError(
                "Terminal Daemon timed out while starting the terminal replay.",
                code="daemon_timeout",
            ) from exc
        if first.get("type") == "error":
            await connection.close()
            if first.get("code") == "not_found":
                raise TerminalNotFoundError(str(first.get("error") or "terminal not found"))
            raise TerminalRequestError(str(first.get("error") or "subscription failed"))
        return connection, first


_CLIENT = TerminalDaemonClient()


def get_terminal_daemon_client() -> TerminalDaemonClient:
    return _CLIENT


__all__ = [
    "LIFECYCLE_VERSION", "TerminalDaemonClient", "TerminalDaemonConnection",
    "TerminalNotFoundError", "TerminalRequestError", "get_terminal_daemon_client",
    "terminal_state_dir",
]
