"""Segmented scrollback storage, independent of worker scheduling and SQLite.

The writer supplies its existing condition for lock registration. Per-terminal
locks still cover migration, reads and eviction across the two worker threads.
"""
from pathlib import Path
from typing import Any
import os
import shutil
import threading


class ScrollbackSegments:
    def __init__(self, state_dir, output_limit, segment_size, condition):
        self._state_dir = state_dir
        self._output_limit = output_limit
        self._segment_size = segment_size
        self._condition = condition
        self._segment_locks: dict[str, threading.RLock] = {}
        self.bytes_written = 0
        self.bytes_read = 0
        self.segments_deleted = 0
        self.eviction_count = 0

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
        """Move an existing single-file scrollback into segmented storage."""
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

    def _append_segments(self, item: Any) -> int | None:
        with self._segment_lock(item.terminal_id):
            return self._append_segments_locked(item)

    def _append_segments_locked(self, item: Any) -> int | None:
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
