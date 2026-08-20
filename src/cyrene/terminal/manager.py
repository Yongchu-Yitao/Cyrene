"""Cross-platform PTY session manager for Workbench terminals."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import signal
import sqlite3
import struct
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyte


DEFAULT_OUTPUT_LIMIT = 16 * 1024 * 1024
USER_INPUT_PRIORITY_SECONDS = 2.0
INPUT_AUDIT_RETAINED_EVENTS = 10_000
DEFAULT_UTF8_LOCALE = "C.UTF-8"
_DEFAULT_TITLE_RE = re.compile(r"^Terminal\s+(\d+)$", re.IGNORECASE)


class TerminalInputBusyError(RuntimeError):
    """Raised when Agent input would race with active user typing."""

    def __init__(self, retry_after_ms: int) -> None:
        self.retry_after_ms = max(1, int(retry_after_ms))
        super().__init__(
            f"user input has priority; retry terminal input after {self.retry_after_ms} ms"
        )



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
    # Electron applications launched from Finder/Dock often do not inherit a
    # locale, while automation launchers may explicitly set LC_ALL=C. OpenSSH
    # forwards LANG/LC_* by default; without a UTF-8 LC_CTYPE the remote shell
    # treats non-ASCII filenames as raw bytes and GNU ls renders Chinese names
    # as $'\\345\\205…'. Preserve an explicit UTF-8 locale when available and
    # otherwise use C.UTF-8, which is language-neutral and supported by modern
    # macOS and Linux hosts. LC_ALL must be removed because it overrides both
    # LANG and LC_CTYPE.
    utf8_locale = next(
        (
            value
            for name in ("LC_ALL", "LC_CTYPE", "LANG")
            if (value := str(env.get(name) or "").strip())
            and ("utf-8" in value.casefold() or "utf8" in value.casefold())
            and value.casefold() != "utf-8"
        ),
        DEFAULT_UTF8_LOCALE,
    )
    env.pop("LC_ALL", None)
    env.update(
        {
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "TERM_PROGRAM": "Cyrene",
            "CLICOLOR": "1",
            "LANG": utf8_locale,
            "LC_CTYPE": utf8_locale,
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
    owner_chat_id: str = ""
    created_by: str = "user"
    owner_tool_call_id: str = ""
    launch_mode: str = "interactive"
    wake_id: str = ""
    output_bytes: int = 0
    output: deque[OutputChunk] = field(default_factory=deque)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    process: asyncio.subprocess.Process | None = None
    master_fd: int | None = None
    winpty: Any = None
    wait_task: asyncio.Task[Any] | None = None
    read_task: asyncio.Task[Any] | None = None
    closing: bool = False
    screen: Any = None
    stream: Any = None
    last_user_input_at: float = 0.0
    last_actor: str = ""
    last_input_at: str = ""
    input_event_count: int = 0
    input_events: deque[dict[str, Any]] = field(default_factory=deque)
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    recovery_pending: bool = False
    exit_reason: str = ""
    exit_at: str = ""
    recovery_reason: str = ""
    recovered_at: str = ""
    recovery_count: int = 0

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
            "ownerChatId": self.owner_chat_id,
            "createdBy": self.created_by,
            "ownerToolCallId": self.owner_tool_call_id,
            "launchMode": self.launch_mode,
            "wakeId": self.wake_id,
            "lastActor": self.last_actor,
            "lastInputAt": self.last_input_at,
            "inputEventCount": self.input_event_count,
            "exitReason": self.exit_reason,
            "exitAt": self.exit_at,
            "recoverable": self.launch_mode == "interactive" and self.status == "exited",
            "recoveryReason": self.recovery_reason,
            "recoveredAt": self.recovered_at,
            "recoveryCount": self.recovery_count,
        }


class TerminalManager:
    """Own PTY processes independently from any individual UI attachment."""

    def __init__(
        self,
        *,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
        state_dir: Path | None = None,
        user_input_priority_seconds: float = USER_INPUT_PRIORITY_SECONDS,
        startup_reason: str = "daemon_restart",
    ) -> None:
        self.output_limit = max(64 * 1024, int(output_limit))
        self.user_input_priority_seconds = max(
            0.01, float(user_input_priority_seconds)
        )
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self.startup_reason = str(startup_reason or "daemon_restart")
        self.started_at = _now_iso()
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
            CREATE TABLE IF NOT EXISTS terminal_wakes (
                wake_id TEXT PRIMARY KEY,
                terminal_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                exit_status TEXT NOT NULL DEFAULT '',
                exit_code INTEGER,
                final_screen TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                lease_token TEXT NOT NULL DEFAULT '',
                lease_until REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                ready_at TEXT NOT NULL DEFAULT '',
                delivered_at TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS terminal_input_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                input_kind TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_input_events_terminal
                ON terminal_input_events(terminal_id, event_id);
            """
        )
        self._ensure_column("terminal_sessions", "owner_chat_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "created_by", "TEXT NOT NULL DEFAULT 'user'")
        self._ensure_column("terminal_sessions", "owner_tool_call_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "launch_mode", "TEXT NOT NULL DEFAULT 'interactive'")
        self._ensure_column("terminal_sessions", "wake_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "last_input_actor", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "last_input_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "input_event_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("terminal_sessions", "exit_reason", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "exit_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "recovery_reason", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "recovered_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "recovery_count", "INTEGER NOT NULL DEFAULT 0")
        self._db.commit()
        self._load_sessions()

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        assert self._db is not None
        columns = {str(row[1]) for row in self._db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _scroll_path(self, terminal_id: str) -> Path:
        assert self.state_dir is not None
        return self.state_dir / "scrollback" / f"{terminal_id}.bin"

    def _load_sessions(self) -> None:
        import json

        assert self._db is not None
        for row in self._db.execute("SELECT * FROM terminal_sessions"):
            stored_status = str(row["status"])
            launch_mode = str(row["launch_mode"] or "interactive")
            stored_exit_reason = str(row["exit_reason"] or "")
            recovery_pending = (
                launch_mode == "interactive"
                and (
                    stored_status in {"starting", "running"}
                    or (
                        stored_status == "exited"
                        and row["exit_code"] is None
                        and stored_exit_reason == "daemon_interrupted"
                    )
                )
            )
            status = stored_status
            if stored_status in {"starting", "running"}:
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
                owner_chat_id=str(row["owner_chat_id"] or ""),
                created_by=str(row["created_by"] or "user"),
                owner_tool_call_id=str(row["owner_tool_call_id"] or ""),
                launch_mode=launch_mode,
                wake_id=str(row["wake_id"] or ""),
                last_actor=str(row["last_input_actor"] or ""),
                last_input_at=str(row["last_input_at"] or ""),
                input_event_count=int(row["input_event_count"] or 0),
                recovery_pending=recovery_pending,
                exit_reason=stored_exit_reason,
                exit_at=str(row["exit_at"] or ""),
                recovery_reason=str(row["recovery_reason"] or ""),
                recovered_at=str(row["recovered_at"] or ""),
                recovery_count=int(row["recovery_count"] or 0),
            )
            for event_row in self._db.execute(
                """SELECT event_id, actor, input_kind, byte_count, accepted,
                          reason, created_at
                   FROM terminal_input_events WHERE terminal_id = ?
                   ORDER BY event_id DESC LIMIT 200""",
                (session.id,),
            ).fetchall()[::-1]:
                session.input_events.append(self._input_event_public(event_row))
            self._reset_screen(session)
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
                        self._feed_screen(session, data)
            self._sessions[session.id] = session
            if (
                status == "exited"
                and stored_status in {"starting", "running"}
                and not recovery_pending
            ):
                session.exit_reason = f"{self.startup_reason}_interrupted"
                session.exit_at = _now_iso()
                self._ready_wake(session, exit_code=None, interrupted=True)
            if status != row["status"] or row["pid"] is not None:
                self._persist_session(session)
        self._repair_duplicate_titles()

    @staticmethod
    def _title_key(title: str) -> str:
        return str(title or "").strip().casefold()

    @classmethod
    def _next_default_title(cls, sessions: list[TerminalSession]) -> str:
        highest = 0
        occupied = {cls._title_key(session.title) for session in sessions}
        for session in sessions:
            match = _DEFAULT_TITLE_RE.fullmatch(str(session.title or "").strip())
            if match:
                highest = max(highest, int(match.group(1)))
        candidate_number = highest + 1
        while cls._title_key(f"Terminal {candidate_number}") in occupied:
            candidate_number += 1
        return f"Terminal {candidate_number}"

    @classmethod
    def _deduplicated_copy_title(cls, title: str, occupied: set[str]) -> str:
        base = str(title or "Terminal").strip()[:60] or "Terminal"
        match = _DEFAULT_TITLE_RE.fullmatch(base)
        if match:
            candidate_number = int(match.group(1)) + 1
            while cls._title_key(f"Terminal {candidate_number}") in occupied:
                candidate_number += 1
            return f"Terminal {candidate_number}"
        copy_number = 2
        while True:
            suffix = f" ({copy_number})"
            candidate = base[:60 - len(suffix)].rstrip() + suffix
            if cls._title_key(candidate) not in occupied:
                return candidate
            copy_number += 1

    def _repair_duplicate_titles(self) -> None:
        """Repair historical duplicates so title-based lookup is unambiguous."""
        by_project: dict[str, list[TerminalSession]] = {}
        for session in self._sessions.values():
            by_project.setdefault(session.project_id, []).append(session)
        for sessions in by_project.values():
            sessions.sort(key=lambda item: (item.order_index, item.created_at, item.id))
            occupied = {self._title_key(item.title) for item in sessions}
            seen: set[str] = set()
            for session in sessions:
                key = self._title_key(session.title)
                if key and key not in seen:
                    seen.add(key)
                    continue
                repaired = self._deduplicated_copy_title(session.title, occupied)
                session.title = repaired
                session.updated_at = _now_iso()
                repaired_key = self._title_key(repaired)
                occupied.add(repaired_key)
                seen.add(repaired_key)
                self._persist_session(session)

    async def restore_interrupted_sessions(self) -> list[dict[str, Any]]:
        """Restart interactive shells whose PTY owner stopped unexpectedly.

        Electron restarts never reach this path because the daemon remains
        alive.  This is the narrower daemon-crash/update recovery path: an
        interactive shell can be made usable again under the same durable
        terminal id, while one-shot Agent commands are never rerun.
        """
        restored: list[dict[str, Any]] = []
        for session in list(self._sessions.values()):
            if not session.recovery_pending:
                continue
            session.recovery_pending = False
            session.closing = False
            session.exit_code = None
            session.status = "starting"
            session.exit_reason = ""
            session.exit_at = ""
            session.recovery_reason = self.startup_reason
            session.recovered_at = _now_iso()
            session.recovery_count += 1
            # Leave the durable scrollback intact, but end any stale full-screen
            # mode and clear the current viewport before the replacement shell
            # writes its prompt.  Replaying the log then produces a clean,
            # usable tail instead of stacking old TUI paint frames.
            self._append_output(
                session,
                (
                    b"\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l"
                    b"\x1b[?1004l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m\x1b[2J\x1b[H"
                    + (
                        b"[Cyrene restored this shell after an application upgrade.]\r\n"
                        if self.startup_reason == "app_upgrade"
                        else b"[Cyrene restored this shell after Terminal Daemon restarted.]\r\n"
                    )
                ),
            )
            try:
                if sys.platform == "win32":  # pragma: no cover - Windows only
                    await self._spawn_windows(session)
                else:
                    await self._spawn_posix(session)
            except Exception:
                session.status = "exited"
                session.exit_reason = "recovery_failed"
                session.exit_at = _now_iso()
                session.updated_at = _now_iso()
                self._persist_session(session)
                self._publish_state(session)
                continue
            restored.append(session.public())
        return restored

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
                , owner_chat_id, created_by, owner_tool_call_id, launch_mode, wake_id,
                last_input_actor, last_input_at, input_event_count,
                exit_reason, exit_at, recovery_reason, recovered_at, recovery_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_id=excluded.project_id, title=excluded.title,
                cwd=excluded.cwd, shell=excluded.shell, argv_json=excluded.argv_json,
                updated_at=excluded.updated_at, status=excluded.status,
                exit_code=excluded.exit_code, pid=excluded.pid, cols=excluded.cols,
                rows=excluded.rows, next_seq=excluded.next_seq,
                output_start_seq=excluded.output_start_seq,
                order_index=excluded.order_index, pinned=excluded.pinned,
                owner_chat_id=excluded.owner_chat_id, created_by=excluded.created_by,
                owner_tool_call_id=excluded.owner_tool_call_id,
                launch_mode=excluded.launch_mode, wake_id=excluded.wake_id,
                last_input_actor=excluded.last_input_actor,
                last_input_at=excluded.last_input_at,
                input_event_count=excluded.input_event_count,
                exit_reason=excluded.exit_reason, exit_at=excluded.exit_at,
                recovery_reason=excluded.recovery_reason,
                recovered_at=excluded.recovered_at,
                recovery_count=excluded.recovery_count
            """,
            (
                session.id, session.project_id, session.title, session.cwd,
                session.shell, json.dumps(session.argv), session.created_at,
                session.updated_at, session.status, session.exit_code, session.pid,
                session.cols, session.rows, session.next_seq,
                session.output_start_seq, session.order_index, int(session.pinned),
                session.owner_chat_id, session.created_by, session.owner_tool_call_id,
                session.launch_mode, session.wake_id,
                session.last_actor, session.last_input_at, session.input_event_count,
                session.exit_reason, session.exit_at, session.recovery_reason,
                session.recovered_at, session.recovery_count,
            ),
        )
        self._db.commit()

    @staticmethod
    def _input_event_public(row: Any) -> dict[str, Any]:
        return {
            "eventId": int(row["event_id"]),
            "actor": str(row["actor"]),
            "kind": str(row["input_kind"]),
            "byteCount": int(row["byte_count"]),
            "accepted": bool(row["accepted"]),
            "reason": str(row["reason"] or ""),
            "createdAt": str(row["created_at"]),
        }

    def _record_input_event(
        self,
        session: TerminalSession,
        *,
        actor: str,
        input_kind: str,
        byte_count: int,
        accepted: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        created_at = _now_iso()
        session.input_event_count += 1
        if accepted:
            session.last_actor = actor
            session.last_input_at = created_at
        if self._db is not None:
            cursor = self._db.execute(
                """INSERT INTO terminal_input_events (
                       terminal_id, actor, input_kind, byte_count, accepted,
                       reason, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id, actor, input_kind, max(0, int(byte_count)),
                    int(accepted), str(reason or ""), created_at,
                ),
            )
            event_id = int(cursor.lastrowid)
            self._db.execute(
                """DELETE FROM terminal_input_events
                   WHERE terminal_id = ? AND event_id NOT IN (
                       SELECT event_id FROM terminal_input_events
                       WHERE terminal_id = ? ORDER BY event_id DESC LIMIT ?
                   )""",
                (session.id, session.id, INPUT_AUDIT_RETAINED_EVENTS),
            )
        else:
            event_id = session.input_event_count
        event = {
            "eventId": event_id,
            "actor": actor,
            "kind": input_kind,
            "byteCount": max(0, int(byte_count)),
            "accepted": bool(accepted),
            "reason": str(reason or ""),
            "createdAt": created_at,
        }
        session.input_events.append(event)
        while len(session.input_events) > 200:
            session.input_events.popleft()
        self._persist_session(session)
        self._publish_state(session)
        return event

    def input_history(self, terminal_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        session = self.get(terminal_id)
        bounded = max(1, min(int(limit or 200), 1000))
        if self._db is None:
            return list(session.input_events)[-bounded:]
        rows = self._db.execute(
            """SELECT event_id, actor, input_kind, byte_count, accepted,
                      reason, created_at
               FROM terminal_input_events WHERE terminal_id = ?
               ORDER BY event_id DESC LIMIT ?""",
            (session.id, bounded),
        ).fetchall()
        return [self._input_event_public(row) for row in rows[::-1]]

    @staticmethod
    def _project_workspace(project_id: str) -> Path:
        from cyrene.workbench.app_services import read_project

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
        from importlib import import_module

        kind, argv = import_module(
            "cyrene.tooling.backends.shell_runtime"
        ).interactive_argv()
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
        owner_chat_id: str = "",
        created_by: str = "user",
        owner_tool_call_id: str = "",
        launch_mode: str = "interactive",
        wake_on_exit: bool = False,
        wake_note: str = "",
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        resolved_cwd = Path(cwd).expanduser().resolve(strict=False)
        if not project_id:
            raise ValueError("projectId is required")
        if not resolved_cwd.is_dir():
            raise ValueError("terminal cwd does not exist")
        if not argv:
            raise ValueError("terminal command is required")
        async with self._lock:
            project_sessions = [
                current for current in self._sessions.values()
                if current.project_id == project_id
            ]
            requested_title = str(title or "").strip()[:60]
            if requested_title and any(
                self._title_key(current.title) == self._title_key(requested_title)
                for current in project_sessions
            ):
                raise ValueError("terminal title already exists in this project")
            resolved_title = requested_title or self._next_default_title(project_sessions)
            next_order_index = max(
                (current.order_index for current in project_sessions), default=-1
            ) + 1
            now = _now_iso()
            session = TerminalSession(
                id=_terminal_id(),
                project_id=project_id,
                title=resolved_title,
                cwd=str(resolved_cwd),
                shell=str(shell),
                argv=[str(part) for part in argv],
                created_at=now,
                updated_at=now,
                cols=max(20, min(400, int(cols or 100))),
                rows=max(5, min(200, int(rows or 30))),
                order_index=next_order_index,
                owner_chat_id=str(owner_chat_id or "").strip(),
                created_by=str(created_by or "user").strip() or "user",
                owner_tool_call_id=str(owner_tool_call_id or "").strip(),
                launch_mode=str(launch_mode or "interactive").strip() or "interactive",
            )
            self._reset_screen(session)
            if wake_on_exit:
                if not session.owner_chat_id:
                    raise ValueError("ownerChatId is required for wake_on_exit")
                session.wake_id = "twake_" + uuid.uuid4().hex[:16]
            self._sessions[session.id] = session
            self._persist_session(session)
            if session.wake_id:
                self._register_wake(session, str(wake_note or ""))
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
                    self._db.execute("DELETE FROM terminal_wakes WHERE terminal_id = ?", (session.id,))
                    self._db.commit()
            raise
        return session.public()

    @staticmethod
    def _reset_screen(session: TerminalSession) -> None:
        session.screen = pyte.Screen(session.cols, session.rows)
        session.stream = pyte.Stream(session.screen)

    @staticmethod
    def _feed_screen(session: TerminalSession, data: bytes) -> None:
        if session.stream is None:
            TerminalManager._reset_screen(session)
        session.stream.feed(bytes(data).decode("utf-8", errors="replace"))

    def screen_snapshot(self, terminal_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        if session.screen is None:
            self._reset_screen(session)
            for chunk in session.output:
                self._feed_screen(session, chunk.data)
        lines = [str(line).rstrip() for line in session.screen.display]
        while lines and not lines[-1]:
            lines.pop()
        return {
            "terminal": session.public(),
            "rows": session.rows,
            "cols": session.cols,
            "cursor": {
                "x": int(session.screen.cursor.x),
                "y": int(session.screen.cursor.y),
                "visible": not bool(getattr(session.screen.cursor, "hidden", False)),
            },
            "screenText": "\n".join(lines),
        }

    def scrollback_snapshot(
        self,
        terminal_id: str,
        *,
        cursor: int | None = None,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, Any]:
        """Return a bounded byte range from durable PTY output.

        Sequence numbers are byte offsets in the retained PTY stream. When no
        cursor is supplied, the most recent range is returned. Supplying a
        cursor reads forward and lets callers page through retained history.
        """
        session = self.get(terminal_id)
        limit = max(1, min(int(max_bytes or 64 * 1024), 512 * 1024))
        oldest_seq = session.output[0].start if session.output else session.next_seq
        next_seq = session.next_seq
        requested_start_seq = None if cursor is None else max(0, int(cursor))
        if requested_start_seq is None:
            start_seq = max(oldest_seq, next_seq - limit)
        else:
            start_seq = min(next_seq, max(oldest_seq, requested_start_seq))

        remaining = limit
        end_seq = start_seq
        parts: list[bytes] = []
        for chunk in session.output:
            if remaining <= 0:
                break
            if chunk.end <= start_seq:
                continue
            chunk_start = max(start_seq, chunk.start)
            offset = chunk_start - chunk.start
            data = chunk.data[offset:offset + remaining]
            if not data:
                continue
            parts.append(data)
            remaining -= len(data)
            end_seq = chunk_start + len(data)

        truncated_before = (
            start_seq > oldest_seq
            or (
                requested_start_seq is not None
                and requested_start_seq < oldest_seq
            )
        )
        truncated_after = end_seq < next_seq
        return {
            "terminal": session.public(),
            "encoding": "base64",
            "data": base64.b64encode(b"".join(parts)).decode("ascii"),
            "requestedStartSeq": requested_start_seq,
            "startSeq": start_seq,
            "endSeq": end_seq,
            "oldestSeq": oldest_seq,
            "nextSeq": next_seq,
            "truncated": truncated_before or truncated_after,
            "truncatedBefore": truncated_before,
            "truncatedAfter": truncated_after,
        }

    def _register_wake(self, session: TerminalSession, note: str) -> None:
        if self._db is None or not session.wake_id:
            return
        self._db.execute(
            """INSERT OR REPLACE INTO terminal_wakes (
                   wake_id, terminal_id, project_id, chat_id, note, title,
                   status, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'watching', ?)""",
            (
                session.wake_id, session.id, session.project_id,
                session.owner_chat_id, str(note or ""), session.title,
                _now_iso(),
            ),
        )
        self._db.commit()

    def _ready_wake(
        self, session: TerminalSession, *, exit_code: int | None,
        interrupted: bool = False,
    ) -> None:
        if self._db is None or not session.wake_id:
            return
        row = self._db.execute(
            "SELECT * FROM terminal_wakes WHERE wake_id = ?", (session.wake_id,)
        ).fetchone()
        if not row or str(row["status"]) not in {"watching", "ready", "claimed"}:
            return
        screen = self.screen_snapshot(session.id)["screenText"]
        status = "interrupted" if interrupted else ("done" if exit_code == 0 else "err")
        prompt = self._wake_prompt(
            session=session,
            status=status,
            exit_code=exit_code,
            note=str(row["note"] or ""),
            screen=screen,
        )
        self._db.execute(
            """UPDATE terminal_wakes SET status='ready', exit_status=?, exit_code=?,
                   final_screen=?, prompt=?, ready_at=?, lease_token='', lease_until=0
               WHERE wake_id=? AND status IN ('watching','ready','claimed')""",
            (status, exit_code, screen, prompt, _now_iso(), session.wake_id),
        )
        self._db.commit()

    @staticmethod
    def _wake_prompt(
        *, session: TerminalSession, status: str, exit_code: int | None,
        note: str, screen: str,
    ) -> str:
        blocks = [
            "[Terminal exited — automatic wake]",
            f"terminal_id: {session.id}",
            f"status: {status}",
            f"exit_code: {exit_code if exit_code is not None else 'unknown'}",
            f"title: {session.title}",
            f"cwd: {session.cwd}",
        ]
        if note.strip():
            blocks.append(f"wake_note: {note.strip()}")
        blocks.extend([
            "",
            "The terminal process has exited. Inspect the captured final VT screen, "
            "continue the prior work, and do not wait for this process again.",
            "",
            "--- final screen ---",
            screen[-12000:] or "(no captured output)",
        ])
        return "\n".join(blocks)

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
        now = _now_iso()
        session.exit_at = now
        if session.closing:
            session.exit_reason = "deleted"
        elif exit_code is None:
            session.exit_reason = "pty_lost"
        elif exit_code < 0:
            session.exit_reason = "signal"
        else:
            session.exit_reason = "process_exit"
        session.updated_at = now
        self._persist_session(session)
        self._ready_wake(session, exit_code=exit_code)
        self._publish_state(session)

    def _append_output(self, session: TerminalSession, data: bytes) -> None:
        if not data:
            return
        self._feed_screen(session, data)
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

    def list(
        self, project_id: str = "", *, owner_chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        project_id = str(project_id or "")
        sessions = [
            session.public()
            for session in self._sessions.values()
            if not project_id or session.project_id == project_id
            if owner_chat_id is None or session.owner_chat_id == str(owner_chat_id or "")
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
        if any(
            current.id != session.id
            and current.project_id == session.project_id
            and self._title_key(current.title) == self._title_key(normalized)
            for current in self._sessions.values()
        ):
            raise ValueError("terminal title already exists in this project")
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

    async def write(self, terminal_id: str, data: str, *, actor: str = "agent") -> None:
        await self.write_bytes(
            terminal_id,
            str(data or "").encode("utf-8"),
            binary=False,
            actor=actor,
        )

    async def write_bytes(
        self,
        terminal_id: str,
        data: bytes,
        *,
        binary: bool = False,
        actor: str = "agent",
    ) -> None:
        encoded = bytes(data or b"")
        if not encoded:
            return
        session = self.get(terminal_id)
        normalized_actor = "user" if actor == "user" else "agent"
        input_kind = "binary" if binary else "text"
        async with session.input_lock:
            if session.status != "running":
                raise RuntimeError("terminal is not running")
            now = time.monotonic()
            user_priority_remaining = (
                self.user_input_priority_seconds - (now - session.last_user_input_at)
            )
            if normalized_actor == "agent" and user_priority_remaining > 0:
                retry_after_ms = int(user_priority_remaining * 1000) + 1
                self._record_input_event(
                    session,
                    actor=normalized_actor,
                    input_kind=input_kind,
                    byte_count=len(encoded),
                    accepted=False,
                    reason="user_priority",
                )
                raise TerminalInputBusyError(retry_after_ms)
            if normalized_actor == "user":
                session.last_user_input_at = now
            if sys.platform == "win32":  # pragma: no cover - Windows only
                text = encoded.decode("latin-1" if binary else "utf-8", errors="replace")
                await asyncio.to_thread(session.winpty.write, text)
            else:
                if session.master_fd is None:
                    raise RuntimeError("terminal is unavailable")
                offset = 0
                while offset < len(encoded):
                    try:
                        offset += os.write(session.master_fd, encoded[offset:])
                    except BlockingIOError:
                        await asyncio.sleep(0)
            self._record_input_event(
                session,
                actor=normalized_actor,
                input_kind=input_kind,
                byte_count=len(encoded),
                accepted=True,
            )

    async def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        session = self.get(terminal_id)
        cols = max(20, min(400, int(cols or 0)))
        rows = max(5, min(200, int(rows or 0)))
        if cols == session.cols and rows == session.rows:
            return
        session.cols = cols
        session.rows = rows
        if session.screen is not None:
            session.screen.resize(lines=rows, columns=cols)
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

    async def restart(
        self, terminal_id: str, *, reason: str = "user_restart"
    ) -> dict[str, Any]:
        """Restart an exited interactive shell under its existing terminal id."""
        session = self.get(terminal_id)
        if session.launch_mode != "interactive":
            raise ValueError("one-shot terminals cannot be restarted")
        if session.status == "running":
            return session.public()
        if session.status == "starting":
            raise RuntimeError("terminal restart is already in progress")
        session.closing = False
        session.process = None
        session.master_fd = None
        session.winpty = None
        session.exit_code = None
        session.exit_reason = ""
        session.exit_at = ""
        session.status = "starting"
        session.recovery_reason = "pty_restart"
        session.recovered_at = _now_iso()
        session.recovery_count += 1
        self._append_output(
            session,
            (
                b"\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l"
                b"\x1b[?1004l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m\x1b[2J\x1b[H"
                b"[Cyrene restarted this terminal after its PTY exited.]\r\n"
            ),
        )
        try:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                await self._spawn_windows(session)
            else:
                await self._spawn_posix(session)
        except Exception as exc:
            session.status = "exited"
            session.exit_reason = "restart_failed"
            session.exit_at = _now_iso()
            session.updated_at = session.exit_at
            self._persist_session(session)
            self._publish_state(session)
            raise RuntimeError(f"terminal restart failed: {exc}") from exc
        return session.public()

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
                        "DELETE FROM terminal_input_events WHERE terminal_id = ?",
                        (session.id,),
                    )
                    self._db.execute(
                        """UPDATE terminal_wakes SET status='cancelled', cancelled_at=?,
                               lease_token='', lease_until=0
                           WHERE terminal_id=? AND status IN ('watching','ready','claimed')""",
                        (_now_iso(), session.id),
                    )
                    self._db.execute(
                        "UPDATE terminal_projects SET active_terminal_id = NULL WHERE active_terminal_id = ?",
                        (session.id,),
                    )
                    self._db.commit()
                if self.state_dir is not None:
                    with contextlib.suppress(OSError):
                        self._scroll_path(session.id).unlink()
        return result

    def wake_info(self, terminal_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT * FROM terminal_wakes WHERE terminal_id = ?", (str(terminal_id or ""),)
        ).fetchone()
        return dict(row) if row else None

    def claim_wake(self, consumer_id: str, lease_seconds: float = 30.0) -> dict[str, Any] | None:
        if self._db is None:
            return None
        import time

        now = time.time()
        token = f"{str(consumer_id or 'web')}:{uuid.uuid4().hex}"
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                """SELECT * FROM terminal_wakes
                   WHERE status='ready' OR (status='claimed' AND lease_until < ?)
                   ORDER BY ready_at, created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                self._db.commit()
                return None
            self._db.execute(
                """UPDATE terminal_wakes SET status='claimed', lease_token=?, lease_until=?
                   WHERE wake_id=?""",
                (token, now + max(5.0, min(float(lease_seconds), 300.0)), row["wake_id"]),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        claimed = dict(row)
        claimed["status"] = "claimed"
        claimed["lease_token"] = token
        claimed["lease_until"] = now + max(5.0, min(float(lease_seconds), 300.0))
        return claimed

    def settle_wake(self, wake_id: str, lease_token: str, outcome: str) -> dict[str, Any]:
        if self._db is None:
            raise LookupError("wake not found")
        row = self._db.execute(
            "SELECT * FROM terminal_wakes WHERE wake_id = ?", (str(wake_id or ""),)
        ).fetchone()
        if not row:
            raise LookupError("wake not found")
        if str(row["status"]) == "delivered":
            return dict(row)
        if str(row["lease_token"] or "") != str(lease_token or ""):
            raise ValueError("wake lease is no longer owned by this consumer")
        normalized = str(outcome or "").strip().lower()
        if normalized == "delivered":
            self._db.execute(
                """UPDATE terminal_wakes SET status='delivered', delivered_at=?,
                       lease_token='', lease_until=0 WHERE wake_id=?""",
                (_now_iso(), wake_id),
            )
        elif normalized == "cancelled":
            self._db.execute(
                """UPDATE terminal_wakes SET status='cancelled', cancelled_at=?,
                       lease_token='', lease_until=0 WHERE wake_id=?""",
                (_now_iso(), wake_id),
            )
        else:
            self._db.execute(
                """UPDATE terminal_wakes SET status='ready', lease_token='', lease_until=0
                   WHERE wake_id=?""",
                (wake_id,),
            )
        self._db.commit()
        settled = self._db.execute(
            "SELECT * FROM terminal_wakes WHERE wake_id = ?", (wake_id,)
        ).fetchone()
        return dict(settled) if settled else {}

    async def close_all(self) -> None:
        for terminal_id in list(self._sessions):
            with contextlib.suppress(Exception):
                await self.close(terminal_id, remove=True)


_MANAGER = TerminalManager()


def get_terminal_manager() -> TerminalManager:
    return _MANAGER
