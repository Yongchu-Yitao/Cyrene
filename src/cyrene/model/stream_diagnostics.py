"""Outcome-only stream diagnostics; observational failures never mask errors."""
from contextlib import contextmanager

from .stream_encoding import ModelStreamError


def new_stream_diagnostics(adapter: str) -> dict:
    return {
        "adapter": adapter,
        "line_count": 0,
        "data_chunk_count": 0,
        "event_count": 0,
        "invalid_json_line_count": 0,
        "saw_done_marker": False,
        "http_status": 0,
        "terminal_event_seen": False,
        "stream_completed": False,
        "termination_reason": "",
    }


@contextmanager
def enrich_stream_errors(tool_calls, snapshot):
    try:
        yield
    except ModelStreamError as exc:
        # Transport/encoding errors copied diagnostics before reaching this
        # boundary; decoder failures may already contain a complete snapshot.
        if tool_calls and not exc.diagnostics.get("tool_calls"):
            try:
                exc.diagnostics["tool_calls"] = snapshot(tool_calls)
            except Exception:
                pass
        raise
