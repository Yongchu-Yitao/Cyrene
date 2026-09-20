"""Repository for live and persisted debug events."""

from __future__ import annotations

import json
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
            for event in self._read_jsonl(log_file):
                summary = _context_summary(event, log_file.name)
                if summary is not None:
                    events_by_id[summary["id"]] = summary
        events = sorted(
            events_by_id.values(),
            key=lambda item: str(item.get("timestamp") or ""),
            reverse=True,
        )[:limit]
        return {"events": events}

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
    def _read_jsonl(log_file: Path) -> Iterator[dict[str, Any]]:
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
