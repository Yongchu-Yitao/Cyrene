"""Cross-platform PTY session manager for Workbench terminals."""

from __future__ import annotations

import asyncio
import base64
import codecs
import concurrent.futures
import contextlib
import json
import os
import queue
import re
import select
import signal
import shutil
import socket
import sqlite3
import struct
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyte

from .history import IncrementalPlainTextParser, osc133_commands, plain_terminal_text
from .remote import build_managed_ssh_launch
from .shell_integration import OscMetadataParser, prepare_shell_integration


DEFAULT_OUTPUT_LIMIT = 16 * 1024 * 1024
USER_INPUT_PRIORITY_SECONDS = 2.0
INPUT_AUDIT_RETAINED_EVENTS = 10_000
DEFAULT_UTF8_LOCALE = "C.UTF-8"
OUTPUT_FLUSH_INTERVAL_SECONDS = 0.05
OUTPUT_FLUSH_THRESHOLD = 256 * 1024
PTY_READ_BUDGET = 64 * 1024
SCREEN_DRAIN_BUDGET = 32 * 1024
DEFAULT_PERSISTENCE_BACKLOG_LIMIT = 8 * 1024 * 1024
PERSISTENCE_BACKLOG_POLL_SECONDS = 0.01
WINDOWS_POST_EXIT_DRAIN_IDLE_SECONDS = 5.0
WINDOWS_POST_EXIT_DRAIN_POLL_SECONDS = 0.05
SCROLLBACK_SEGMENT_SIZE = 4 * 1024 * 1024
SSH_RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
_DEFAULT_TITLE_RE = re.compile(r"^Terminal\s+(\d+)$", re.IGNORECASE)


def _winpty_output_ready(process: Any, timeout: float) -> bool:
    return bool(select.select([process.fileobj], [], [], timeout)[0])


def _write_winpty_input(process: Any, text: str) -> None:
    try:
        process.write(text)
        return
    except EOFError as exc:
        if bool(getattr(process, "flag_eof", False)):
            raise RuntimeError("terminal is not running") from exc
    try:
        process.pty.write(text)
    except Exception as exc:
        raise RuntimeError("terminal is not running") from exc


_SESSION_UPSERT_SQL = """
    INSERT INTO terminal_sessions (
        id, project_id, title, cwd, shell, argv_json, created_at,
        updated_at, status, exit_code, pid, cols, rows, next_seq,
        output_start_seq, order_index, pinned,
        owner_chat_id, created_by, owner_tool_call_id, launch_mode, wake_id,
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
"""

_SESSION_METADATA_SQL = """
    UPDATE terminal_sessions
       SET cwd_uri = ?, shell_title = ?, integration_level = ?,
           command_state = ?, last_command_exit_code = ?,
           connection_kind = ?, ssh_target = ?, remote_cwd = ?,
           tmux_session = ?, connection_status = ?, disconnect_reason = ?,
           reconnect_attempt = ?
     WHERE id = ?
"""


@dataclass(frozen=True, slots=True)
class _PersistenceItem:
    terminal_id: str
    next_seq: int
    output: bytes
    output_events: tuple[tuple[int, int, str], ...]
    session_values: tuple[Any, ...]
    metadata_values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _ScreenUpdate:
    terminal_id: str
    data: bytes
    cols: int
    rows: int
    start_seq: int
    next_seq: int
    metadata_loop: Any = None
    metadata_callback: Any = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class _ScreenResize:
    terminal_id: str
    cols: int
    rows: int
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class _TerminalWorkReady:
    terminal_id: str


@dataclass(frozen=True, slots=True)
class _WorkerQuery:
    operation: str
    arguments: tuple[Any, ...]
    future: concurrent.futures.Future[Any]
    after_token: int = 0
    enqueued_at: float = field(default_factory=time.monotonic)


_QUERY_OPERATIONS = frozenset({
    "history", "search", "commands", "command_output", "replay",
})
_TERMINAL_CONTROL_OPERATIONS = frozenset({"screen", "reset_metadata", "remove"})
_WORKER_STOP = object()


