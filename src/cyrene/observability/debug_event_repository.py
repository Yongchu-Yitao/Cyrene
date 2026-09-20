"""Repository for live and persisted debug events."""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any


class DebugEventRepository:
    """Read debug events while explicitly skipping malformed JSONL records."""

    malformed_line_policy = "skip"
    unreadable_file_policy = "skip"

    def __init__(
        self,
        data_dir: Path,
        *,
        recent_events: Callable[[int], list[dict[str, Any]]],
        full_event: Callable[[str], dict[str, Any] | None],
        subscribe_events: Callable[..., AsyncIterator[dict[str, Any]]],
    ) -> None:
        self.data_dir = Path(data_dir)
        self._recent_events = recent_events
        self._full_event = full_event
        self._subscribe_events = subscribe_events
        self._summary_cache = OrderedDict()
        self._summary_bytes = 0
        self._summary_lock = threading.RLock()

    def subscribe(self, session_id: str = "") -> AsyncIterator[dict[str, Any]]:
        return self._subscribe_events(session_id=session_id)

    def recent_summaries(self, session_id: str = "") -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for event in self._recent_events(50):
            if session_id and event.get("session_id") not in (session_id, ""):
                continue
            event_id = event.get("event_id", "")
            if event_id:
                items.append(
                    {
                        "id": event_id,
                        "type": event.get("type", "?"),
                        "caller": event.get("caller", "?"),
                    }
                )
        return {"events": items}

    def get(self, event_id: str) -> dict[str, Any] | None:
        return self._full_event(event_id)

    def get_llm_call(self, event_id: str) -> dict[str, Any] | None:
        event = self.get(event_id)
        if event is None or event.get("type") != "llm_call":
            return None
        return event

    def context_events(self, limit: int) -> dict[str, Any]:
        events_by_id: dict[str, dict[str, Any]] = {}
        for event in self._recent_events(500):
            summary = _context_summary(event)
            if summary is not None:
                events_by_id[summary["id"]] = summary
        for log_file in self._debug_log_files():
            for summary in self._file_context_summaries(log_file):
                events_by_id[summary["id"]] = summary
        events = sorted(
            events_by_id.values(),
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )[:limit]
        # Cached file summaries remain private, including nested token maps.
        return {"events": [copy.deepcopy(item) if item["source_log"] else item for item in events]}

    def _file_context_summaries(self, path: Path):
        def stamp():
            try:
                stat = path.stat()
                return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                return None

        before = stamp()
        with self._summary_lock:
            cached = self._summary_cache.get(path)
            if before is not None and cached is not None and cached[0] == before:
                self._summary_cache.move_to_end(path)
                return cached[1]
        complete = False

        def read_complete():
            nonlocal complete
            complete = True

        summaries = tuple(summary for event in self._read_jsonl(path, on_complete=read_complete)
                          if (summary := _context_summary(event, path.name)) is not None)
        # Failed/partial reads keep the normal response semantics, but must be
        # retried next time even when the file's metadata has not changed.
        after = stamp()
        size = None
        if complete and before is not None and before == after:
            try:
                # ASCII escaping also accounts safely for JSON surrogate escapes.
                size = len(json.dumps(summaries, ensure_ascii=True))
            except (ValueError, TypeError, RecursionError, OverflowError):
                pass  # Cache admission must never make a readable result fail.
        with self._summary_lock:
            old = self._summary_cache.pop(path, None)
            if old is not None:
                self._summary_bytes -= old[2]
            if size is not None and size <= 16 * 1024 * 1024:
                self._summary_cache[path] = (after, summaries, size)
                self._summary_bytes += size
            while self._summary_cache and (len(self._summary_cache) > 20 or self._summary_bytes > 16 * 1024 * 1024):
                _, old = self._summary_cache.popitem(last=False)
                self._summary_bytes -= old[2]
        return summaries

    def _debug_log_files(self) -> list[Path]:
        if not self.data_dir.exists():
            return []
        try:
            return sorted(
                self.data_dir.glob("debug_*.jsonl"), reverse=True
            )[:20]
        except OSError:
            return []

    @staticmethod
    def _read_jsonl(
        log_file: Path, *, on_complete: Callable[[], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        try:
            with log_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        raw = json.loads(stripped)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(raw, dict):
                        yield raw
        except (OSError, UnicodeDecodeError):
            return
        if on_complete is not None:
            on_complete()


def _context_summary(
    raw: dict[str, Any], source_log: str = ""
) -> dict[str, Any] | None:
    if raw.get("type") != "llm_call":
        return None
    event_id = str(raw.get("event_id") or "").strip()
    if not event_id:
        return None
    trace = raw.get("context_trace")
    trace = trace if isinstance(trace, dict) else {}
    included = trace.get("included")
    included = included if isinstance(included, list) else []
    try:
        total_tokens = int(trace.get("total_tokens_est") or 0)
    except (TypeError, ValueError):
        return None
    messages = raw.get("messages")
    return {
        "id": event_id,
        "timestamp": raw.get("timestamp") or "",
        "caller": raw.get("caller") or "",
        "phase": raw.get("phase") or "",
        "model": raw.get("model") or "",
        "duration_ms": raw.get("duration_ms"),
        "total_tokens_est": total_tokens,
        "block_count": len(included),
        "message_count": len(messages or []) if isinstance(messages, list) else 0,
        "token_by_type": trace.get("token_by_type") or {},
        "source_log": source_log,
    }


__all__ = ["DebugEventRepository"]
