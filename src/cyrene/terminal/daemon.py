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

from .client import (
    LIFECYCLE_VERSION,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    terminal_state_dir,
)
from .manager import TerminalInputBusyError, TerminalManager


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
    def __init__(self, state_dir: Path, *, stop_event: asyncio.Event | None = None) -> None:
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.start_reason = str(
            os.environ.get("CYRENE_TERMINAL_START_REASON") or "daemon_restart"
        )
        self.manager = TerminalManager(
            state_dir=self.state_dir, startup_reason=self.start_reason
        )
        self.server: asyncio.AbstractServer | None = None
        self.connections: set[asyncio.StreamWriter] = set()
        self.stop_event = stop_event

    async def start(self) -> None:
        await self.manager.restore_interrupted_sessions()
        self.server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_MESSAGE_BYTES
        )
        port = int(self.server.sockets[0].getsockname()[1])
        connection_path = self.state_dir / "connection.json"
        temporary = connection_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "version": PROTOCOL_VERSION,
            "lifecycleVersion": LIFECYCLE_VERSION,
            "pid": os.getpid(),
            "port": port,
            "token": self.token,
            "startedAt": self.manager.started_at,
            "startReason": self.start_reason,
        }), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(temporary, 0o600)
        temporary.replace(connection_path)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
        writers = list(self.connections)
        for writer in writers:
            writer.close()
            # A renderer may be suspended and never finish the TCP close
            # handshake.  Daemon shutdown must not wait on that view; aborting
            # the local transport releases the handler immediately.
            transport = writer.transport
            if transport is not None:
                transport.abort()
        if writers:
            await asyncio.sleep(0)
        if self.server is not None:
            await self.server.wait_closed()
            self.server = None
        self.manager.close_store()

    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections.add(writer)
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
            self.connections.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, writer: asyncio.StreamWriter, request: dict[str, Any]) -> None:
        action = str(request.get("action") or "")
        try:
            if action == "ping":
                payload: dict[str, Any] = {}
            elif action == "shutdown":
                payload = {"stopping": True}
            elif action == "list":
                project_id = str(request.get("projectId") or "")
                payload = {
                    "terminals": self.manager.list(
                        project_id,
                        owner_chat_id=(
                            str(request.get("ownerChatId") or "")
                            if request.get("ownerChatId") is not None else None
                        ),
                    ),
                    "activeTerminalId": self.manager.active_terminal_id(project_id),
                }
            elif action == "create":
                payload = {"terminal": await self._create_terminal(request)}
            elif action == "screen":
                payload = await self.manager.screen_snapshot_async(
                    str(request.get("terminalId") or "")
                )
            elif action == "scrollback":
                requested_cursor = request.get("cursor")
                payload = await self.manager.scrollback_snapshot_async(
                    str(request.get("terminalId") or ""),
                    cursor=(
                        int(requested_cursor)
                        if requested_cursor is not None else None
                    ),
                    max_bytes=int(request.get("maxBytes") or 64 * 1024),
                )
            elif action == "historySearch":
                payload = {"matches": await self.manager.search_history_async(
                    str(request.get("projectId") or ""),
                    str(request.get("query") or ""),
                    terminal_id=str(request.get("terminalId") or ""),
                    limit=int(request.get("limit") or 100),
                )}
            elif action == "commands":
                payload = {"commands": await self.manager.commands_async(
                    str(request.get("terminalId") or "")
                )}
            elif action == "commandOutput":
                payload = await self.manager.command_output_async(
                    str(request.get("terminalId") or ""),
                    str(request.get("commandId") or ""),
                )
            elif action == "inputHistory":
                payload = {
                    "events": self.manager.input_history(
                        str(request.get("terminalId") or ""),
                        limit=int(request.get("limit") or 200),
                    )
                }
            elif action == "input":
                data = str(request.get("data") or "")
                if request.get("encoding") == "base64":
                    decoded = base64.b64decode(data, validate=True)
                    await self.manager.write_bytes(
                        str(request.get("terminalId") or ""), decoded,
                        binary=bool(request.get("binary")),
                        actor=str(request.get("actor") or "agent"),
                    )
                else:
                    await self.manager.write(
                        str(request.get("terminalId") or ""), data,
                        actor=str(request.get("actor") or "agent"),
                    )
                payload = await self.manager.screen_snapshot_async(
                    str(request.get("terminalId") or "")
                )
            elif action == "waitConnected":
                payload = {"terminal": await self.manager.wait_until_connected(
                    str(request.get("terminalId") or ""),
                    timeout=float(request.get("timeoutSeconds") or 300),
                )}
            elif action == "interrupt":
                terminal_id = str(request.get("terminalId") or "")
                await self.manager.interrupt(terminal_id)
                payload = await self.manager.screen_snapshot_async(terminal_id)
            elif action == "restart":
                payload = {"terminal": await self.manager.restart(
                    str(request.get("terminalId") or ""),
                    reason=str(request.get("reason") or "user_restart"),
                )}
            elif action == "rename":
                payload = {"terminal": self.manager.rename(
                    str(request.get("terminalId") or ""), str(request.get("title") or "")
                )}
            elif action == "delete":
                terminal_id = str(request.get("terminalId") or "")
                wake = self.manager.wake_info(terminal_id)
                payload = {
                    "terminal": await self.manager.close(terminal_id, remove=True),
                    "deleted": True,
                    "wakeCancelled": bool(
                        wake and str(wake.get("status") or "") in {"watching", "ready", "claimed"}
                    ),
                }
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
            elif action == "wakeInfo":
                payload = {"wake": self.manager.wake_info(str(request.get("terminalId") or ""))}
            elif action == "claimWake":
                payload = {"wake": self.manager.claim_wake(
                    str(request.get("consumerId") or "web"),
                    float(request.get("leaseSeconds") or 30),
                )}
            elif action == "settleWake":
                payload = {"wake": self.manager.settle_wake(
                    str(request.get("wakeId") or ""),
                    str(request.get("leaseToken") or ""),
                    str(request.get("outcome") or "release"),
                )}
            else:
                raise ValueError("unknown terminal daemon action")
        except TerminalInputBusyError as exc:
            await self._send(writer, {
                "ok": False,
                "code": "input_busy",
                "error": str(exc),
                "retryAfterMs": exc.retry_after_ms,
            })
            return
        except LookupError as exc:
            await self._send(writer, {"ok": False, "code": "not_found", "error": str(exc)})
            return
        except RuntimeError as exc:
            await self._send(writer, {
                "ok": False, "code": "operation_failed", "error": str(exc),
            })
            return
        await self._send(writer, {"ok": True, **payload})
        if action == "shutdown" and self.stop_event is not None:
            self.stop_event.set()

    async def _create_terminal(self, request: dict[str, Any]) -> dict[str, Any]:
        project_id = str(request.get("projectId") or "")
        cwd = str(request.get("cwd") or "")
        if not cwd:
            active_id = self.manager.active_terminal_id(project_id)
            cwd = (
                self.manager.get(active_id).cwd
                if active_id
                else str(request.get("defaultCwd") or "")
            )
        ssh_target = str(request.get("sshTarget") or "").strip()
        if ssh_target:
            terminal = await self.manager.create_ssh(
                project_id,
                ssh_target=ssh_target,
                remote_cwd=str(request.get("remoteCwd") or ""),
                tmux_session=str(request.get("tmuxSession") or ""),
                cwd=cwd,
                title=str(request.get("title") or ""),
                cols=int(request.get("cols") or 100),
                rows=int(request.get("rows") or 30),
                owner_chat_id=str(request.get("ownerChatId") or ""),
                created_by=str(request.get("createdBy") or "user"),
                owner_tool_call_id=str(request.get("ownerToolCallId") or ""),
                wake_on_exit=bool(request.get("wakeOnExit")),
                wake_note=str(request.get("wakeNote") or ""),
            )
        else:
            terminal = await self.manager.create_resolved(
                project_id,
                cwd=cwd,
                shell=str(request.get("shell") or "shell"),
                argv=[str(part) for part in request.get("argv") or []],
                title=str(request.get("title") or ""),
                cols=int(request.get("cols") or 100),
                rows=int(request.get("rows") or 30),
                owner_chat_id=str(request.get("ownerChatId") or ""),
                created_by=str(request.get("createdBy") or "user"),
                owner_tool_call_id=str(request.get("ownerToolCallId") or ""),
                launch_mode=str(request.get("launchMode") or "interactive"),
                wake_on_exit=bool(request.get("wakeOnExit")),
                wake_note=str(request.get("wakeNote") or ""),
            )
        if bool(request.get("activate", True)):
            self.manager.set_active(terminal["projectId"], terminal["id"])
        return terminal

    async def _subscribe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, request: dict[str, Any],
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
            # Freeze the replay boundary before yielding. Live output produced
            # while the replay is sent is already queued by subscribe() and is
            # delivered afterwards without creating a gap.
            snapshot = session.public()
            replay_target = int(snapshot.get("nextSeq") or 0)
            replayed_through = min(
                max(cursor, int(snapshot.get("oldestSeq") or 0)), replay_target
            )
            await self._send(writer, {"type": "snapshot", "terminal": snapshot})
            for event in await self.manager.replay_async(
                terminal_id, cursor, end_seq=replay_target
            ):
                await self._send(writer, event)
                replayed_through = int(event.get("nextSeq") or replayed_through)
            # The old protocol inferred completion from the last output chunk.
            # If durable metadata and retained bytes differed, or the replay was
            # empty, the renderer could remain in "restoring" forever. This
            # explicit boundary makes completion deterministic.
            await self._send(writer, {
                "type": "replay_complete",
                "nextSeq": replay_target,
                "replayedThroughSeq": replayed_through,
                "truncated": replayed_through < replay_target,
            })

            send_lock = asyncio.Lock()

            async def send(message: dict[str, Any]) -> None:
                async with send_lock:
                    await self._send(writer, message)

            async def send_events() -> None:
                while True:
                    await send(await queue.get())

            async def receive_commands() -> None:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    message = dict(json.loads(line))
                    action = str(message.get("type") or "")
                    try:
                        if action == "input":
                            data = str(message.get("data") or "")
                            if message.get("encoding") == "base64":
                                try:
                                    decoded = base64.b64decode(data, validate=True)
                                except (binascii.Error, ValueError):
                                    continue
                                if len(decoded) <= 1024 * 1024:
                                    await self.manager.write_bytes(
                                        terminal_id, decoded, binary=bool(message.get("binary")),
                                        actor="user",
                                    )
                            else:
                                await self.manager.write(terminal_id, data, actor="user")
                        elif action == "resize":
                            await self.manager.resize(
                                terminal_id,
                                int(message.get("cols") or session.cols),
                                int(message.get("rows") or session.rows),
                            )
                        elif action == "interrupt":
                            await self.manager.interrupt(terminal_id)
                    except LookupError as exc:
                        await send({
                            "type": "error", "code": "not_found", "error": str(exc),
                        })
                        return
                    except RuntimeError as exc:
                        current = self.manager.get(terminal_id)
                        await send({
                            "type": "error",
                            "code": "terminal_not_running",
                            "error": str(exc),
                            "terminal": current.public(),
                        })

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
    stop = asyncio.Event()
    daemon = TerminalDaemon(state_dir, stop_event=stop)
    await daemon.start()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await stop.wait()
    finally:
        await daemon.stop()
        lock.close()
        connection_path = state_dir / "connection.json"
        with contextlib.suppress(OSError, ValueError, TypeError):
            current = json.loads(connection_path.read_text(encoding="utf-8"))
            if int(current.get("pid") or 0) == os.getpid():
                connection_path.unlink()


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
