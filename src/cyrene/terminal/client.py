"""Client and lifecycle bootstrap for the local Cyrene Terminal Daemon."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cyrene.runtime.paths import user_data_dir
from cyrene.tooling.backends.shell_runtime import interactive_argv

from .manager import TerminalManager


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


def terminal_state_dir() -> Path:
    override = str(os.environ.get("CYRENE_TERMINAL_STATE_DIR") or "").strip()
    return Path(override).expanduser().resolve() if override else user_data_dir() / "terminal-daemon"


class TerminalRequestError(RuntimeError):
    pass


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

    @property
    def connection_path(self) -> Path:
        return self.state_dir / "connection.json"

    def _connection_info(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.connection_path.read_text(encoding="utf-8"))
            if int(payload.get("version") or 0) != PROTOCOL_VERSION:
                return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    async def _open(self, *, start: bool = True) -> TerminalDaemonConnection:
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
        self._start_daemon()
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

    def _start_daemon(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["CYRENE_TERMINAL_STATE_DIR"] = str(self.state_dir)
        if not getattr(sys, "frozen", False):
            source_dir = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = os.pathsep.join(
                part for part in (source_dir, env.get("PYTHONPATH", "")) if part
            )
        command = (
            [sys.executable, "--launch-terminal-daemon"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "cyrene.terminal.daemon"]
        )
        log = (self.state_dir / "daemon.log").open("ab")
        flags = 0
        if sys.platform == "win32":  # pragma: no cover - Windows only
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
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

    async def _request(self, action: str, **payload: Any) -> dict[str, Any]:
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
            raise TerminalRequestError(message)
        return response

    async def list(self, project_id: str = "") -> dict[str, Any]:
        return await self._request("list", projectId=project_id)

    async def create(
        self, project_id: str, *, title: str = "", cwd: str = "",
        cols: int = 100, rows: int = 30,
    ) -> dict[str, Any]:
        resolved = TerminalManager._resolve_cwd(project_id, cwd)
        shell, argv = interactive_argv()
        return await self._request(
            "create", projectId=project_id, title=title, cwd=str(resolved),
            shell=shell, argv=list(argv), cols=cols, rows=rows,
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
        first = await connection.read()
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
    "TerminalDaemonClient", "TerminalDaemonConnection", "TerminalNotFoundError",
    "TerminalRequestError", "get_terminal_daemon_client", "terminal_state_dir",
]