class _TerminalPersistenceWriter:
    """Own durable scrollback, SQLite, screen parsing, and history queries."""

    def __init__(self, state_dir: Path, *, output_limit: int) -> None:
        self._state_dir = state_dir
        self._db_path = state_dir / "terminals.sqlite3"
        self._output_limit = output_limit
        self._segment_size = min(SCROLLBACK_SEGMENT_SIZE, output_limit)
        self._queue: queue.PriorityQueue[tuple[int, int, float, Any]] = (
            queue.PriorityQueue()
        )
        self._query_queue: queue.Queue[Any] = queue.Queue()
        self._condition = threading.Condition()
        self._queue_sequence = 0
        self._submitted = 0
        self._completed = 0
        self._failure: BaseException | None = None
        self._retained_starts: dict[str, int] = {}
        self._screens: dict[str, tuple[Any, Any, Any]] = {}
        self._metadata_parsers: dict[str, OscMetadataParser] = {}
        self._command_index_parsers: dict[str, OscMetadataParser] = {}
        self._segment_locks: dict[str, threading.RLock] = {}
        self._terminal_work: dict[str, deque[Any]] = {}
        self._terminal_work_scheduled: set[str] = set()
        self._terminal_work_bytes = 0
        self._terminal_work_peak_bytes = 0
        self._screen_snapshots: dict[str, tuple[int, dict[str, Any]]] = {}
        self.thread_id: int | None = None
        self.query_thread_id: int | None = None
        self.batch_count = 0
        self.bytes_written = 0
        self.bytes_read = 0
        self.segments_deleted = 0
        self.eviction_count = 0
        self.screen_bytes_parsed = 0
        self.query_count = 0
        self.worker_queue_wait_max_ms = 0.0
        self.query_queue_wait_max_ms = 0.0
        self._thread = threading.Thread(
            target=self._run_main,
            name="cyrene-terminal-worker",
            daemon=True,
        )
        self._query_thread = threading.Thread(
            target=self._run_queries,
            name="cyrene-terminal-query-worker",
            daemon=True,
        )
        self._thread.start()
        self._query_thread.start()

    @property
    def completed(self) -> int:
        with self._condition:
            return self._completed

    @property
    def submitted(self) -> int:
        with self._condition:
            return self._submitted

    def retained_start(self, terminal_id: str) -> int:
        with self._condition:
            return self._retained_starts.get(terminal_id, 0)

    def metrics(self) -> dict[str, int]:
        """Return monotonic worker counters used by the performance harness."""
        with self._condition:
            return {
                "batches": self.batch_count,
                "bytesWritten": self.bytes_written,
                "bytesRead": self.bytes_read,
                "segmentsDeleted": self.segments_deleted,
                "evictions": self.eviction_count,
                "screenBytesParsed": self.screen_bytes_parsed,
                "queries": self.query_count,
                "terminalWorkPeakBytes": self._terminal_work_peak_bytes,
                "workerQueueWaitMaxUs": int(self.worker_queue_wait_max_ms * 1000),
                "queryQueueWaitMaxUs": int(self.query_queue_wait_max_ms * 1000),
            }

    def _put_main(self, priority: int, payload: Any) -> None:
        with self._condition:
            self._queue_sequence += 1
            sequence = self._queue_sequence
        self._queue.put((priority, sequence, time.monotonic(), payload))

    def _submit_terminal_work(self, terminal_id: str, payload: Any) -> None:
        schedule = False
        with self._condition:
            pending = self._terminal_work.setdefault(terminal_id, deque())
            pending.append(payload)
            if isinstance(payload, _ScreenUpdate):
                self._terminal_work_bytes += len(payload.data)
                self._terminal_work_peak_bytes = max(
                    self._terminal_work_peak_bytes, self._terminal_work_bytes
                )
            if terminal_id not in self._terminal_work_scheduled:
                self._terminal_work_scheduled.add(terminal_id)
                schedule = True
        if schedule:
            self._put_main(0, _TerminalWorkReady(terminal_id))

    def cached_screen(
        self, terminal_id: str, minimum_seq: int,
    ) -> dict[str, Any] | None:
        with self._condition:
            entry = self._screen_snapshots.get(terminal_id)
            if entry is None or entry[0] < minimum_seq:
                return None
            return dict(entry[1])

    def submit(self, items: list[_PersistenceItem]) -> int:
        if not items:
            return self.submitted
        with self._condition:
            if self._failure is not None:
                raise RuntimeError("terminal persistence writer failed") from self._failure
            self._submitted += 1
            token = self._submitted
        self._put_main(1, (token, tuple(items)))
        return token

    def submit_screen(
        self, terminal_id: str, data: bytes, *, cols: int, rows: int,
        start_seq: int, next_seq: int, metadata_loop: Any = None,
        metadata_callback: Any = None,
    ) -> None:
        self._submit_terminal_work(
            terminal_id,
            _ScreenUpdate(
                terminal_id, bytes(data), cols, rows, start_seq, next_seq,
                metadata_loop, metadata_callback,
            ),
        )

    def resize_screen(self, terminal_id: str, *, cols: int, rows: int) -> None:
        self._submit_terminal_work(
            terminal_id, _ScreenResize(terminal_id, cols, rows)
        )

    def request(self, operation: str, *arguments: Any) -> concurrent.futures.Future[Any]:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._condition:
            if self._failure is not None:
                future.set_exception(
                    RuntimeError("terminal background worker failed")
                )
                return future
        query = _WorkerQuery(
            operation,
            tuple(arguments),
            future,
            self.submitted if operation in _QUERY_OPERATIONS else 0,
        )
        if operation in _QUERY_OPERATIONS:
            self._query_queue.put(query)
        elif operation in _TERMINAL_CONTROL_OPERATIONS and arguments:
            self._submit_terminal_work(str(arguments[0]), query)
        else:
            self._put_main(2, query)
        return future

    def call(self, operation: str, *arguments: Any) -> Any:
        return self.request(operation, *arguments).result()

    def wait(self, token: int) -> None:
        if token <= 0:
            return
        with self._condition:
            while self._completed < token and self._failure is None:
                self._condition.wait()
            if self._failure is not None:
                raise RuntimeError("terminal persistence writer failed") from self._failure

    def close(self) -> None:
        token = self.submitted
        self.wait(token)
        self._query_queue.put(_WORKER_STOP)
        self._query_thread.join()
        self._put_main(3, _WORKER_STOP)
        self._thread.join()

    def _legacy_path(self, terminal_id: str) -> Path:
        return self._state_dir / "scrollback" / f"{terminal_id}.bin"

    def _segment_dir(self, terminal_id: str) -> Path:
        return self._state_dir / "scrollback" / terminal_id

    def _segment_lock(self, terminal_id: str) -> threading.RLock:
        with self._condition:
            return self._segment_locks.setdefault(terminal_id, threading.RLock())

    def _segments(self, terminal_id: str) -> list[tuple[int, Path, int]]:
        directory = self._segment_dir(terminal_id)
        if not directory.is_dir():
            return []
        entries: list[tuple[int, Path, int]] = []
        for path in directory.glob("*.bin"):
            try:
                entries.append((int(path.stem), path, path.stat().st_size))
            except (OSError, ValueError):
                continue
        entries.sort(key=lambda entry: entry[0])
        return entries

    def _migrate_legacy(self, terminal_id: str, output_start_seq: int) -> None:
        legacy = self._legacy_path(terminal_id)
        target = self._segment_dir(terminal_id)
        if target.is_dir() or not legacy.is_file():
            return
        temporary = target.with_name(f".{target.name}.{os.getpid()}.migrating")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        cursor = max(0, int(output_start_seq))
        with legacy.open("rb") as source:
            while data := source.read(self._segment_size):
                with (temporary / f"{cursor:020d}.bin").open("wb") as stream:
                    stream.write(data)
                self.bytes_written += len(data)
                cursor += len(data)
        os.replace(temporary, target)
        legacy.unlink()

    def _oldest_seq(self, terminal_id: str, fallback: int) -> int:
        with self._segment_lock(terminal_id):
            segments = self._segments(terminal_id)
            return segments[0][0] if segments else max(0, int(fallback))

    def _append_segments(self, item: _PersistenceItem) -> int | None:
        with self._segment_lock(item.terminal_id):
            return self._append_segments_locked(item)

    def _append_segments_locked(self, item: _PersistenceItem) -> int | None:
        output_start_seq = int(item.session_values[14])
        self._migrate_legacy(item.terminal_id, output_start_seq)
        directory = self._segment_dir(item.terminal_id)
        directory.mkdir(parents=True, exist_ok=True)
        cursor = item.next_seq - len(item.output)
        offset = 0
        segments = self._segments(item.terminal_id)
        while offset < len(item.output):
            if segments:
                segment_start, path, size = segments[-1]
                if segment_start + size == cursor and size < self._segment_size:
                    capacity = self._segment_size - size
                else:
                    path = directory / f"{cursor:020d}.bin"
                    size = 0
                    capacity = self._segment_size
                    segments.append((cursor, path, 0))
            else:
                path = directory / f"{cursor:020d}.bin"
                size = 0
                capacity = self._segment_size
                segments.append((cursor, path, 0))
            chunk = item.output[offset:offset + capacity]
            with path.open("ab") as stream:
                stream.write(chunk)
            self.bytes_written += len(chunk)
            new_size = size + len(chunk)
            segments[-1] = (segments[-1][0], path, new_size)
            cursor += len(chunk)
            offset += len(chunk)

        total = sum(size for _start, _path, size in segments)
        evicted = False
        while total > self._output_limit and len(segments) > 1:
            _start, path, size = segments.pop(0)
            path.unlink()
            self.segments_deleted += 1
            total -= size
            evicted = True
        if evicted:
            self.eviction_count += 1
        return segments[0][0] if evicted and segments else None

    def _read_history(
        self, terminal_id: str, start_seq: int, end_seq: int, fallback_start: int,
    ) -> tuple[int, int, bytes]:
        with self._segment_lock(terminal_id):
            return self._read_history_locked(
                terminal_id, start_seq, end_seq, fallback_start
            )

    def _read_history_locked(
        self, terminal_id: str, start_seq: int, end_seq: int, fallback_start: int,
    ) -> tuple[int, int, bytes]:
        self._migrate_legacy(terminal_id, fallback_start)
        segments = self._segments(terminal_id)
        oldest = segments[0][0] if segments else max(0, int(fallback_start))
        start = max(oldest, int(start_seq))
        end = max(start, int(end_seq))
        if end <= start:
            return oldest, start, b""
        if not segments:
            legacy = self._legacy_path(terminal_id)
            try:
                with legacy.open("rb") as stream:
                    stream.seek(start - oldest)
                    return oldest, start, stream.read(end - start)
            except OSError:
                return oldest, start, b""
        parts: list[bytes] = []
        for segment_start, path, size in segments:
            segment_end = segment_start + size
            if segment_end <= start or segment_start >= end:
                continue
            left = max(start, segment_start) - segment_start
            right = min(end, segment_end) - segment_start
            with path.open("rb") as stream:
                stream.seek(left)
                data = stream.read(right - left)
                self.bytes_read += len(data)
                parts.append(data)
        return oldest, start, b"".join(parts)

    def _screen_state(self, terminal_id: str, cols: int, rows: int):
        state = self._screens.get(terminal_id)
        if state is None:
            screen = pyte.Screen(cols, rows)
            state = (
                screen,
                pyte.Stream(screen),
                codecs.getincrementaldecoder("utf-8")(errors="replace"),
            )
            self._screens[terminal_id] = state
        return state

    def _feed_worker_screen(self, update: _ScreenUpdate) -> None:
        screen, stream, decoder = self._screen_state(
            update.terminal_id, update.cols, update.rows
        )
        stream.feed(decoder.decode(update.data, final=False))
        self.screen_bytes_parsed += len(update.data)
        parser = self._metadata_parsers.setdefault(
            update.terminal_id, OscMetadataParser()
        )
        metadata = parser.feed(update.data, start_seq=update.start_seq)
        if metadata and update.metadata_loop is not None:
            update.metadata_loop.call_soon_threadsafe(
                update.metadata_callback, update.terminal_id, tuple(metadata)
            )
        snapshot = self._screen_body((screen, stream, decoder))
        with self._condition:
            self._screen_snapshots[update.terminal_id] = (
                update.next_seq, snapshot
            )

    @staticmethod
    def _screen_body(state: tuple[Any, Any, Any]) -> dict[str, Any]:
        screen = state[0]
        lines = [str(line).rstrip() for line in screen.display]
        while lines and not lines[-1]:
            lines.pop()
        return {
            "rows": int(screen.lines),
            "cols": int(screen.columns),
            "cursor": {
                "x": int(screen.cursor.x),
                "y": int(screen.cursor.y),
                "visible": not bool(getattr(screen.cursor, "hidden", False)),
            },
            "screenText": "\n".join(lines),
        }

    @staticmethod
    def _history_timestamp(
        connection: sqlite3.Connection, terminal_id: str, seq: int, default: str = "",
    ) -> str:
        row = connection.execute(
            """SELECT created_at FROM terminal_output_events
               WHERE terminal_id = ? AND start_seq <= ?
               ORDER BY start_seq DESC LIMIT 1""",
            (terminal_id, max(0, int(seq))),
        ).fetchone()
        return str(row[0]) if row else default

    def _index_output(
        self, connection: sqlite3.Connection, item: _PersistenceItem,
    ) -> None:
        if not item.output:
            return
        terminal_id = item.terminal_id
        chunk_start = item.next_seq - len(item.output)
        output_start_seq = int(item.session_values[14])
        state = connection.execute(
            "SELECT * FROM terminal_index_state WHERE terminal_id = ?",
            (terminal_id,),
        ).fetchone()
        if state is None:
            if chunk_start != output_start_seq:
                return
            indexed_next_seq = chunk_start
            capture_start: int | None = None
            capture = bytearray()
            running_start: int | None = None
            running_command = ""
        else:
            indexed_next_seq = int(state["indexed_next_seq"])
            if indexed_next_seq != chunk_start:
                return
            capture_start = state["command_capture_start_seq"]
            capture = bytearray(bytes(state["command_capture"] or b""))
            running_start = state["running_output_start_seq"]
            running_command = str(state["running_command_text"] or "")

        created_at = (
            str(item.output_events[0][2])
            if item.output_events
            else self._history_timestamp(connection, terminal_id, chunk_start)
        )
        parser_state = {}
        if state is not None and str(state["text_state_json"] or ""):
            parser_state = json.loads(str(state["text_state_json"]))
        text_parser = IncrementalPlainTextParser(parser_state or {
            "nextSeq": chunk_start,
            "lineStartSeq": chunk_start,
        })
        lines = text_parser.feed(item.output, start_seq=chunk_start)
        lines.append(text_parser.current_line())
        self._upsert_search_lines(
            connection, terminal_id, lines, created_at=created_at
        )

        parser = self._command_index_parsers.setdefault(
            terminal_id, OscMetadataParser()
        )
        events = parser.feed(item.output, start_seq=chunk_start)
        cursor = chunk_start
        for event in events:
            event_start = int(event["startSeq"])
            event_end = int(event["endSeq"])
            if capture_start is not None and cursor < event_start:
                capture.extend(item.output[
                    cursor - chunk_start:event_start - chunk_start
                ])
            cursor = max(cursor, event_end)
            kind = str(event.get("kind") or "")
            if kind == "command":
                capture_start = event_end
                capture.clear()
            elif kind == "output" and capture_start is not None:
                running_command = plain_terminal_text(bytes(capture)).strip()
                running_start = event_end
                command_id = f"cmd_{running_start}"
                connection.execute(
                    """INSERT OR REPLACE INTO terminal_commands (
                           terminal_id, command_id, command_start_seq,
                           command_text, output_start_seq, output_end_seq,
                           exit_code, started_at, finished_at, running
                       ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, '', 1)""",
                    (
                        terminal_id, command_id, int(capture_start),
                        running_command, running_start, item.next_seq,
                        self._history_timestamp(
                            connection, terminal_id, running_start, created_at
                        ),
                    ),
                )
                capture_start = None
                capture.clear()
            elif kind == "finished" and running_start is not None:
                connection.execute(
                    """UPDATE terminal_commands
                          SET output_end_seq = ?, exit_code = ?, finished_at = ?,
                              running = 0
                        WHERE terminal_id = ? AND command_id = ?""",
                    (
                        event_start, event.get("exitCode"),
                        self._history_timestamp(
                            connection, terminal_id, event_start, created_at
                        ),
                        terminal_id, f"cmd_{running_start}",
                    ),
                )
                running_start = None
                running_command = ""
        capture_end = item.next_seq
        pending_sequence_start = parser.pending_sequence_start
        if pending_sequence_start is not None:
            capture_end = min(capture_end, max(chunk_start, pending_sequence_start))
        if capture_start is not None and cursor < capture_end:
            capture.extend(item.output[
                cursor - chunk_start:capture_end - chunk_start
            ])
        if running_start is not None:
            connection.execute(
                """UPDATE terminal_commands SET output_end_seq = ?
                    WHERE terminal_id = ? AND command_id = ? AND running = 1""",
                (item.next_seq, terminal_id, f"cmd_{running_start}"),
            )
        connection.execute(
            """INSERT OR REPLACE INTO terminal_index_state (
                   terminal_id, indexed_next_seq, command_capture_start_seq,
                   command_capture, running_output_start_seq,
                   running_command_text, text_state_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                terminal_id, item.next_seq, capture_start, bytes(capture),
                running_start, running_command,
                json.dumps(text_parser.state(), separators=(",", ":")),
            ),
        )

    def _upsert_search_lines(
        self, connection: sqlite3.Connection, terminal_id: str,
        lines: list[dict[str, Any]], *, created_at: str = "",
    ) -> None:
        for line in lines:
            line_start = int(line["startSeq"])
            timestamp = self._history_timestamp(
                connection, terminal_id, line_start, created_at
            )
            text = str(line["text"])
            connection.execute(
                """INSERT INTO terminal_text_chunks (
                       terminal_id, line_number, start_seq, end_seq, text,
                       search_text, complete, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(terminal_id, line_number) DO UPDATE SET
                       start_seq=excluded.start_seq,
                       end_seq=excluded.end_seq,
                       text=excluded.text,
                       search_text=excluded.search_text,
                       complete=excluded.complete,
                       created_at=excluded.created_at""",
                (
                    terminal_id, int(line["line"]), line_start,
                    int(line["endSeq"]), text, text.casefold(),
                    int(bool(line["complete"])), timestamp,
                ),
            )

    def _rebuild_index(
        self, connection: sqlite3.Connection, terminal_id: str,
        output_start_seq: int, next_seq: int,
    ) -> None:
        oldest, _actual, data = self._read_history(
            terminal_id, output_start_seq, next_seq, output_start_seq
        )
        connection.execute(
            "DELETE FROM terminal_text_chunks WHERE terminal_id = ?",
            (terminal_id,),
        )
        connection.execute(
            "DELETE FROM terminal_commands WHERE terminal_id = ?",
            (terminal_id,),
        )
        text_parser = IncrementalPlainTextParser({
            "nextSeq": oldest,
            "lineStartSeq": oldest,
        })
        for offset in range(0, len(data), OUTPUT_FLUSH_THRESHOLD):
            raw = data[offset:offset + OUTPUT_FLUSH_THRESHOLD]
            position = oldest + offset
            self._upsert_search_lines(
                connection, terminal_id,
                text_parser.feed(raw, start_seq=position),
            )
        self._upsert_search_lines(
            connection, terminal_id, [text_parser.current_line()]
        )
        commands = osc133_commands(
            data,
            base_seq=oldest,
            timestamp_at=lambda seq: self._history_timestamp(
                connection, terminal_id, seq
            ),
        )
        for command in commands:
            connection.execute(
                """INSERT INTO terminal_commands (
                       terminal_id, command_id, command_start_seq, command_text,
                       output_start_seq, output_end_seq, exit_code, started_at,
                       finished_at, running
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    terminal_id, command["id"],
                    int(command["outputStartSeq"]), str(command["command"]),
                    int(command["outputStartSeq"]),
                    int(command["outputEndSeq"]), command.get("exitCode"),
                    str(command.get("startedAt") or ""),
                    str(command.get("finishedAt") or ""),
                    int(bool(command.get("running"))),
                ),
            )
        running = next(
            (command for command in reversed(commands) if command.get("running")),
            None,
        )
        connection.execute(
            """INSERT OR REPLACE INTO terminal_index_state (
                   terminal_id, indexed_next_seq, command_capture_start_seq,
                   command_capture, running_output_start_seq,
                   running_command_text, text_state_json
               ) VALUES (?, ?, NULL, X'', ?, ?, ?)""",
            (
                terminal_id, oldest + len(data),
                int(running["outputStartSeq"]) if running else None,
                str(running["command"]) if running else "",
                json.dumps(text_parser.state(), separators=(",", ":")),
            ),
        )
        connection.commit()

    def _ensure_index(
        self, connection: sqlite3.Connection, terminal_id: str,
        output_start_seq: int, next_seq: int,
    ) -> None:
        row = connection.execute(
            "SELECT indexed_next_seq FROM terminal_index_state WHERE terminal_id = ?",
            (terminal_id,),
        ).fetchone()
        if row is not None and int(row[0]) >= int(next_seq):
            return
        self._rebuild_index(
            connection, terminal_id, output_start_seq, next_seq
        )

    def _commands_query(
        self, connection: sqlite3.Connection, terminal_id: str,
        output_start_seq: int, next_seq: int,
    ) -> list[dict[str, Any]]:
        output_start_seq = self._oldest_seq(terminal_id, output_start_seq)
        self._ensure_index(
            connection, terminal_id, output_start_seq, next_seq
        )
        rows = connection.execute(
            """SELECT command_id, command_text, output_start_seq,
                      output_end_seq, exit_code, started_at, finished_at, running
                 FROM terminal_commands
                WHERE terminal_id = ? AND command_start_seq >= ?
                  AND output_start_seq < ?
                ORDER BY output_start_seq""",
            (terminal_id, output_start_seq, next_seq),
        ).fetchall()
        return [{
            "id": str(row["command_id"]),
            "command": str(row["command_text"]),
            "outputStartSeq": int(row["output_start_seq"]),
            "outputEndSeq": min(next_seq, int(row["output_end_seq"])),
            "exitCode": row["exit_code"],
            "startedAt": str(row["started_at"] or ""),
            "finishedAt": str(row["finished_at"] or ""),
            "running": bool(row["running"]),
        } for row in rows]

    def _search_query(
        self, connection: sqlite3.Connection, sessions: tuple[dict[str, Any], ...],
        needle: str, limit: int,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for session in sessions:
            terminal_id = str(session["id"])
            oldest = self._oldest_seq(
                terminal_id, int(session["outputStartSeq"])
            )
            next_seq = int(session["nextSeq"])
            self._ensure_index(connection, terminal_id, oldest, next_seq)
            first_line_row = connection.execute(
                """SELECT MIN(line_number) FROM terminal_text_chunks
                    WHERE terminal_id = ? AND end_seq > ? AND start_seq < ?""",
                (terminal_id, oldest, next_seq),
            ).fetchone()
            first_line = int(first_line_row[0] or 1)
            rows = connection.execute(
                """SELECT line_number, text, created_at
                     FROM terminal_text_chunks
                    WHERE terminal_id = ? AND end_seq > ? AND start_seq < ?
                      AND instr(search_text, ?) > 0
                    ORDER BY line_number""",
                (terminal_id, oldest, next_seq, needle),
            ).fetchall()
            for row in rows:
                matches.append({
                    "terminalId": terminal_id,
                    "title": session["title"],
                    "line": int(row["line_number"]) - first_line + 1,
                    "text": str(row["text"]),
                    "createdAt": str(row["created_at"] or ""),
                })
                if len(matches) >= limit:
                    return matches
        return matches

    def _execute_query(
        self, connection: sqlite3.Connection, query: _WorkerQuery,
    ) -> Any:
        self.query_count += 1
        operation = query.operation
        arguments = query.arguments
        if operation == "history":
            return self._read_history(*arguments)
        if operation == "barrier":
            return None
        if operation == "reset_metadata":
            self._metadata_parsers.pop(str(arguments[0]), None)
            self._command_index_parsers.pop(str(arguments[0]), None)
            return None
        if operation == "screen":
            terminal_id, cols, rows, output_start_seq, next_seq = arguments
            state = self._screens.get(terminal_id)
            if state is None:
                state = self._screen_state(terminal_id, cols, rows)
                _oldest, _start, data = self._read_history(
                    terminal_id, output_start_seq, next_seq, output_start_seq
                )
                if data:
                    state[1].feed(state[2].decode(data, final=False))
            body = self._screen_body(state)
            with self._condition:
                self._screen_snapshots[terminal_id] = (next_seq, body)
            return body
        if operation == "commands":
            return self._commands_query(connection, *arguments)
        if operation == "command_output":
            terminal_id, command_id, output_start_seq, next_seq = arguments
            command = next((item for item in self._commands_query(
                connection, terminal_id, output_start_seq, next_seq
            ) if item["id"] == command_id), None)
            if command is None:
                raise LookupError("terminal command not found")
            _oldest, _actual, data = self._read_history(
                terminal_id, int(command["outputStartSeq"]),
                int(command["outputEndSeq"]), output_start_seq,
            )
            return command, data, plain_terminal_text(data)
        if operation == "search":
            return self._search_query(connection, *arguments)
        if operation == "replay":
            terminal_id, cursor, target, chunk_size, output_start_seq = arguments
            oldest = self._oldest_seq(terminal_id, output_start_seq)
            position = max(oldest, min(target, cursor))
            events: list[dict[str, Any]] = []
            while position < target:
                _oldest, actual, data = self._read_history(
                    terminal_id, position, min(target, position + chunk_size), oldest
                )
                if not data:
                    break
                end = actual + len(data)
                events.append({
                    "type": "output",
                    "seq": actual,
                    "nextSeq": end,
                    "createdAt": self._history_timestamp(
                        connection, terminal_id, actual
                    ),
                    "data": base64.b64encode(data).decode("ascii"),
                })
                position = end
            return events
        if operation == "remove":
            terminal_id = str(arguments[0])
            self._screens.pop(terminal_id, None)
            self._metadata_parsers.pop(terminal_id, None)
            self._command_index_parsers.pop(terminal_id, None)
            with self._condition:
                self._screen_snapshots.pop(terminal_id, None)
            with self._segment_lock(terminal_id):
                shutil.rmtree(self._segment_dir(terminal_id), ignore_errors=True)
                with contextlib.suppress(OSError):
                    self._legacy_path(terminal_id).unlink()
            for table in (
                "terminal_text_chunks", "terminal_commands", "terminal_index_state",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE terminal_id = ?", (terminal_id,)
                )
            connection.commit()
            return None
        raise ValueError(f"unknown terminal worker operation: {operation}")

    def _record_failure(self, exc: BaseException) -> None:
        with self._condition:
            self._failure = exc
            self._condition.notify_all()
            terminal_items = [
                item
                for pending in self._terminal_work.values()
                for item in pending
            ]
            self._terminal_work.clear()
            self._terminal_work_scheduled.clear()
        queued: list[Any] = terminal_items
        for pending_queue, is_main in (
            (self._queue, True), (self._query_queue, False),
        ):
            while True:
                try:
                    pending = pending_queue.get_nowait()
                except queue.Empty:
                    break
                queued.append(pending[3] if is_main else pending)
        for pending in queued:
            if isinstance(pending, _WorkerQuery) and not pending.future.done():
                pending.future.set_exception(
                    RuntimeError("terminal background worker failed")
                )

    def _take_terminal_work(self, terminal_id: str) -> Any:
        with self._condition:
            pending = self._terminal_work.get(terminal_id)
            if not pending:
                self._terminal_work.pop(terminal_id, None)
                self._terminal_work_scheduled.discard(terminal_id)
                return None
            item = pending.popleft()
            if isinstance(item, _ScreenUpdate):
                self._terminal_work_bytes = max(
                    0, self._terminal_work_bytes - len(item.data)
                )
            return item

    def _finish_terminal_work(self, terminal_id: str) -> bool:
        with self._condition:
            pending = self._terminal_work.get(terminal_id)
            if not pending:
                self._terminal_work.pop(terminal_id, None)
                self._terminal_work_scheduled.discard(terminal_id)
                return False
            return True

    def _handle_terminal_work(
        self, connection: sqlite3.Connection, item: Any,
    ) -> None:
        enqueued_at = float(getattr(item, "enqueued_at", time.monotonic()))
        self.worker_queue_wait_max_ms = max(
            self.worker_queue_wait_max_ms,
            (time.monotonic() - enqueued_at) * 1000,
        )
        if isinstance(item, _ScreenUpdate):
            self._feed_worker_screen(item)
            return
        if isinstance(item, _ScreenResize):
            state = self._screens.get(item.terminal_id)
            if state is not None:
                state[0].resize(lines=item.rows, columns=item.cols)
                with self._condition:
                    previous = self._screen_snapshots.get(
                        item.terminal_id, (0, {})
                    )
                    self._screen_snapshots[item.terminal_id] = (
                        previous[0], self._screen_body(state)
                    )
            return
        if isinstance(item, _WorkerQuery):
            try:
                item.future.set_result(self._execute_query(connection, item))
            except BaseException as exc:
                item.future.set_exception(exc)

    def _persist_batch(
        self, connection: sqlite3.Connection, token: int,
        items: tuple[_PersistenceItem, ...],
    ) -> None:
        retained_starts: dict[str, int] = {}
        for item in items:
            retained_start = self._retained_starts.get(item.terminal_id, 0)
            evicted_start: int | None = None
            if item.output:
                evicted_start = self._append_segments(item)
                if evicted_start is not None:
                    retained_start = evicted_start
                    retained_starts[item.terminal_id] = retained_start
            if item.output_events:
                connection.executemany(
                    """INSERT INTO terminal_output_events (
                           terminal_id, start_seq, end_seq, created_at
                       ) VALUES (?, ?, ?, ?)""",
                    [
                        (item.terminal_id, start, end, created_at)
                        for start, end, created_at in item.output_events
                    ],
                )
            self._index_output(connection, item)
            session_values = list(item.session_values)
            session_values[14] = max(int(session_values[14]), retained_start)
            connection.execute(_SESSION_UPSERT_SQL, tuple(session_values))
            connection.execute(_SESSION_METADATA_SQL, item.metadata_values)
            if evicted_start is not None:
                connection.execute(
                    """DELETE FROM terminal_output_events
                       WHERE terminal_id = ? AND end_seq <= ?""",
                    (item.terminal_id, retained_start),
                )
                connection.execute(
                    """DELETE FROM terminal_text_chunks
                       WHERE terminal_id = ? AND start_seq < ?""",
                    (item.terminal_id, retained_start),
                )
                connection.execute(
                    """DELETE FROM terminal_commands
                       WHERE terminal_id = ? AND command_start_seq < ?""",
                    (item.terminal_id, retained_start),
                )
        connection.commit()
        self.batch_count += 1
        with self._condition:
            self._retained_starts.update(retained_starts)
            self._completed = token
            self._condition.notify_all()

    def _run_main(self) -> None:
        self.thread_id = threading.get_ident()
        try:
            connection = sqlite3.connect(self._db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
        except BaseException as exc:
            self._record_failure(exc)
            return
        try:
            while True:
                _priority, _sequence, enqueued_at, entry = self._queue.get()
                self.worker_queue_wait_max_ms = max(
                    self.worker_queue_wait_max_ms,
                    (time.monotonic() - enqueued_at) * 1000,
                )
                if entry is _WORKER_STOP:
                    return
                if isinstance(entry, _TerminalWorkReady):
                    item = self._take_terminal_work(entry.terminal_id)
                    if item is not None:
                        self._handle_terminal_work(connection, item)
                    has_more = self._finish_terminal_work(entry.terminal_id)
                    if has_more:
                        self._put_main(0, entry)
                    continue
                if isinstance(entry, _WorkerQuery):
                    try:
                        entry.future.set_result(self._execute_query(connection, entry))
                    except BaseException as exc:
                        entry.future.set_exception(exc)
                    continue
                token, items = entry
                try:
                    self._persist_batch(connection, token, items)
                except BaseException as exc:
                    connection.rollback()
                    self._record_failure(exc)
                    return
        except BaseException as exc:
            self._record_failure(exc)
        finally:
            connection.close()

    def _run_queries(self) -> None:
        self.query_thread_id = threading.get_ident()
        try:
            connection = sqlite3.connect(self._db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
        except BaseException as exc:
            self._record_failure(exc)
            return
        try:
            while True:
                entry = self._query_queue.get()
                if entry is _WORKER_STOP:
                    return
                assert isinstance(entry, _WorkerQuery)
                try:
                    self.wait(entry.after_token)
                    self.query_queue_wait_max_ms = max(
                        self.query_queue_wait_max_ms,
                        (time.monotonic() - entry.enqueued_at) * 1000,
                    )
                    entry.future.set_result(self._execute_query(connection, entry))
                except BaseException as exc:
                    if not entry.future.done():
                        entry.future.set_exception(exc)
        except BaseException as exc:
            self._record_failure(exc)
        finally:
            connection.close()


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
    screen_decoder: Any = None
    screen_pending: deque[bytes] = field(default_factory=deque)
    screen_pending_bytes: int = 0
    screen_task: asyncio.Task[Any] | None = None
    pending_output: bytearray = field(default_factory=bytearray)
    pending_output_events: list[tuple[int, int, str]] = field(default_factory=list)
    persist_queued_bytes: int = 0
    persist_queued_events: int = 0
    persist_jobs: deque[tuple[int, int, int]] = field(default_factory=deque)
    pty_read_paused: bool = False
    cwd_uri: str = ""
    shell_title: str = ""
    integration_level: str = "none"
    command_state: str = ""
    last_command_exit_code: int | None = None
    connection_kind: str = "local"
    ssh_target: str = ""
    remote_cwd: str = ""
    tmux_session: str = ""
    connection_status: str = "local"
    disconnect_reason: str = ""
    reconnect_attempt: int = 0
    remote_lifecycle: str = ""
    remote_connected: bool = False
    connection_event: asyncio.Event = field(default_factory=asyncio.Event)
    reconnect_task: asyncio.Task[Any] | None = None
    osc_parser: OscMetadataParser = field(default_factory=OscMetadataParser)
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
        oldest_seq = self.output_start_seq
        display_title = (
            self.shell_title
            if self.shell_title and _DEFAULT_TITLE_RE.fullmatch(self.title.strip())
            else self.title
        )
        return {
            "id": self.id,
            "projectId": self.project_id,
            "title": self.title,
            "displayTitle": display_title,
            "cwd": self.cwd,
            "cwdUri": self.cwd_uri,
            "shellTitle": self.shell_title,
            "integrationLevel": self.integration_level,
            "commandState": self.command_state,
            "lastCommandExitCode": self.last_command_exit_code,
            "connectionKind": self.connection_kind,
            "sshTarget": self.ssh_target,
            "remoteCwd": self.remote_cwd,
            "tmuxSession": self.tmux_session,
            "connectionStatus": self.connection_status,
            "disconnectReason": self.disconnect_reason,
            "reconnectAttempt": self.reconnect_attempt,
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
        persistence_backlog_limit: int = DEFAULT_PERSISTENCE_BACKLOG_LIMIT,
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
        self._persistence_writer: _TerminalPersistenceWriter | None = None
        self._dirty_sessions: set[str] = set()
        self._pending_output_bytes = 0
        self.persistence_backlog_limit = max(
            PTY_READ_BUDGET * 2, int(persistence_backlog_limit)
        )
        self.persistence_backlog_low_water = self.persistence_backlog_limit // 2
        self._persistence_backlog_bytes = 0
        self._flush_handle: asyncio.Handle | None = None
        self._backpressure_handle: asyncio.Handle | None = None
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
        self._db.execute("PRAGMA busy_timeout=5000")
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
            CREATE TABLE IF NOT EXISTS terminal_output_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id TEXT NOT NULL,
                start_seq INTEGER NOT NULL,
                end_seq INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_output_events_terminal
                ON terminal_output_events(terminal_id, start_seq);
            CREATE TABLE IF NOT EXISTS terminal_text_chunks (
                chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                terminal_id TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                start_seq INTEGER NOT NULL,
                end_seq INTEGER NOT NULL,
                text TEXT NOT NULL,
                search_text TEXT NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_text_chunks_terminal
                ON terminal_text_chunks(terminal_id, start_seq);
            CREATE TABLE IF NOT EXISTS terminal_commands (
                terminal_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                command_start_seq INTEGER NOT NULL,
                command_text TEXT NOT NULL,
                output_start_seq INTEGER NOT NULL,
                output_end_seq INTEGER NOT NULL,
                exit_code INTEGER,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                running INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (terminal_id, command_id)
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_commands_terminal
                ON terminal_commands(terminal_id, output_start_seq);
            CREATE TABLE IF NOT EXISTS terminal_index_state (
                terminal_id TEXT PRIMARY KEY,
                indexed_next_seq INTEGER NOT NULL DEFAULT 0,
                command_capture_start_seq INTEGER,
                command_capture BLOB NOT NULL DEFAULT X'',
                running_output_start_seq INTEGER,
                running_command_text TEXT NOT NULL DEFAULT '',
                text_state_json TEXT NOT NULL DEFAULT ''
            );
            """
        )
        text_columns = {
            str(row[1])
            for row in self._db.execute("PRAGMA table_info(terminal_text_chunks)")
        }
        required_text_columns = {
            "terminal_id", "line_number", "start_seq", "end_seq", "text",
            "search_text", "complete", "created_at",
        }
        reset_terminal_indexes = not required_text_columns.issubset(text_columns)
        if reset_terminal_indexes:
            self._db.executescript(
                """DROP TABLE terminal_text_chunks;
                   CREATE TABLE terminal_text_chunks (
                       chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                       terminal_id TEXT NOT NULL,
                       line_number INTEGER NOT NULL,
                       start_seq INTEGER NOT NULL,
                       end_seq INTEGER NOT NULL,
                       text TEXT NOT NULL,
                       search_text TEXT NOT NULL,
                       complete INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT NOT NULL
                   );"""
            )
        self._db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_terminal_text_chunks_line
               ON terminal_text_chunks(terminal_id, line_number)"""
        )
        self._db.execute(
            """CREATE INDEX IF NOT EXISTS idx_terminal_text_chunks_terminal
               ON terminal_text_chunks(terminal_id, start_seq)"""
        )
        self._ensure_column(
            "terminal_index_state", "text_state_json", "TEXT NOT NULL DEFAULT ''"
        )
        if reset_terminal_indexes:
            self._db.execute("DELETE FROM terminal_commands")
            self._db.execute("DELETE FROM terminal_index_state")
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
        self._ensure_column("terminal_sessions", "cwd_uri", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "shell_title", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(
            "terminal_sessions", "integration_level", "TEXT NOT NULL DEFAULT 'none'"
        )
        self._ensure_column("terminal_sessions", "command_state", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "last_command_exit_code", "INTEGER")
        self._ensure_column(
            "terminal_sessions", "connection_kind", "TEXT NOT NULL DEFAULT 'local'"
        )
        self._ensure_column("terminal_sessions", "ssh_target", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "remote_cwd", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("terminal_sessions", "tmux_session", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(
            "terminal_sessions", "connection_status", "TEXT NOT NULL DEFAULT 'local'"
        )
        self._ensure_column(
            "terminal_sessions", "disconnect_reason", "TEXT NOT NULL DEFAULT ''"
        )
        self._ensure_column(
            "terminal_sessions", "reconnect_attempt", "INTEGER NOT NULL DEFAULT 0"
        )
        self._db.commit()
        self._persistence_writer = _TerminalPersistenceWriter(
            self.state_dir, output_limit=self.output_limit
        )
        self._load_sessions()

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        assert self._db is not None
        columns = {str(row[1]) for row in self._db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _scroll_path(self, terminal_id: str) -> Path:
        assert self.state_dir is not None
        return self.state_dir / "scrollback" / f"{terminal_id}.bin"

    def _scroll_segment_dir(self, terminal_id: str) -> Path:
        assert self.state_dir is not None
        return self.state_dir / "scrollback" / terminal_id

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
                cwd_uri=str(row["cwd_uri"] or ""),
                shell_title=str(row["shell_title"] or ""),
                integration_level=str(row["integration_level"] or "none"),
                command_state=str(row["command_state"] or ""),
                last_command_exit_code=row["last_command_exit_code"],
                connection_kind=str(row["connection_kind"] or "local"),
                ssh_target=str(row["ssh_target"] or ""),
                remote_cwd=str(row["remote_cwd"] or ""),
                tmux_session=str(row["tmux_session"] or ""),
                connection_status=str(row["connection_status"] or "local"),
                disconnect_reason=str(row["disconnect_reason"] or ""),
                reconnect_attempt=int(row["reconnect_attempt"] or 0),
                remote_connected=(
                    str(row["connection_kind"] or "local") == "ssh"
                    and str(row["connection_status"] or "")
                    in {"connected", "reconnecting"}
                ),
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
        repaired_any = False
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
                repaired_any = True
        if repaired_any:
            self.flush()

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

    def _upsert_session(self, session: TerminalSession) -> None:
        if self._db is None:
            return
        self._db.execute(_SESSION_UPSERT_SQL, self._session_values(session))
        self._db.execute(_SESSION_METADATA_SQL, self._metadata_values(session))

    @staticmethod
    def _session_values(session: TerminalSession) -> tuple[Any, ...]:
        return (
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
        )

    @staticmethod
    def _metadata_values(session: TerminalSession) -> tuple[Any, ...]:
        return (
            session.cwd_uri,
            session.shell_title,
            session.integration_level,
            session.command_state,
            session.last_command_exit_code,
            session.connection_kind,
            session.ssh_target,
            session.remote_cwd,
            session.tmux_session,
            session.connection_status,
            session.disconnect_reason,
            session.reconnect_attempt,
            session.id,
        )

    def _reap_persisted_output(self, session: TerminalSession | None = None) -> None:
        writer = self._persistence_writer
        if writer is None:
            return
        completed = writer.completed
        sessions = (session,) if session is not None else tuple(self._sessions.values())
        for current in sessions:
            while current.persist_jobs and current.persist_jobs[0][0] <= completed:
                _token, byte_count, event_count = current.persist_jobs.popleft()
                del current.pending_output[:byte_count]
                del current.pending_output_events[:event_count]
                current.persist_queued_bytes -= byte_count
                current.persist_queued_events -= event_count
                self._persistence_backlog_bytes = max(
                    0, self._persistence_backlog_bytes - byte_count
                )
            current.output_start_seq = max(
                current.output_start_seq, writer.retained_start(current.id)
            )

    def _persistence_item(self, session: TerminalSession) -> _PersistenceItem | None:
        byte_start = session.persist_queued_bytes
        event_start = session.persist_queued_events
        data = bytes(session.pending_output[byte_start:])
        events = tuple(session.pending_output_events[event_start:])
        if not data and not events:
            return None
        return _PersistenceItem(
            terminal_id=session.id,
            next_seq=session.next_seq,
            output=data,
            output_events=events,
            session_values=self._session_values(session),
            metadata_values=self._metadata_values(session),
        )

    def _submit_persistence_items(
        self, batches: list[tuple[TerminalSession, _PersistenceItem]],
    ) -> int:
        writer = self._persistence_writer
        if writer is None:
            return 0
        token = writer.submit([item for _session, item in batches])
        for session, item in batches:
            byte_count = len(item.output)
            event_count = len(item.output_events)
            if byte_count or event_count:
                session.persist_queued_bytes += byte_count
                session.persist_queued_events += event_count
                session.persist_jobs.append((token, byte_count, event_count))
                self._pending_output_bytes = max(
                    0, self._pending_output_bytes - byte_count
                )
                self._dirty_sessions.discard(session.id)
        return token

    def _flush_pending_outputs(self) -> int:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        writer = self._persistence_writer
        if writer is None or not self._dirty_sessions:
            return writer.submitted if writer is not None else 0
        self._reap_persisted_output()
        self._dirty_sessions.intersection_update(self._sessions)
        sessions = [
            self._sessions[terminal_id]
            for terminal_id in tuple(self._dirty_sessions)
            if terminal_id in self._sessions
        ]
        batches: list[tuple[TerminalSession, _PersistenceItem]] = []
        for session in sessions:
            item = self._persistence_item(session)
            if item is not None:
                batches.append((session, item))
        return self._submit_persistence_items(batches)

    def _schedule_output_flush(self, session: TerminalSession) -> None:
        if self.state_dir is None or not session.pending_output:
            return
        self._dirty_sessions.add(session.id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_pending_outputs()
            return
        if self._pending_output_bytes >= OUTPUT_FLUSH_THRESHOLD:
            if self._flush_handle is not None:
                self._flush_handle.cancel()
            self._flush_handle = loop.call_soon(self._flush_pending_outputs)
        elif self._flush_handle is None:
            self._flush_handle = loop.call_later(
                OUTPUT_FLUSH_INTERVAL_SECONDS, self._flush_pending_outputs
            )

    def _persistence_is_pressured(self) -> bool:
        if self._persistence_writer is None:
            return False
        self._reap_persisted_output()
        return self._persistence_backlog_bytes >= self.persistence_backlog_limit

    def _pause_posix_reader(self, session: TerminalSession) -> None:
        if session.master_fd is None or session.pty_read_paused:
            return
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().remove_reader(session.master_fd)
        session.pty_read_paused = True
        self._schedule_backpressure_poll()

    def _schedule_backpressure_poll(self) -> None:
        if self._backpressure_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._backpressure_handle = loop.call_later(
            PERSISTENCE_BACKLOG_POLL_SECONDS,
            self._poll_persistence_backpressure,
        )

    def _poll_persistence_backpressure(self) -> None:
        self._backpressure_handle = None
        self._reap_persisted_output()
        if self._persistence_backlog_bytes > self.persistence_backlog_low_water:
            self._schedule_backpressure_poll()
            return
        loop = asyncio.get_running_loop()
        for session in self._sessions.values():
            if not session.pty_read_paused or session.master_fd is None:
                continue
            session.pty_read_paused = False
            loop.add_reader(session.master_fd, self._read_posix_ready, session.id)

    async def _wait_for_persistence_capacity(self) -> None:
        while self._persistence_is_pressured():
            await asyncio.sleep(PERSISTENCE_BACKLOG_POLL_SECONDS)

    async def _worker_call(self, operation: str, *arguments: Any) -> Any:
        writer = self._persistence_writer
        if writer is None:
            raise RuntimeError("terminal background worker is unavailable")
        return await asyncio.wrap_future(writer.request(operation, *arguments))

    def flush(self) -> None:
        """Synchronously checkpoint pending terminal output and metadata."""
        token = self._flush_pending_outputs()
        if self._persistence_writer is not None:
            self._persistence_writer.wait(token)
            self._reap_persisted_output()

    def close_store(self) -> None:
        """Flush and stop the dedicated persistence writer."""
        for session in self._sessions.values():
            if session.reconnect_task is not None:
                session.reconnect_task.cancel()
                session.reconnect_task = None
        if self._backpressure_handle is not None:
            self._backpressure_handle.cancel()
            self._backpressure_handle = None
        self.flush()
        if self._persistence_writer is not None:
            self._persistence_writer.close()
            self._persistence_writer = None
        if self._db is not None:
            self._db.close()
            self._db = None

    def _persist_session(self, session: TerminalSession) -> None:
        if self._db is None:
            return
        writer = self._persistence_writer
        if writer is None:
            self._upsert_session(session)
            self._db.commit()
            return
        if self._db.in_transaction:
            self._db.commit()
        self._reap_persisted_output(session)
        item = self._persistence_item(session)
        if item is None:
            item = _PersistenceItem(
                terminal_id=session.id,
                next_seq=session.next_seq,
                output=b"",
                output_events=(),
                session_values=self._session_values(session),
                metadata_values=self._metadata_values(session),
            )
        self._submit_persistence_items([(session, item)])

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
        requested_cwd = str(cwd or "").strip()
        resolved_cwd = (
            str(self._resolve_cwd(project_id, requested_cwd))
            if requested_cwd
            else ""
        )
        from importlib import import_module

        kind, argv = import_module(
            "cyrene.tooling.backends.shell_runtime"
        ).interactive_argv()
        return await self.create_resolved(
            project_id,
            cwd=resolved_cwd,
            shell=kind,
            argv=list(argv),
            title=title,
            cols=cols,
            rows=rows,
        )

    async def create_ssh(
        self,
        project_id: str,
        *,
        ssh_target: str,
        remote_cwd: str = "",
        tmux_session: str = "",
        title: str = "",
        cwd: str = "",
        cols: int = 100,
        rows: int = 30,
        owner_chat_id: str = "",
        created_by: str = "user",
        owner_tool_call_id: str = "",
        wake_on_exit: bool = False,
        wake_note: str = "",
    ) -> dict[str, Any]:
        """Create a managed SSH PTY whose remote bootstrap is restartable."""
        local_cwd = str(self._resolve_cwd(project_id, cwd))
        requested_remote_cwd = str(remote_cwd or "")
        if not requested_remote_cwd:
            active_id = self.active_terminal_id(project_id)
            active = self._sessions.get(str(active_id or ""))
            if (
                active is not None
                and active.connection_kind == "ssh"
                and active.ssh_target == str(ssh_target or "").strip()
            ):
                requested_remote_cwd = active.remote_cwd
        launch = build_managed_ssh_launch(
            target=ssh_target,
            remote_cwd=requested_remote_cwd,
            tmux_session=tmux_session,
        )
        return await self.create_resolved(
            project_id,
            cwd=local_cwd,
            shell="ssh",
            argv=launch.argv,
            title=title,
            cols=cols,
            rows=rows,
            owner_chat_id=owner_chat_id,
            created_by=created_by,
            owner_tool_call_id=owner_tool_call_id,
            launch_mode="interactive",
            wake_on_exit=wake_on_exit,
            wake_note=wake_note,
            connection_kind="ssh",
            ssh_target=launch.target,
            remote_cwd=launch.remote_cwd,
            tmux_session=launch.tmux_session,
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
        connection_kind: str = "local",
        ssh_target: str = "",
        remote_cwd: str = "",
        tmux_session: str = "",
    ) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        requested_cwd = str(cwd or "").strip()
        resolved_cwd = (
            Path(requested_cwd).expanduser().resolve(strict=False)
            if requested_cwd
            else None
        )
        if not project_id:
            raise ValueError("projectId is required")
        if resolved_cwd is not None and not resolved_cwd.is_dir():
            raise ValueError("terminal cwd does not exist")
        if not argv:
            raise ValueError("terminal command is required")
        if resolved_cwd is None and self._persistence_writer is not None:
            # Drain preceding OSC 7 output before inheriting the active shell's
            # cwd. The worker posts metadata callbacks before completing this
            # barrier, and one loop turn applies those callbacks locally.
            await self._worker_call("barrier")
            await asyncio.sleep(0)
        async with self._lock:
            if resolved_cwd is None:
                active_id = self.active_terminal_id(project_id)
                active = self._sessions.get(str(active_id or ""))
                inherited_cwd = active.cwd if active is not None else ""
                resolved_cwd = self._resolve_cwd(project_id, inherited_cwd)
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
                connection_kind=(
                    "ssh" if str(connection_kind or "local") == "ssh" else "local"
                ),
                ssh_target=str(ssh_target or ""),
                remote_cwd=str(remote_cwd or ""),
                tmux_session=str(tmux_session or ""),
                connection_status=(
                    "connecting"
                    if str(connection_kind or "local") == "ssh" else "local"
                ),
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
                self.flush()
                self._sessions.pop(session.id, None)
                if self._db is not None:
                    self._db.execute("DELETE FROM terminal_sessions WHERE id = ?", (session.id,))
                    self._db.execute("DELETE FROM terminal_wakes WHERE terminal_id = ?", (session.id,))
                    self._db.commit()
            raise
        self._publish_list_change(session.project_id, "created", session.id)
        return session.public()

    @staticmethod
    def _reset_screen(session: TerminalSession) -> None:
        session.screen = pyte.Screen(session.cols, session.rows)
        session.stream = pyte.Stream(session.screen)
        session.screen_decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )

    @staticmethod
    def _feed_screen(session: TerminalSession, data: bytes) -> None:
        if session.stream is None:
            TerminalManager._reset_screen(session)
        session.stream.feed(session.screen_decoder.decode(bytes(data), final=False))

    @staticmethod
    def _take_screen_data(session: TerminalSession, limit: int) -> bytes:
        remaining = max(1, int(limit))
        parts: list[bytes] = []
        while session.screen_pending and remaining > 0:
            chunk = session.screen_pending[0]
            if len(chunk) <= remaining:
                parts.append(session.screen_pending.popleft())
                session.screen_pending_bytes -= len(chunk)
                remaining -= len(chunk)
            else:
                parts.append(chunk[:remaining])
                session.screen_pending[0] = chunk[remaining:]
                session.screen_pending_bytes -= remaining
                remaining = 0
        return b"".join(parts)

    async def _drain_screen(self, terminal_id: str) -> None:
        if self._persistence_writer is not None:
            return
        session = self._sessions.get(terminal_id)
        if session is None:
            return
        current = asyncio.current_task()
        try:
            while session.screen_pending:
                data = self._take_screen_data(session, SCREEN_DRAIN_BUDGET)
                if data:
                    self._feed_screen(session, data)
                await asyncio.sleep(0)
        finally:
            if session.screen_task is current:
                session.screen_task = None

    def _queue_screen_data(self, session: TerminalSession, data: bytes) -> None:
        if self._persistence_writer is not None:
            try:
                metadata_loop = asyncio.get_running_loop()
                metadata_callback = self._apply_worker_metadata
            except RuntimeError:
                metadata_loop = None
                metadata_callback = None
                changed = False
                for metadata in session.osc_parser.feed(
                    data, start_seq=session.next_seq - len(data)
                ):
                    changed = self._apply_osc_metadata(session, metadata) or changed
                if changed:
                    self._persist_session(session)
                    self._publish_state(session, reason="metadata")
            self._persistence_writer.submit_screen(
                session.id, data, cols=session.cols, rows=session.rows,
                start_seq=session.next_seq - len(data), next_seq=session.next_seq,
                metadata_loop=metadata_loop,
                metadata_callback=metadata_callback,
            )
            return
        session.screen_pending.append(data)
        session.screen_pending_bytes += len(data)
        if session.screen_pending_bytes > self.output_limit:
            self._reset_screen(session)
            session.screen_pending.clear()
            session.screen_pending.extend(chunk.data for chunk in session.output)
            session.screen_pending_bytes = session.output_bytes
        if session.screen_task is not None and not session.screen_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._drain_screen_now(session)
            return
        session.screen_task = loop.create_task(self._drain_screen(session.id))

    def _apply_worker_metadata(
        self, terminal_id: str, metadata_events: tuple[dict[str, Any], ...],
    ) -> None:
        session = self._sessions.get(terminal_id)
        if session is None:
            return
        changed = False
        for metadata in metadata_events:
            changed = self._apply_osc_metadata(session, metadata) or changed
        if changed:
            session.updated_at = _now_iso()
            self._persist_session(session)
            self._publish_state(session, reason="metadata")

    def _drain_screen_now(self, session: TerminalSession) -> None:
        if self._persistence_writer is not None:
            return
        while session.screen_pending:
            data = self._take_screen_data(session, SCREEN_DRAIN_BUDGET)
            if data:
                self._feed_screen(session, data)

    def screen_snapshot(self, terminal_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        if self._persistence_writer is not None:
            body = self._persistence_writer.cached_screen(
                session.id, session.next_seq
            )
            if body is None:
                body = self._persistence_writer.call(
                    "screen", session.id, session.cols, session.rows,
                    session.output_start_seq, session.next_seq,
                )
            return {"terminal": session.public(), **body}
        if session.screen is None:
            self._reset_screen(session)
            for chunk in session.output:
                self._feed_screen(session, chunk.data)
        self._drain_screen_now(session)
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

    async def screen_snapshot_async(self, terminal_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        if self._persistence_writer is None:
            return self.screen_snapshot(terminal_id)
        body = self._persistence_writer.cached_screen(
            session.id, session.next_seq
        )
        if body is None:
            body = await self._worker_call(
                "screen", session.id, session.cols, session.rows,
                session.output_start_seq, session.next_seq,
            )
        return {"terminal": session.public(), **body}

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
        self._reap_persisted_output(session)
        limit = max(1, min(int(max_bytes or 64 * 1024), 512 * 1024))
        oldest_seq = session.output_start_seq
        next_seq = session.next_seq
        requested_start_seq = None if cursor is None else max(0, int(cursor))
        if requested_start_seq is None:
            start_seq = max(oldest_seq, next_seq - limit)
        else:
            start_seq = min(next_seq, max(oldest_seq, requested_start_seq))

        data = self._history_bytes(session, start_seq, min(next_seq, start_seq + limit))
        oldest_seq = session.output_start_seq
        start_seq = max(oldest_seq, start_seq)
        end_seq = start_seq + len(data)

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
            "data": base64.b64encode(data).decode("ascii"),
            "requestedStartSeq": requested_start_seq,
            "startSeq": start_seq,
            "endSeq": end_seq,
            "oldestSeq": oldest_seq,
            "nextSeq": next_seq,
            "truncated": truncated_before or truncated_after,
            "truncatedBefore": truncated_before,
            "truncatedAfter": truncated_after,
        }

    async def scrollback_snapshot_async(
        self, terminal_id: str, *, cursor: int | None = None,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        if self._persistence_writer is None:
            return self.scrollback_snapshot(
                terminal_id, cursor=cursor, max_bytes=max_bytes
            )
        limit = max(1, min(int(max_bytes or 64 * 1024), 512 * 1024))
        requested = None if cursor is None else max(0, int(cursor))
        requested_start = (
            max(session.output_start_seq, session.next_seq - limit)
            if requested is None
            else min(session.next_seq, max(session.output_start_seq, requested))
        )
        start, data = await self._history_bytes_async(
            session, requested_start,
            min(session.next_seq, requested_start + limit),
        )
        oldest = session.output_start_seq
        end = start + len(data)
        truncated_before = start > oldest or (requested is not None and requested < oldest)
        truncated_after = end < session.next_seq
        return {
            "terminal": session.public(),
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
            "requestedStartSeq": requested,
            "startSeq": start,
            "endSeq": end,
            "oldestSeq": oldest,
            "nextSeq": session.next_seq,
            "truncated": truncated_before or truncated_after,
            "truncatedBefore": truncated_before,
            "truncatedAfter": truncated_after,
        }

    def _history_bytes(
        self, session: TerminalSession, start_seq: int, end_seq: int,
    ) -> bytes:
        self._reap_persisted_output(session)
        start = max(session.output_start_seq, min(session.next_seq, int(start_seq)))
        end = max(start, min(session.next_seq, int(end_seq)))
        if end <= start:
            return b""
        if self._persistence_writer is not None:
            pending = bytes(session.pending_output)
            pending_start = session.next_seq - len(pending)
            durable_end = min(end, pending_start)
            parts: list[bytes] = []
            if start < durable_end:
                oldest, _actual, data = self._persistence_writer.call(
                    "history", session.id, start, durable_end,
                    session.output_start_seq,
                )
                session.output_start_seq = max(session.output_start_seq, oldest)
                parts.append(data)
            pending_from = max(start, pending_start)
            if pending_from < end:
                offset = pending_from - pending_start
                parts.append(pending[offset:offset + end - pending_from])
            return b"".join(parts)
        parts: list[bytes] = []
        for chunk in session.output:
            if chunk.end <= start or chunk.start >= end:
                continue
            left = max(start, chunk.start) - chunk.start
            right = min(end, chunk.end) - chunk.start
            parts.append(chunk.data[left:right])
        return b"".join(parts)

    async def _history_bytes_async(
        self, session: TerminalSession, start_seq: int, end_seq: int,
    ) -> tuple[int, bytes]:
        self._reap_persisted_output(session)
        start = max(session.output_start_seq, min(session.next_seq, int(start_seq)))
        end = max(start, min(session.next_seq, int(end_seq)))
        if end <= start:
            return start, b""
        if self._persistence_writer is None:
            return start, self._history_bytes(session, start, end)
        pending = bytes(session.pending_output)
        pending_start = session.next_seq - len(pending)
        durable_end = min(end, pending_start)
        parts: list[bytes] = []
        actual_start = start
        if start < durable_end:
            oldest, actual_start, data = await self._worker_call(
                "history", session.id, start, durable_end,
                session.output_start_seq,
            )
            session.output_start_seq = max(session.output_start_seq, oldest)
            parts.append(data)
        pending_from = max(start, pending_start)
        if pending_from < end:
            if not parts:
                actual_start = pending_from
            offset = pending_from - pending_start
            parts.append(pending[offset:offset + end - pending_from])
        return actual_start, b"".join(parts)

    def iter_replay(
        self,
        terminal_id: str,
        cursor: int = 0,
        *,
        chunk_size: int = 256 * 1024,
        end_seq: int | None = None,
    ):
        session = self.get(terminal_id)
        target = session.next_seq if end_seq is None else min(session.next_seq, int(end_seq))
        size = max(4096, min(int(chunk_size), 512 * 1024))
        if self._persistence_writer is not None:
            self._flush_pending_outputs()
            events = self._persistence_writer.call(
                "replay", session.id, int(cursor or 0), target, size,
                session.output_start_seq,
            )
            self._reap_persisted_output(session)
            yield from events
            return
        position = max(session.output_start_seq, min(session.next_seq, int(cursor or 0)))
        while position < target:
            data = self._history_bytes(
                session, position, min(target, position + size)
            )
            if not data:
                break
            end = position + len(data)
            yield {
                "type": "output",
                "seq": position,
                "nextSeq": end,
                "createdAt": self._history_timestamp(session.id, position),
                "data": base64.b64encode(data).decode("ascii"),
            }
            position = end

    async def replay_async(
        self, terminal_id: str, cursor: int = 0, *,
        chunk_size: int = 256 * 1024, end_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        session = self.get(terminal_id)
        if self._persistence_writer is None:
            return list(self.iter_replay(
                terminal_id, cursor, chunk_size=chunk_size, end_seq=end_seq
            ))
        target = session.next_seq if end_seq is None else min(session.next_seq, int(end_seq))
        size = max(4096, min(int(chunk_size), 512 * 1024))
        self._flush_pending_outputs()
        events = await self._worker_call(
            "replay", session.id, int(cursor or 0), target, size,
            session.output_start_seq,
        )
        self._reap_persisted_output(session)
        if events:
            session.output_start_seq = max(
                session.output_start_seq, int(events[0]["seq"])
            )
        return list(events)

    def _history_timestamp(self, terminal_id: str, seq: int) -> str:
        position = max(0, int(seq))
        session = self._sessions.get(terminal_id)
        if session is not None:
            pending = next(
                (
                    created_at
                    for start, _end, created_at in reversed(
                        session.pending_output_events
                    )
                    if start <= position
                ),
                "",
            )
            if pending:
                return pending
        if self._db is not None:
            row = self._db.execute(
                """SELECT created_at FROM terminal_output_events
                   WHERE terminal_id = ? AND start_seq <= ?
                   ORDER BY start_seq DESC LIMIT 1""",
                (terminal_id, position),
            ).fetchone()
            if row:
                return str(row[0])
        return ""

    def _history_events(self, session: TerminalSession):
        history_start = session.output_start_seq
        history_end = session.next_seq
        history = self._history_bytes(session, history_start, history_end)
        if not history:
            return
        if self._db is not None:
            rows = list(self._db.execute(
                """SELECT start_seq, end_seq, created_at
                   FROM terminal_output_events WHERE terminal_id = ?
                   ORDER BY start_seq""",
                (session.id,),
            ).fetchall())
            rows.extend(session.pending_output_events)
            cursor = history_start
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_start, row_end, created_at = (
                        int(row["start_seq"]),
                        int(row["end_seq"]),
                        str(row["created_at"]),
                    )
                else:
                    row_start, row_end, created_at = row
                start = max(history_start, row_start, cursor)
                end = min(history_end, row_end)
                if start > cursor:
                    yield (
                        cursor,
                        start,
                        session.created_at,
                        history[cursor - history_start:start - history_start],
                    )
                if end > start:
                    yield (
                        start,
                        end,
                        created_at,
                        history[start - history_start:end - history_start],
                    )
                    cursor = end
            if rows:
                if cursor < history_end:
                    yield (
                        cursor,
                        history_end,
                        self._history_timestamp(session.id, cursor) or session.created_at,
                        history[cursor - history_start:],
                    )
                return
        yield history_start, history_end, session.created_at, history

    def search_history(
        self, project_id: str, query: str, *, terminal_id: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("terminal history query is required")
        bounded = max(1, min(int(limit or 100), 500))
        matches: list[dict[str, Any]] = []
        sessions = [
            session for session in self._sessions.values()
            if session.project_id == str(project_id or "")
            and (not terminal_id or session.id == str(terminal_id))
        ]
        if self._persistence_writer is not None:
            self._flush_pending_outputs()
            specs = tuple({
                "id": session.id,
                "title": session.title,
                "createdAt": session.created_at,
                "outputStartSeq": session.output_start_seq,
                "nextSeq": session.next_seq,
            } for session in sessions)
            result = self._persistence_writer.call(
                "search", specs, needle, bounded
            )
            self._reap_persisted_output()
            return list(result)
        for session in sessions:
            pending = ""
            pending_at = ""
            line_number = 0
            for _start, _end, created_at, data in self._history_events(session):
                line_timestamp = pending_at if pending else created_at
                text = pending + plain_terminal_text(data)
                lines = text.split("\n")
                pending = lines.pop() if lines else ""
                for line in lines:
                    line_number += 1
                    if needle in line.casefold():
                        matches.append({
                            "terminalId": session.id,
                            "title": session.title,
                            "line": line_number,
                            "text": line,
                            "createdAt": line_timestamp,
                        })
                        if len(matches) >= bounded:
                            return matches
                    line_timestamp = created_at
                pending_at = line_timestamp
            if pending:
                line_number += 1
                if needle in pending.casefold():
                    matches.append({
                        "terminalId": session.id,
                        "title": session.title,
                        "line": line_number,
                        "text": pending,
                        "createdAt": pending_at,
                    })
                    if len(matches) >= bounded:
                        return matches
        return matches

    async def search_history_async(
        self, project_id: str, query: str, *, terminal_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._persistence_writer is None:
            return self.search_history(
                project_id, query, terminal_id=terminal_id, limit=limit
            )
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("terminal history query is required")
        bounded = max(1, min(int(limit or 100), 500))
        sessions = [
            session for session in self._sessions.values()
            if session.project_id == str(project_id or "")
            and (not terminal_id or session.id == str(terminal_id))
        ]
        specs = tuple({
            "id": session.id,
            "title": session.title,
            "createdAt": session.created_at,
            "outputStartSeq": session.output_start_seq,
            "nextSeq": session.next_seq,
        } for session in sessions)
        self._flush_pending_outputs()
        result = await self._worker_call("search", specs, needle, bounded)
        self._reap_persisted_output()
        return list(result)

    def commands(self, terminal_id: str) -> list[dict[str, Any]]:
        session = self.get(terminal_id)
        if self._persistence_writer is not None:
            self._flush_pending_outputs()
            result = self._persistence_writer.call(
                "commands", session.id, session.output_start_seq, session.next_seq
            )
            self._reap_persisted_output(session)
            return list(result)
        data = self._history_bytes(session, session.output_start_seq, session.next_seq)
        return osc133_commands(
            data,
            base_seq=session.output_start_seq,
            timestamp_at=lambda seq: self._history_timestamp(session.id, seq),
        )

    async def commands_async(self, terminal_id: str) -> list[dict[str, Any]]:
        session = self.get(terminal_id)
        if self._persistence_writer is None:
            return self.commands(terminal_id)
        self._flush_pending_outputs()
        result = await self._worker_call(
            "commands", session.id, session.output_start_seq, session.next_seq
        )
        self._reap_persisted_output(session)
        return list(result)

    def command_output(self, terminal_id: str, command_id: str) -> dict[str, Any]:
        session = self.get(terminal_id)
        if self._persistence_writer is not None:
            self._flush_pending_outputs()
            command, data, text = self._persistence_writer.call(
                "command_output", session.id, command_id,
                session.output_start_seq, session.next_seq,
            )
            self._reap_persisted_output(session)
            return {
                "terminal": session.public(),
                "command": command,
                "encoding": "base64",
                "data": base64.b64encode(data).decode("ascii"),
                "text": text,
            }
        command = next(
            (item for item in self.commands(terminal_id) if item["id"] == command_id),
            None,
        )
        if command is None:
            raise LookupError("terminal command not found")
        start = int(command["outputStartSeq"])
        end = int(command["outputEndSeq"])
        data = self._history_bytes(session, start, end)
        return {
            "terminal": session.public(),
            "command": command,
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
            "text": plain_terminal_text(data),
        }

    async def command_output_async(
        self, terminal_id: str, command_id: str,
    ) -> dict[str, Any]:
        session = self.get(terminal_id)
        if self._persistence_writer is None:
            return self.command_output(terminal_id, command_id)
        self._flush_pending_outputs()
        command, data, text = await self._worker_call(
            "command_output", session.id, command_id,
            session.output_start_seq, session.next_seq,
        )
        self._reap_persisted_output(session)
        return {
            "terminal": session.public(),
            "command": command,
            "encoding": "base64",
            "data": base64.b64encode(data).decode("ascii"),
            "text": text,
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
        note: str,
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
            "This is an internal wake notification, not a user message. "
            "Use code.shell.read with the terminal_id above to inspect the terminal, "
            "then continue the prior work. Do not wait for this process again.",
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
        if session.connection_kind == "ssh":
            session.connection_event.clear()
            launch = build_managed_ssh_launch(
                target=session.ssh_target,
                remote_cwd=session.remote_cwd,
                tmux_session=session.tmux_session,
            )
            session.argv = list(launch.argv)
            command = list(launch.argv)
            session.integration_level = "none"
            session.connection_status = (
                "reconnecting" if session.reconnect_attempt else "connecting"
            )
            session.disconnect_reason = ""
            session.remote_lifecycle = ""
        else:
            command = list(session.argv)
        if self.state_dir is not None and session.connection_kind != "ssh":
            launch = prepare_shell_integration(
                shell=session.shell,
                argv=session.argv,
                env=env,
                runtime_dir=self.state_dir,
                launch_mode=session.launch_mode,
            )
            command = launch.argv
            env = launch.env
            session.integration_level = launch.integration_level
        session.osc_parser.reset()
        if self._persistence_writer is not None:
            await self._worker_call("reset_metadata", session.id)
        session.command_state = ""

        def child_setup() -> None:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=session.cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=child_setup,
                close_fds=True,
            )
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        session.process = process
        session.master_fd = master_fd
        session.pid = process.pid
        session.status = "running"
        session.updated_at = _now_iso()
        self._persist_session(session)
        loop = asyncio.get_running_loop()
        session.pty_read_paused = False
        loop.add_reader(master_fd, self._read_posix_ready, session.id)
        session.wait_task = asyncio.create_task(self._wait_posix(session.id))
        self._publish_state(session)

    async def _spawn_windows(self, session: TerminalSession) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # pragma: no cover - Windows packaging guard
            raise RuntimeError("pywinpty is required for terminal support on Windows") from exc

        env = _terminal_environment()
        if session.connection_kind == "ssh":
            session.connection_event.clear()
            launch = build_managed_ssh_launch(
                target=session.ssh_target,
                remote_cwd=session.remote_cwd,
                tmux_session=session.tmux_session,
            )
            session.argv = list(launch.argv)
            command = list(launch.argv)
            session.integration_level = "none"
            session.connection_status = (
                "reconnecting" if session.reconnect_attempt else "connecting"
            )
            session.disconnect_reason = ""
            session.remote_lifecycle = ""
        else:
            command = session.argv
        if self.state_dir is not None and session.connection_kind != "ssh":
            launch = prepare_shell_integration(
                shell=session.shell,
                argv=session.argv,
                env=env,
                runtime_dir=self.state_dir,
                launch_mode=session.launch_mode,
            )
            command = launch.argv
            env = launch.env
            session.integration_level = launch.integration_level
        session.osc_parser.reset()
        if self._persistence_writer is not None:
            await self._worker_call("reset_metadata", session.id)
        session.command_state = ""
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
        if self._persistence_is_pressured():
            self._pause_posix_reader(session)
            return
        try:
            data = os.read(session.master_fd, PTY_READ_BUDGET)
        except (BlockingIOError, OSError):
            return
        if data:
            self._append_output(session, data)
            if self._persistence_is_pressured():
                self._pause_posix_reader(session)

    async def _read_windows(self, terminal_id: str) -> None:  # pragma: no cover - Windows only
        session = self._sessions.get(terminal_id)
        if not session or session.winpty is None:
            return
        try:
            reached_eof = False
            while (
                session.launch_mode == "interactive"
                or session.winpty.isalive()
            ):
                await self._wait_for_persistence_capacity()
                try:
                    text = await asyncio.to_thread(session.winpty.read, 4096)
                except EOFError:
                    reached_eof = True
                    break
                if text:
                    self._append_output(
                        session, str(text).encode("utf-8", errors="replace")
                    )

            loop = asyncio.get_running_loop()
            drain_idle_deadline = (
                loop.time() + WINDOWS_POST_EXIT_DRAIN_IDLE_SECONDS
            )
            while not reached_eof and loop.time() < drain_idle_deadline:
                await self._wait_for_persistence_capacity()
                ready = await asyncio.to_thread(
                    _winpty_output_ready,
                    session.winpty,
                    WINDOWS_POST_EXIT_DRAIN_POLL_SECONDS,
                )
                if not ready:
                    continue
                try:
                    text = await asyncio.to_thread(session.winpty.read, 4096)
                except EOFError:
                    break
                if text:
                    self._append_output(session, str(text).encode("utf-8", errors="replace"))
                    drain_idle_deadline = (
                        loop.time() + WINDOWS_POST_EXIT_DRAIN_IDLE_SECONDS
                    )
            exit_code = await asyncio.to_thread(session.winpty.wait)
        except asyncio.CancelledError:
            raise
        except Exception:
            exit_code = None
        await self._synchronize_exit_metadata(session)
        self._mark_exited(session, exit_code)

    async def _wait_posix(self, terminal_id: str) -> None:
        session = self._sessions.get(terminal_id)
        if not session or session.process is None:
            return
        exit_code = await session.process.wait()
        if session.master_fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(session.master_fd)
            while session.master_fd is not None:
                await self._wait_for_persistence_capacity()
                try:
                    data = os.read(session.master_fd, PTY_READ_BUDGET)
                except (BlockingIOError, OSError):
                    break
                if not data:
                    break
                self._append_output(session, data)
                await asyncio.sleep(0)
            with contextlib.suppress(OSError):
                os.close(session.master_fd)
            session.master_fd = None
        await self._synchronize_exit_metadata(session)
        self._mark_exited(session, exit_code)

    async def _synchronize_exit_metadata(self, session: TerminalSession) -> None:
        if self._persistence_writer is None:
            return
        self._flush_pending_outputs()
        await self._worker_call("barrier")
        await asyncio.sleep(0)

    def _mark_exited(self, session: TerminalSession, exit_code: int | None) -> None:
        self._drain_screen_now(session)
        session.exit_code = exit_code
        session.status = "closed" if session.closing else "exited"
        session.pid = None
        now = _now_iso()
        session.exit_at = now
        if session.closing:
            session.exit_reason = "deleted"
            if session.connection_kind == "ssh":
                session.connection_status = "exited"
                session.disconnect_reason = "user_exit"
        elif session.connection_kind == "ssh" and session.remote_lifecycle in {
            "user_exit", "tmux_detached", "tmux_ended",
        }:
            session.exit_reason = session.remote_lifecycle
            session.connection_status = (
                "detached"
                if session.remote_lifecycle == "tmux_detached" else "exited"
            )
            session.disconnect_reason = session.remote_lifecycle
        elif session.connection_kind == "ssh" and session.remote_connected:
            session.exit_reason = "transport_lost"
            session.disconnect_reason = "transport_lost"
            session.connection_status = "reconnecting"
            if self._schedule_remote_reconnect(session):
                session.status = "starting"
            else:
                session.connection_status = "disconnected"
                session.disconnect_reason = "reconnect_exhausted"
        elif session.connection_kind == "ssh":
            session.exit_reason = "connection_failed"
            session.connection_status = "disconnected"
            session.disconnect_reason = "connection_failed"
        elif exit_code is None:
            session.exit_reason = "pty_lost"
        elif exit_code < 0:
            session.exit_reason = "signal"
        else:
            session.exit_reason = "process_exit"
        session.updated_at = now
        session.connection_event.set()
        self._persist_session(session)
        if session.status != "starting":
            self._ready_wake(session, exit_code=exit_code)
        self._publish_state(session)
        self._publish_list_change(session.project_id, "status", session.id)

    def _schedule_remote_reconnect(self, session: TerminalSession) -> bool:
        if session.closing or session.reconnect_task is not None:
            return False
        if session.reconnect_attempt >= len(SSH_RECONNECT_DELAYS):
            return False
        delay = SSH_RECONNECT_DELAYS[session.reconnect_attempt]
        session.reconnect_attempt += 1
        session.connection_status = "reconnecting"
        session.reconnect_task = asyncio.create_task(
            self._reconnect_remote(session.id, delay)
        )
        return True

    async def _reconnect_remote(self, terminal_id: str, delay: float) -> None:
        session = self._sessions.get(terminal_id)
        if session is None:
            return
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(max(0.0, float(delay)))
            if session.closing or session.status != "starting":
                return
            session.process = None
            session.master_fd = None
            session.winpty = None
            self._append_output(
                session,
                (
                    "\r\n[Cyrene reconnecting SSH "
                    f"({session.reconnect_attempt}/{len(SSH_RECONNECT_DELAYS)})…]\r\n"
                ).encode("utf-8"),
            )
            try:
                if sys.platform == "win32":  # pragma: no cover - Windows only
                    await self._spawn_windows(session)
                else:
                    await self._spawn_posix(session)
            except Exception:
                session.status = "starting"
                session.connection_status = "reconnecting"
                session.disconnect_reason = "transport_lost"
                session.updated_at = _now_iso()
                self._persist_session(session)
                self._publish_state(session, reason="reconnect")
                session.reconnect_task = None
                if not self._schedule_remote_reconnect(session):
                    session.status = "exited"
                    session.connection_status = "disconnected"
                    session.disconnect_reason = "reconnect_exhausted"
                    self._persist_session(session)
                    self._ready_wake(session, exit_code=session.exit_code)
                    self._publish_state(session, reason="reconnect")
                    self._publish_list_change(
                        session.project_id, "status", session.id
                    )
        finally:
            if session.reconnect_task is current_task:
                session.reconnect_task = None

    def _append_output(self, session: TerminalSession, data: bytes) -> None:
        if not data:
            return
        self._reap_persisted_output(session)
        created_at = _now_iso()
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
        session.updated_at = created_at
        if self.state_dir is None:
            session.output_start_seq = (
                session.output[0].start if session.output else session.next_seq
            )
        else:
            session.pending_output.extend(data)
            if (
                len(session.pending_output_events) > session.persist_queued_events
                and session.pending_output_events[-1][1] == start
            ):
                event_start, _event_end, event_created_at = (
                    session.pending_output_events[-1]
                )
                session.pending_output_events[-1] = (
                    event_start, end, event_created_at
                )
            else:
                session.pending_output_events.append((start, end, created_at))
            self._pending_output_bytes += len(data)
            self._persistence_backlog_bytes += len(data)
            self._schedule_output_flush(session)
        metadata_changed = False
        if self._persistence_writer is None:
            for metadata in session.osc_parser.feed(data, start_seq=start):
                metadata_changed = (
                    self._apply_osc_metadata(session, metadata) or metadata_changed
                )
        event = {
            "type": "output",
            "seq": start,
            "nextSeq": end,
            "createdAt": created_at,
            "data": base64.b64encode(data).decode("ascii"),
        }
        self._publish(session, event)
        if metadata_changed:
            self._publish_state(session, reason="metadata")
        self._queue_screen_data(session, data)

    @staticmethod
    def _apply_osc_metadata(
        session: TerminalSession, metadata: dict[str, Any],
    ) -> bool:
        kind = str(metadata.get("kind") or "")
        if kind in {"context", "profile"}:
            return False
        if kind == "lifecycle":
            if session.connection_kind != "ssh":
                return False
            value = str(metadata.get("value") or "")
            if value not in {
                "connected", "user_exit", "tmux_detached", "tmux_ended",
            }:
                return False
            before = (
                session.remote_lifecycle,
                session.connection_status,
                session.disconnect_reason,
                session.reconnect_attempt,
            )
            session.remote_lifecycle = value
            if value == "connected":
                session.remote_connected = True
                session.connection_status = "connected"
                session.disconnect_reason = ""
                session.reconnect_attempt = 0
            else:
                session.connection_status = (
                    "detached" if value == "tmux_detached" else "exited"
                )
                session.disconnect_reason = value
            session.connection_event.set()
            return before != (
                session.remote_lifecycle,
                session.connection_status,
                session.disconnect_reason,
                session.reconnect_attempt,
            )
        if kind == "integration":
            value = str(metadata.get("value") or "none")
            if value not in {"none", "basic", "full"} or value == session.integration_level:
                return False
            session.integration_level = value
            return True
        if kind == "title":
            value = "".join(
                character
                for character in str(metadata.get("value") or "")
                if ord(character) >= 32
            ).strip()[:512]
            if value == session.shell_title:
                return False
            session.shell_title = value
            return True
        if kind == "cwd":
            uri = str(metadata.get("uri") or "")[:4096]
            changed = uri != session.cwd_uri
            session.cwd_uri = uri
            value = str(metadata.get("value") or "")
            if session.connection_kind == "ssh":
                if value.startswith("/") or value == "~" or value.startswith("~/"):
                    if value != session.remote_cwd:
                        session.remote_cwd = value
                        changed = True
                return changed
            host = str(metadata.get("host") or "").casefold()
            local_hosts = {
                "", "localhost", socket.gethostname().casefold(),
            }
            if host not in local_hosts:
                return changed
            candidate = Path(value).expanduser() if value else None
            if (
                candidate is not None
                and candidate.is_absolute()
                and candidate.is_dir()
            ):
                resolved = str(candidate.resolve(strict=False))
                if resolved != session.cwd:
                    session.cwd = resolved
                    changed = True
            return changed
        states = {
            "prompt": "prompt",
            "command": "command",
            "output": "output",
            "finished": "finished",
        }
        state = states.get(kind)
        if state is None:
            return False
        changed = state != session.command_state
        session.command_state = state
        if kind == "finished":
            exit_code = metadata.get("exitCode")
            if exit_code != session.last_command_exit_code:
                session.last_command_exit_code = exit_code
                changed = True
        return changed

    def _publish_state(self, session: TerminalSession, *, reason: str = "") -> None:
        event = {"type": "state", "terminal": session.public()}
        if reason:
            event["reason"] = reason
        self._publish(session, event)

    @staticmethod
    def _publish_list_change(project_id: str, change: str, terminal_id: str = "") -> None:
        from cyrene.observability.debug import publish_event_sync

        publish_event_sync({
            "type": "terminal_list_changed",
            "change": change,
            "project_id": str(project_id or ""),
            "terminal_id": str(terminal_id or ""),
        })

    @staticmethod
    def _publish(session: TerminalSession, event: dict[str, Any]) -> None:
        for subscriber_queue in tuple(session.subscribers):
            if subscriber_queue.full():
                while True:
                    try:
                        subscriber_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                subscriber_queue.put_nowait({
                    "type": "resync_required",
                    "nextSeq": session.next_seq,
                })
                continue
            with contextlib.suppress(asyncio.QueueFull):
                subscriber_queue.put_nowait(event)

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
        self._publish_list_change(session.project_id, "renamed", session.id)
        return session.public()

    def replay(self, terminal_id: str, cursor: int = 0) -> list[dict[str, Any]]:
        return list(self.iter_replay(terminal_id, cursor))

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

    async def wait_until_connected(
        self, terminal_id: str, *, timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Wait for the managed remote launcher to confirm shell readiness."""
        session = self.get(terminal_id)
        if session.connection_kind != "ssh":
            return session.public()
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            if session.remote_connected and session.connection_status == "connected":
                return session.public()
            if session.status in {"closed", "exited"} or session.connection_status in {
                "detached", "disconnected", "exited",
            }:
                reason = session.disconnect_reason or session.exit_reason or "connection_failed"
                raise RuntimeError(f"SSH connection did not become ready: {reason}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("SSH connection did not become ready before timeout")
            session.connection_event.clear()
            if session.remote_connected and session.connection_status == "connected":
                continue
            try:
                await asyncio.wait_for(session.connection_event.wait(), timeout=remaining)
            except TimeoutError as exc:
                raise RuntimeError(
                    "SSH connection did not become ready before timeout"
                ) from exc

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
                await asyncio.to_thread(_write_winpty_input, session.winpty, text)
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
        if self._persistence_writer is not None:
            self._persistence_writer.resize_screen(session.id, cols=cols, rows=rows)
        elif session.screen is not None:
            self._drain_screen_now(session)
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
        self._publish_list_change(project_id, "layout")
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
        self._publish_list_change(project_id, "activated", normalized or "")
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
            if session.reconnect_task is None:
                raise RuntimeError("terminal restart is already in progress")
            session.reconnect_task.cancel()
            session.reconnect_task = None
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
        session.reconnect_attempt = 0
        session.remote_connected = False
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
            self._publish_list_change(session.project_id, "status", session.id)
            raise RuntimeError(f"terminal restart failed: {exc}") from exc
        self._publish_list_change(session.project_id, "restarted", session.id)
        return session.public()

    async def close(self, terminal_id: str, *, remove: bool = False) -> dict[str, Any]:
        session = self.get(terminal_id)
        session.closing = True
        if session.reconnect_task is not None:
            session.reconnect_task.cancel()
            session.reconnect_task = None
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
        elif session.status == "starting":
            session.status = "closed"
            session.exit_reason = "deleted"
            session.exit_at = _now_iso()
            if session.connection_kind == "ssh":
                session.connection_status = "exited"
                session.disconnect_reason = "user_exit"
                session.connection_event.set()
            self._persist_session(session)
        result = session.public()
        if remove:
            async with self._lock:
                self._drain_screen_now(session)
                self.flush()
                self._dirty_sessions.discard(session.id)
                if self._persistence_writer is not None:
                    self._persistence_writer.call("remove", session.id)
                self._sessions.pop(session.id, None)
                if self._db is not None:
                    self._db.execute("DELETE FROM terminal_sessions WHERE id = ?", (session.id,))
                    self._db.execute(
                        "DELETE FROM terminal_input_events WHERE terminal_id = ?",
                        (session.id,),
                    )
                    self._db.execute(
                        "DELETE FROM terminal_output_events WHERE terminal_id = ?",
                        (session.id,),
                    )
                    for table in (
                        "terminal_text_chunks", "terminal_commands",
                        "terminal_index_state",
                    ):
                        self._db.execute(
                            f"DELETE FROM {table} WHERE terminal_id = ?",
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
        self._publish_list_change(
            session.project_id,
            "deleted" if remove else "closed",
            session.id,
        )
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
