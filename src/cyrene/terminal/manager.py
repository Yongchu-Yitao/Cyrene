"""Cross-platform PTY session manager for Workbench terminals."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import signal
import sqlite3
import struct
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cyrene.tooling.backends.shell_runtime import interactive_argv
from cyrene.workbench.app_services import read_project


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _terminal_id() -> str:
    return "term_" + uuid.uuid4().hex[:12]


def _terminal_environment() -> dict[str, str]:
    """Build a color-capable environment isolated from the host launcher."""
    env = dict(os.environ)
    # Desktop/dev launchers are often automation processes that deliberately
    # disable color for their own logs. Those flags must not leak into an
    # interactive PTY, where they make Claude, Codex, Rich, and other TUIs
    # suppress ANSI color even though the terminal advertises truecolor.
    for name in ("NO_COLOR", "FORCE_COLOR", "CLICOLOR_FORCE"):
        env.pop(name, None)
    env.update(
        {
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "TERM_PROGRAM": "Cyrene",
            "CLICOLOR": "1",
        }
    )
    return env


@dataclass(slots=True)
class OutputChunk:
    start: int
    end: int
    data: bytes


@dataclass(slots=True)
class TerminalSession:
    id: str
    project_id: str
    title: str
    cwd: str
    shell: str
    argv: list[str]
    created_at: str
    updated_at: str
    status: str = "starting"
    exit_code: int | None = None
    pid: int | None = None
    cols: int = 100
    rows: int = 30
    next_seq: int = 0
    output_start_seq: int = 0
    order_index: int = 0
    pinned: bool = False
    output_bytes: int = 0
    output: deque[OutputChunk] = field(default_factory=deque)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    process: asyncio.subprocess.Process | None = None
    master_fd: int | None = None
    winpty: Any = None
    wait_task: asyncio.Task[Any] | None = None
    read_task: asyncio.Task[Any] | None = None
    closing: bool = False

    def public(self) -> dict[str, Any]:
        oldest_seq = self.output[0].start if self.output else self.next_seq
        return {
            "id": self.id,
            "projectId": self.project_id,
            "title": self.title,
            "cwd": self.cwd,
            "shell": self.shell,
            "status": self.status,
            "exitCode": self.exit_code,
            "pid": self.pid,
            "cols": self.cols,
            "rows": self.rows,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "oldestSeq": oldest_seq,
            "nextSeq": self.next_seq,
            "orderIndex": self.order_index,
            "pinned": self.pinned,
        }


class TerminalManager:
    """Own PTY processes independently from any individual UI attachment."""

    def __init__(
        self,
        *,
        output_limit: int = 2 * 1024 * 1024,
        state_dir: Path | None = None,
    ) -> None:
        self.output_limit = max(64 * 1024, int(output_limit))
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self.state_dir = Path(state_dir).resolve() if state_dir else None
        self._db: sqlite3.Connection | None = None
        if self.state_dir is not None:
            self._open_store()

    def _open_store(self) -> None:
        assert self.state_dir is not None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "scrollback").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.state_dir / "terminals.sqlite3")
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS terminal_sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                cwd TEXT NOT NULL,
                shell TEXT NOT NULL,
                argv_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                pid INTEGER,
                cols INTEGER NOT NULL,
                rows INTEGER NOT NULL,
                next_seq INTEGER NOT NULL DEFAULT 0,
                output_start_seq INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS terminal_projects (
                project_id TEXT PRIMARY KEY,
                active_terminal_id TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._db.commit()
        self._load_sessions()

    def _scroll_path(self, terminal_id: str) -> Path:
        assert self.state_dir is not None
        return self.state_dir / "scrollback" / f"{terminal_id}.bin"

    def _load_sessions(self) -> None:
        import json

        assert self._db is not None
        for row in self._db.execute("SELECT * FROM terminal_sessions"):
            status = str(row["status"])
            if status in {"starting", "running"}:
                status = "exited"
            session = TerminalSession(
                id=str(row["id"]),
                project_id=str(row["project_id"]),
                title=str(row["title"]),
                cwd=str(row["cwd"]),
                shell=str(row["shell"]),
                argv=list(json.loads(str(row["argv_json"]))),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                status=status,
                exit_code=row["exit_code"],
                pid=None,
                cols=int(row["cols"]),
                rows=int(row["rows"]),
                next_seq=int(row["next_seq"]),
                output_start_seq=int(row["output_start_seq"]),
                order_index=int(row["order_index"]),
                pinned=bool(row["pinned"]),
            )
            if self.state_dir is not None:
                with contextlib.suppress(OSError):
                    path = self._scroll_path(session.id)
                    with path.open("rb") as stream:
                        size = path.stat().st_size
                        if size > self.output_limit:
                            stream.seek(-self.output_limit, os.SEEK_END)
                        data = stream.read()
                    if data:
                        start = max(0, session.next_seq - len(data))
                        session.output.append(OutputChunk(start, session.next_seq, data))
                        session.output_bytes = len(data)
                        session.output_start_seq = start
            self._sessions[session.id] = session
            if status != row["status"] or row["pid"] is not None:
                self._persist_session(session)

    def _persist_session(self, session: TerminalSession) -> None:
        import json

        if self._db is None:
            return
        self._db.execute(
            """
            INSERT INTO terminal_sessions (
                id, project_id, title, cwd, shell, argv_json, created_at,
                updated_at, status, exit_code, pid, cols, rows, next_seq,
                output_start_seq, order_index, pinned
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_id=excluded.project_id, title=excluded.title,
                cwd=excluded.cwd, shell=excluded.shell, argv_json=excluded.argv_json,
                updated_at=excluded.updated_at, status=excluded.status,
                exit_code=excluded.exit_code, pid=excluded.pid, cols=excluded.cols,
                rows=excluded.rows, next_seq=excluded.next_seq,
                output_start_seq=excluded.output_start_seq,
                order_index=excluded.order_index, pinned=excluded.pinned
            """,
            (
                session.id, session.project_id, session.title, session.cwd,
                session.shell, json.dumps(session.argv), session.created_at,
                session.updated_at, session.status, session.exit_code, session.pid,
                session.cols, session.rows, session.next_seq,
                session.output_start_seq, session.order_index, int(session.pinned),
            ),
        )
        self._db.commit()

    @staticmethod
    def _project_workspace(project_id: str) -> Path:
        project = read_project(project_id)
        raw = str(project.get("workspacePath") or "").strip()
        if not raw:
            raise ValueError("project workspace is unavailable")
        root = Path(raw).expanduser().resolve(strict=False)
        if not root.is_dir():
            raise ValueError("project workspace does not exist")
        return root

    @classmethod
    def _resolve_cwd(cls, project_id: str, cwd: str = "") -> Path:
        root = cls._project_workspace(project_id)
        raw = str(cwd or "").strip()
        candidate = Path(raw).expanduser() if raw else root
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        if not candidate.is_relative_to(root):
            raise ValueError("terminal cwd must stay inside the project workspace")
        if not candidate.is_dir():
            raise ValueError("terminal cwd does not exist")
        return candidate

    async def create(
        self,
        project_id: str,
        *,
        title: str = "",
        cwd: str = "",
        cols: int = 100,
        rows: int = 30,
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        if not project_id:
            raise ValueError("projectId is required")
        resolved_cwd = self._resolve_cwd(project_id, cwd)
        kind, argv = interactive_argv()
        return await self.create_resolved(
            project_id,
            cwd=str(resolved_cwd),
            shell=kind,
            argv=list(argv),
            title=title,
            cols=cols,
            rows=rows,
        )

    async def create_resolved(
        self,
        project_id: str,
        *,
        cwd: str,
        shell: str,
        argv: list[str],
        title: str = "",
        cols: int = 100,
        rows: int = 30,
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        resolved_cwd = Path(cwd).expanduser().resolve(strict=False)
        if not project_id:
            raise ValueError("projectId is required")
        if not resolved_cwd.is_dir():
            raise ValueError("terminal cwd does not exist")
        if not argv:
            raise ValueError("terminal command is required")
        now = _now_iso()
        project_sessions = [
            current for current in self._sessions.values()
            if current.project_id == project_id
        ]
        existing_count = len(project_sessions)
        next_order_index = max(
            (current.order_index for current in project_sessions), default=-1
        ) + 1
        session = TerminalSession(
            id=_terminal_id(),
            project_id=project_id,
            title=str(title or "").strip()[:60] or f"Terminal {existing_count + 1}",
            cwd=str(resolved_cwd),
            shell=str(shell),
            argv=[str(part) for part in argv],
            created_at=now,
            updated_at=now,
            cols=max(20, min(400, int(cols or 100))),
            rows=max(5, min(200, int(rows or 30))),
            order_index=next_order_index,
        )
        async with self._lock:
            self._sessions[session.id] = session
            self._persist_session(session)
        try:
            if sys.platform == "win32":
                await self._spawn_windows(session)
            else:
                await self._spawn_posix(session)
        except Exception:
            async with self._lock:
                self._sessions.pop(session.id, None)
                if self._db is not None:
                    self._db.execute("DELETE FROM terminal_sessions WHERE id = ?", (session.id,))
                    self._db.commit()
            raise
        return session.public()

    async def _spawn_posix(self, session: TerminalSession) -> None:
        import fcntl
        import pty
        import termios

        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack("HHHH", session.rows, session.cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        os.set_blocking(master_fd, False)
        env = _terminal_environment()

        def child_setup() -> None:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        try:
            process = await asyncio.create_subprocess_exec(
                *session.argv,
                cwd=session.cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=child_setup,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        session.process = process
        session.master_fd = master_fd
        session.pid = process.pid
        session.status = "running"
        session.updated_at = _now_iso()
        self._persist_session(session)
        loop = asyncio.get_running_loop()
        loop.add_reader(master_fd, self._read_posix_ready, session.id)
        session.wait_task = asyncio.create_task(self._wait_posix(session.id))
        self._publish_state(session)

    async def _spawn_windows(self, session: TerminalSession) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # pragma: no cover - Windows packaging guard
            raise RuntimeError("pywinpty is required for terminal support on Windows") from exc

        env = _terminal_environment()
        command = session.argv
        process = await asyncio.to_thread(
            PtyProcess.spawn,
            command,
            cwd=session.cwd,
            env=env,
            dimensions=(session.rows, session.cols),
        )
        session.winpty = process
        session.pid = getattr(process, "pid", None)
        session.status = "running"
        session.updated_at = _now_iso()
        self._persist_session(session)
        session.read_task = asyncio.create_task(self._read_windows(session.id))
        self._publish_state(session)

    def _read_posix_ready(self, terminal_id: str) -> None:
        session = self._sessions.get(terminal_id)
        if not session or session.master_fd is None:
            return
        while True:
            try:
                data = os.read(session.master_fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                return
            if not data:
                return
            self._append_output(session, data)

    async def _read_windows(self, terminal_id: str) -> None:  # pragma: no cover - Windows only
        session = self._sessions.get(terminal_id)
        if not session or session.winpty is None:
            return
        try:
            while session.winpty.isalive():
                try:
                    text = await asyncio.to_thread(session.winpty.read, 4096)
                except EOFError:
                    break
                if text:
                    self._append_output(session, str(text).encode("utf-8", errors="replace"))
            exit_code = await asyncio.to_thread(session.winpty.wait)
        except asyncio.CancelledError:
            raise
        except Exception:
            exit_code = None
        self._mark_exited(session, exit_code)

    async def _wait_posix(self, terminal_id: str) -> None:
        session = self._sessions.get(terminal_id)
        if not session or session.process is None:
            return
        exit_code = await session.process.wait()
        if session.master_fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(session.master_fd)
            self._read_posix_ready(terminal_id)
            with contextlib.suppress(OSError):
                os.close(session.master_fd)
            session.master_fd = None
        self._mark_exited(session, exit_code)

    def _mark_exited(self, session: TerminalSession, exit_code: int | None) -> None:
        session.exit_code = exit_code
        session.status = "closed" if session.closing else "exited"
        session.pid = None
        session.updated_at = _now_iso()
        self._persist_session(session)
        self._publish_state(session)

    def _append_output(self, session: TerminalSession, data: bytes) -> None:
        if not data:
            return
        start = session.next_seq
        end = start + len(data)
        session.next_seq = end
        session.output.append(OutputChunk(start=start, end=end, data=data))
        session.output_bytes += len(data)
        while session.output and session.output_bytes > self.output_limit:
            overflow = session.output_bytes - self.output_limit
            oldest = session.output[0]
            if len(oldest.data) <= overflow:
                session.output.popleft()
                session.output_bytes -= len(oldest.data)
            else:
                session.output[0] = OutputChunk(
                    oldest.start + overflow,
                    oldest.end,
                    oldest.data[overflow:],
                )
                session.output_bytes -= overflow
        session.output_start_seq = session.output[0].start if session.output else session.next_seq
        if self.state_dir is not None:
            path = self._scroll_path(session.id)
            with path.open("ab") as stream:
                stream.write(data)
            trim_threshold = self.output_limit + max(256 * 1024, self.output_limit // 4)
            if path.stat().st_size > trim_threshold:
                with path.open("rb") as stream:
                    stream.seek(-self.output_limit, os.SEEK_END)
                    retained = stream.read()
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(retained)
                temporary.replace(path)
        session.updated_at = _now_iso()
        self._persist_session(session)
        event = {
            "type": "output",
            "seq": start,
            "nextSeq": end,
            "data": base64.b64encode(data).decode("ascii"),
        }
        self._publish(session, event)

    def _publish_state(self, session: TerminalSession) -> None:
        self._publish(session, {"type": "state", "terminal": session.public()})

    @staticmethod
    def _publish(session: TerminalSession, event: dict[str, Any]) -> None:
        for queue in tuple(session.subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def list(self, project_id: str = "") -> list[dict[str, Any]]:
        project_id = str(project_id or "")
        sessions = [
            session.public()
            for session in self._sessions.values()
            if not project_id or session.project_id == project_id
        ]
        sessions.sort(key=lambda item: (not bool(item.get("pinned")), int(item.get("orderIndex") or 0)))
        return sessions

    def get(self, terminal_id: str) -> TerminalSession:
        session = self._sessions.get(str(terminal_id or ""))
        if not session:
            raise LookupError("terminal not found")
        return session

    def rename(self, terminal_id: str, title: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        normalized = str(title or "").strip()[:60]
        if not normalized:
            raise ValueError("terminal title is required")
        session.title = normalized
        session.updated_at = _now_iso()
        self._persist_session(session)
        self._publish_state(session)
        return session.public()

    def replay(self, terminal_id: str, cursor: int = 0) -> list[dict[str, Any]]:
        session = self.get(terminal_id)
        cursor = max(0, int(cursor or 0))
        return [
            {
                "type": "output",
                "seq": max(cursor, chunk.start),
                "nextSeq": chunk.end,
                "data": base64.b64encode(
                    chunk.data[max(0, cursor - chunk.start):]
                ).decode("ascii"),
            }
            for chunk in session.output
            if chunk.end > cursor
        ]

    def subscribe(self, terminal_id: str) -> asyncio.Queue[dict[str, Any]]:
        session = self.get(terminal_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        session.subscribers.add(queue)
        return queue

    def unsubscribe(self, terminal_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        session = self._sessions.get(terminal_id)
        if session:
            session.subscribers.discard(queue)

    async def write(self, terminal_id: str, data: str) -> None:
        await self.write_bytes(
            terminal_id,
            str(data or "").encode("utf-8"),
            binary=False,
        )

    async def write_bytes(
        self,
        terminal_id: str,
        data: bytes,
        *,
        binary: bool = False,
    ) -> None:
        session = self.get(terminal_id)
        if session.status != "running":
            raise RuntimeError("terminal is not running")
        encoded = bytes(data or b"")
        if not encoded:
            return
        if sys.platform == "win32":  # pragma: no cover - Windows only
            text = encoded.decode("latin-1" if binary else "utf-8", errors="replace")
            await asyncio.to_thread(session.winpty.write, text)
            return
        if session.master_fd is None:
            raise RuntimeError("terminal is unavailable")
        offset = 0
        while offset < len(encoded):
            try:
                offset += os.write(session.master_fd, encoded[offset:])
            except BlockingIOError:
                await asyncio.sleep(0)

    async def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        session = self.get(terminal_id)
        cols = max(20, min(400, int(cols or 0)))
        rows = max(5, min(200, int(rows or 0)))
        if cols == session.cols and rows == session.rows:
            return
        session.cols = cols
        session.rows = rows
        if sys.platform == "win32":  # pragma: no cover - Windows only
            if session.winpty is not None:
                await asyncio.to_thread(session.winpty.setwinsize, rows, cols)
        elif session.master_fd is not None:
            import fcntl
            import termios

            fcntl.ioctl(
                session.master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        session.updated_at = _now_iso()
        self._persist_session(session)

    def update_layout(
        self,
        project_id: str,
        order: list[str],
        pinned: list[str],
    ) -> list[dict[str, Any]]:
        project_id = str(project_id or "").strip()
        project_sessions = [s for s in self._sessions.values() if s.project_id == project_id]
        by_id = {s.id: s for s in project_sessions}
        normalized = [str(item) for item in order if str(item) in by_id]
        normalized.extend(s.id for s in project_sessions if s.id not in normalized)
        pinned_set = {str(item) for item in pinned if str(item) in by_id}
        for index, terminal_id in enumerate(normalized):
            session = by_id[terminal_id]
            session.order_index = index
            session.pinned = terminal_id in pinned_set
            self._persist_session(session)
        return self.list(project_id)

    def set_active(self, project_id: str, terminal_id: str | None) -> str | None:
        project_id = str(project_id or "").strip()
        normalized = str(terminal_id or "").strip() or None
        if normalized is not None:
            session = self.get(normalized)
            if session.project_id != project_id:
                raise LookupError("terminal not found")
        if self._db is not None:
            self._db.execute(
                """INSERT INTO terminal_projects (project_id, active_terminal_id, updated_at)
                   VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET
                   active_terminal_id=excluded.active_terminal_id,
                   updated_at=excluded.updated_at""",
                (project_id, normalized, _now_iso()),
            )
            self._db.commit()
        return normalized

    def active_terminal_id(self, project_id: str) -> str | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT active_terminal_id FROM terminal_projects WHERE project_id = ?",
            (str(project_id or ""),),
        ).fetchone()
        terminal_id = str(row[0] or "") if row else ""
        session = self._sessions.get(terminal_id)
        return terminal_id if session and session.project_id == project_id else None

    async def interrupt(self, terminal_id: str) -> None:
        session = self.get(terminal_id)
        if session.status != "running":
            return
        if sys.platform == "win32":  # pragma: no cover - Windows only
            await self.write(terminal_id, "\x03")
        elif session.pid:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(session.pid, signal.SIGINT)

    async def close(self, terminal_id: str, *, remove: bool = False) -> dict[str, Any]:
        session = self.get(terminal_id)
        session.closing = True
        if session.status == "running":
            if sys.platform == "win32":  # pragma: no cover - Windows only
                if session.winpty is not None:
                    await asyncio.to_thread(session.winpty.terminate, True)
            elif session.pid:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(session.pid, signal.SIGHUP)
                if session.process is not None:
                    try:
                        await asyncio.wait_for(session.process.wait(), timeout=1.5)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(session.pid, signal.SIGTERM)
                        try:
                            await asyncio.wait_for(session.process.wait(), timeout=1.0)
                        except TimeoutError:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(session.pid, signal.SIGKILL)
        if session.status == "running":
            self._mark_exited(session, session.exit_code)
        result = session.public()
        if remove:
            async with self._lock:
                self._sessions.pop(session.id, None)
                if self._db is not None:
                    self._db.execute("DELETE FROM terminal_sessions WHERE id = ?", (session.id,))
                    self._db.execute(
                        "UPDATE terminal_projects SET active_terminal_id = NULL WHERE active_terminal_id = ?",
                        (session.id,),
                    )
                    self._db.commit()
                if self.state_dir is not None:
                    with contextlib.suppress(OSError):
                        self._scroll_path(session.id).unlink()
        return result

    async def close_all(self) -> None:
        for terminal_id in list(self._sessions):
            with contextlib.suppress(Exception):
                await self.close(terminal_id, remove=True)


_MANAGER = TerminalManager()


def get_terminal_manager() -> TerminalManager:
    return _MANAGER
