"""Long-lived local process that owns Cyrene PTYs and terminal persistence."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import os
import secrets
import signal
from pathlib import Path
from typing import Any

from .client import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, terminal_state_dir
from .manager import TerminalManager


class AlreadyRunning(RuntimeError):
    pass


def _acquire_lock(state_dir: Path):
    path = state_dir / "daemon.lock"
    stream = path.open("a+b")
    try:
        if os.name == "nt":  # pragma: no cover - Windows only
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise AlreadyRunning from exc
    return stream


class TerminalDaemon:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.manager = TerminalManager(state_dir=self.state_dir)
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_MESSAGE_BYTES
        )
        port = int(self.server.sockets[0].getsockname()[1])
        connection_path = self.state_dir / "connection.json"
        temporary = connection_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "version": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "port": port,
            "token": self.token,
        }), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(temporary, 0o600)
        temporary.replace(connection_path)

    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            request = dict(json.loads(line))
            if request.get("token") != self.token or int(request.get("version") or 0) != PROTOCOL_VERSION:
                await self._send(writer, {"ok": False, "code": "unauthorized", "error": "unauthorized"})
                return
            if request.get("action") == "subscribe":
                await self._subscribe(reader, writer, request)
                return
            await self._dispatch(writer, request)
        except (ConnectionError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            with contextlib.suppress(Exception):
                await self._send(writer, {"ok": False, "code": "bad_request", "error": str(exc)})
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, writer: asyncio.StreamWriter, request: dict[str, Any]) -> None:
        action = str(request.get("action") or "")
        try:
            if action == "ping":
                payload: dict[str, Any] = {}
            elif action == "list":
                project_id = str(request.get("projectId") or "")
                payload = {
                    "terminals": self.manager.list(project_id),
                    "activeTerminalId": self.manager.active_terminal_id(project_id),
                }
            elif action == "create":
                terminal = await self.manager.create_resolved(
                    str(request.get("projectId") or ""),
                    cwd=str(request.get("cwd") or ""),
                    shell=str(request.get("shell") or "shell"),
                    argv=[str(part) for part in request.get("argv") or []],
                    title=str(request.get("title") or ""),
                    cols=int(request.get("cols") or 100),
                    rows=int(request.get("rows") or 30),
                )
                self.manager.set_active(terminal["projectId"], terminal["id"])
                payload = {"terminal": terminal}
            elif action == "rename":
                payload = {"terminal": self.manager.rename(
                    str(request.get("terminalId") or ""), str(request.get("title") or "")
                )}
            elif action == "delete":
                payload = {"terminal": await self.manager.close(
                    str(request.get("terminalId") or ""), remove=True
                ), "deleted": True}
            elif action == "layout":
                project_id = str(request.get("projectId") or "")
                payload = {"terminals": self.manager.update_layout(
                    project_id,
                    list(request.get("order") or []),
                    list(request.get("pinned") or []),
                )}
            elif action == "activate":
                payload = {"activeTerminalId": self.manager.set_active(
                    str(request.get("projectId") or ""), request.get("terminalId")
                )}
            else:
                raise ValueError("unknown terminal daemon action")
        except LookupError as exc:
            await self._send(writer, {"ok": False, "code": "not_found", "error": str(exc)})
            return
        await self._send(writer, {"ok": True, **payload})

    async def _subscribe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        request: dict[str, Any],
    ) -> None:
        terminal_id = str(request.get("terminalId") or "")
        try:
            session = self.manager.get(terminal_id)
        except LookupError as exc:
            await self._send(writer, {"type": "error", "code": "not_found", "error": str(exc)})
            return
        cursor = max(0, int(request.get("cursor") or 0))
        queue = self.manager.subscribe(terminal_id)
        try:
            await self._send(writer, {"type": "snapshot", "terminal": session.public()})
            for event in self.manager.replay(terminal_id, cursor):
                await self._send(writer, event)

            async def send_events() -> None:
                while True:
                    await self._send(writer, await queue.get())

            async def receive_commands() -> None:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    message = dict(json.loads(line))
                    action = str(message.get("type") or "")
                    if action == "input":
                        data = str(message.get("data") or "")
                        if message.get("encoding") == "base64":
                            try:
                                decoded = base64.b64decode(data, validate=True)
                            except (binascii.Error, ValueError):
                                continue
                            if len(decoded) <= 1024 * 1024:
                                await self.manager.write_bytes(
                                    terminal_id, decoded, binary=bool(message.get("binary"))
                                )
                        else:
                            await self.manager.write(terminal_id, data)
                    elif action == "resize":
                        await self.manager.resize(
                            terminal_id,
                            int(message.get("cols") or session.cols),
                            int(message.get("rows") or session.rows),
                        )
                    elif action == "interrupt":
                        await self.manager.interrupt(terminal_id)

            sender = asyncio.create_task(send_events())
            receiver = asyncio.create_task(receive_commands())
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            self.manager.unsubscribe(terminal_id, queue)


async def run_daemon() -> None:
    state_dir = terminal_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(state_dir, 0o700)
    try:
        lock = _acquire_lock(state_dir)
    except AlreadyRunning:
        return
    daemon = TerminalDaemon(state_dir)
    await daemon.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await stop.wait()
    finally:
        if daemon.server is not None:
            daemon.server.close()
            await daemon.server.wait_closed()
        with contextlib.suppress(OSError):
            (state_dir / "connection.json").unlink()
        lock.close()


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
