"""
Debug logging for LLM calls. Logs every request/response to a file.
Activated by `python -m cyrene.runtime.host --verbose`.
"""

import asyncio
import json
import logging
import uuid as _uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from cyrene.config import DATA_DIR, DB_PATH
from cyrene.observability.context_trace import strip_context_metadata, summarize_context_trace

logger = logging.getLogger(__name__)

VERBOSE = False
_log_file: Path | None = None
_write_failure_reported = False
_PERMISSION_EVENT_TYPES = frozenset({
    "auto_review",
    "permission_decision",
    "destructive_confirmation",
    "external_upload_confirmation",
    "self_configuration_confirmation",
    "host_lifecycle_confirmation",
})

# Usage/stats writes used to run synchronously on the main path (several
# SQLite commits per LLM call). They are now batched off the hot path: events
# are queued here and flushed by a background task with one connection and one
# commit. Telemetry is best-effort — a full queue drops the oldest events.
_TELEMETRY_FLUSH_INTERVAL = 2.0
_TELEMETRY_QUEUE_MAX = 2000
_telemetry_pending: deque[dict] = deque()
_telemetry_flush_task: asyncio.Task | None = None


def init_debug_log() -> None:
    """Create a timestamped debug log file."""
    global _log_file
    if not VERBOSE:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _log_file = DATA_DIR / f"debug_{ts}.jsonl"
    _write_entry({"type": "session_start", "timestamp": datetime.now(timezone.utc).isoformat()})
    logger.info("Debug log: %s", _log_file)


def _write_entry(entry: dict) -> None:
    """Append a JSON line to the debug log."""
    global _write_failure_reported
    if _log_file is None:
        return
    try:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Report once: a wedged debug log would otherwise spam one warning per
        # LLM call, hiding the very failures the file exists to expose.
        if not _write_failure_reported:
            _write_failure_reported = True
            logger.warning("Debug log write failed: %s", _log_file, exc_info=True)


def log_llm_call(
    caller: str,
    phase: str,
    messages: list,
    tools: list | None,
    response: dict,
    duration_ms: float,
) -> None:
    """Log one LLM call (request + response) — FULL content, no truncation."""
    if not VERBOSE:
        return

    # Clean messages for JSON serialization (remove non-serializable fields)
    clean_messages = _clean_for_json(strip_context_metadata(messages))

    # Generate event_id so this entry is queryable via get_full_event()
    import uuid as _uuid
    event_id = f"evt_{_uuid.uuid4().hex[:12]}"

    entry = {
        "type": "llm_call",
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "phase": phase,
        "messages": clean_messages,
        "context_trace": _clean_for_json(summarize_context_trace(messages)),
        "tools": tools,
        "response": _clean_for_json(response),
        "duration_ms": round(duration_ms, 1),
    }
    _write_entry(entry)
    # Also store in _full_events for fast lookup
    _full_events[event_id] = dict(entry)


def log_tool_call(caller: str, tool_name: str, args: dict, result: str, duration_ms: float) -> None:
    """Log one tool execution — FULL args and result."""
    if not VERBOSE:
        return
    import uuid as _uuid
    event_id = f"evt_{_uuid.uuid4().hex[:12]}"
    entry = {
        "type": "tool_call",
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "caller": caller,
        "tool": tool_name,
        "args": args,
        "result": str(result),
        "duration_ms": round(duration_ms, 1),
    }
    _write_entry(entry)
    _full_events[event_id] = dict(entry)



def _clean_for_json(obj):
    """Recursively clean an object for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_for_json(i) for i in obj]
    return str(obj)


def get_log_path() -> str:
    """Return the current debug log path, or empty string."""
    return str(_log_file) if _log_file else ""


# ---------------------------------------------------------------------------
# Event bus — 实时事件推送给 Web UI
# ---------------------------------------------------------------------------

_event_subscribers: dict[asyncio.Queue[dict], str] = {}
_event_loop: asyncio.AbstractEventLoop | None = None
_recent_events: deque[dict] = deque(maxlen=500)
_full_events: dict[str, dict] = {}
_MAX_FULL_EVENTS = 1000


def enable_event_bus() -> None:
    """Initialize the event bus.

    Subscribers are registered lazily by :func:`subscribe`. Capturing the host
    loop here also gives synchronous persistence services a single thread-safe
    path into the same event stream.
    """
    global _event_loop
    _event_loop = asyncio.get_running_loop()


def _enqueue_telemetry(event: dict) -> None:
    """Queue one stats event for the background batcher (fire-and-forget)."""
    _telemetry_pending.append(event)
    if len(_telemetry_pending) > _TELEMETRY_QUEUE_MAX:
        _telemetry_pending.popleft()
    global _telemetry_flush_task
    if _telemetry_flush_task is None or _telemetry_flush_task.done():
        stale = True
    else:
        # The task may outlive its event loop if the loop was torn down
        # without cancelling it (pytest-asyncio function-scoped loops,
        # embedded asyncio.run cycles, dev reload); done() stays False then,
        # so also check whether the loop is closed.
        try:
            stale = _telemetry_flush_task.get_loop().is_closed()
        except RuntimeError:
            stale = True  # task no longer bound to a loop, treat as stale
    if stale:
        try:
            _telemetry_flush_task = asyncio.create_task(_telemetry_flush_loop())
        except RuntimeError:
            # No running loop in this context (thread/sync call); the events
            # stay queued and will flush when a loop starts the task.
            logger.warning("No running event loop to start telemetry flush task", exc_info=True)


async def _telemetry_flush_loop() -> None:
    while True:
        await asyncio.sleep(_TELEMETRY_FLUSH_INTERVAL)
        try:
            await _flush_telemetry_batch()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to flush telemetry stats batch")


def _usage_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _accounting_usage(event: dict) -> dict[str, int]:
    """Normalize Provider Plugin usage into the durable analytics schema."""

    raw = event.get("usage")
    raw = raw if isinstance(raw, dict) else {}
    observed = event.get("usage_observation")
    observed = observed if isinstance(observed, dict) else {}
    prompt = _usage_int(
        observed.get("prompt_tokens")
        or raw.get("prompt_tokens")
        or raw.get("input_tokens")
    )
    completion = _usage_int(
        observed.get("completion_tokens")
        or raw.get("completion_tokens")
        or raw.get("output_tokens")
    )
    total = _usage_int(
        observed.get("total_tokens") or raw.get("total_tokens")
    ) or prompt + completion
    details = raw.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cache_hit = _usage_int(
        observed.get("cached_prompt_tokens")
        or raw.get("prompt_cache_hit_tokens")
        or raw.get("cached_input_tokens")
        or raw.get("cache_read_input_tokens")
        or details.get("cached_tokens")
    )
    cache_miss = _usage_int(
        observed.get("cache_miss_tokens")
        or raw.get("prompt_cache_miss_tokens")
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
    }


async def _flush_telemetry_batch() -> None:
    if not _telemetry_pending:
        return
    events = list(_telemetry_pending)
    runtime_events: list[tuple] = []
    model_events: list[tuple] = []
    tool_events: list[tuple] = []
    permission_events: list[dict] = []
    token_events: list[dict] = []
    for event in events:
        timestamp = str(event.get("timestamp") or "")
        event_type = str(event.get("type") or "")
        if event_type == "llm_call":
            status = str(event.get("status") or "completed").strip().lower()
            if status not in {"completed", "success", "succeeded", "ok"}:
                continue
            usage = _accounting_usage(event)
            runtime_events.append((timestamp, usage))
            model = str(event.get("model") or "").strip()
            if model:
                model_events.append((timestamp, model, usage))
            token_events.append({
                "created_at": timestamp,
                "model": model,
                "round_id": str(event.get("round_id") or event.get("run_id") or ""),
                "session_id": str(event.get("session_id") or ""),
                "caller": str(event.get("caller") or "main_agent"),
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "cache_hit_tokens": usage["prompt_cache_hit_tokens"],
                "cache_miss_tokens": usage["prompt_cache_miss_tokens"],
                "duration_ms": int(event.get("duration_ms") or 0),
            })
        elif event_type == "tool_call":
            tool_events.append((timestamp, str(event.get("tool") or "")))
        elif event_type in _PERMISSION_EVENT_TYPES:
            permission_events.append(event)
    from cyrene.runtime import database as cy_db

    await cy_db.record_usage_stats_batch(
        str(DB_PATH),
        runtime_events=runtime_events,
        model_events=model_events,
        tool_events=tool_events,
        permission_events=permission_events,
        token_events=token_events,
    )
    # Only drop the batch once the DB write succeeded; on failure the queue
    # keeps the events and the next flush cycle retries (the queue cap bounds
    # growth under persistent failure). Remove the exact objects committed by
    # this snapshot: events appended while the async DB write was in flight
    # must remain queued for the next batch, even if the queue cap evicted part
    # of the original prefix in the meantime.
    committed_ids = {id(event) for event in events}
    retained = [
        event for event in _telemetry_pending
        if id(event) not in committed_ids
    ]
    _telemetry_pending.clear()
    _telemetry_pending.extend(retained)


def _publish_event_now(event: dict, session_id: str = "") -> None:
    event = dict(event)
    if "timestamp" not in event:
        event = {**event, "timestamp": datetime.now(timezone.utc).isoformat()}

    # Tag session_id for downstream filtering
    if session_id:
        event = {**event, "session_id": session_id}

    # Operationally significant events retain a full, addressable record.
    if event.get("type") in {"llm_call", "tool_call"} | _PERMISSION_EVENT_TYPES:
        event_id = f"evt_{_uuid.uuid4().hex[:12]}"
        event["event_id"] = event_id
        _full_events[event_id] = dict(event)
        # 控制 _full_events 大小
        if len(_full_events) > _MAX_FULL_EVENTS:
            overflow = len(_full_events) - _MAX_FULL_EVENTS
            for key in list(_full_events.keys())[:overflow]:
                _full_events.pop(key, None)
        # Stats persistence is queued off the hot path (see module docstring).
        _enqueue_telemetry(dict(event))

    _recent_events.append(event)
    for queue, subscriber_session_id in tuple(_event_subscribers.items()):
        if (
            subscriber_session_id
            and event.get("session_id") not in (subscriber_session_id, "")
        ):
            continue
        queue.put_nowait(event)


async def publish_event(event: dict, session_id: str = "") -> None:
    """Publish one event from asynchronous runtime code."""
    _publish_event_now(event, session_id)


def publish_event_sync(event: dict, session_id: str = "") -> bool:
    """Publish from a synchronous persistence boundary on the host event loop.

    Worker-thread writes are handed to the loop captured by
    :func:`enable_event_bus`; writes already running on that loop dispatch
    immediately. A process without an enabled event host has no SSE clients, so
    there is deliberately no secondary delivery mechanism.
    """
    global _event_loop
    payload = dict(event)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None and (
        _event_loop is None or _event_loop.is_closed() or running_loop is _event_loop
    ):
        _event_loop = running_loop
        _publish_event_now(payload, session_id)
        return True
    if _event_loop is None or _event_loop.is_closed():
        return False
    _event_loop.call_soon_threadsafe(_publish_event_now, payload, session_id)
    return True


def _search_debug_logs(event_id: str) -> dict | None:
    """Search all debug log files on disk for *event_id*."""
    if not DATA_DIR.exists():
        return None
    log_files = sorted(DATA_DIR.glob("debug_*.jsonl"), reverse=True)
    for log_file in log_files:
        if not log_file.exists():
            continue
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if entry.get("event_id") == event_id:
                        return entry
        except Exception:
            continue
    return None


def get_full_event(event_id: str) -> dict | None:
    """Return the full event data for *event_id*.

    Checks the in-memory _full_events dict first, then falls back to
    all debug JSONL log files on disk for persistence across daemon restarts.
    """
    # 1) Check in-memory dict
    event = _full_events.get(event_id)
    if event is not None:
        return event

    # 2) Fall back to debug log files on disk
    return _search_debug_logs(event_id)


async def subscribe(session_id: str = ""):
    """Async generator — 供 SSE 端点消费事件流。

    自动初始化事件总线。每 15 秒发一次心跳保活。

    Each subscriber owns its queue, so every connected client receives every
    matching event. Session filtering happens during publication, before an
    event enters the queue, and therefore cannot consume another subscriber's
    event.
    """
    global _event_loop
    running_loop = asyncio.get_running_loop()
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = running_loop
    queue: asyncio.Queue[dict] = asyncio.Queue()
    _event_subscribers[queue] = session_id
    try:
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield {
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
    finally:
        _event_subscribers.pop(queue, None)


def get_recent_events(limit: int = 200) -> list[dict]:
    """Return a copy of the most recent runtime events for live UI overlays."""
    if limit <= 0:
        return []
    return list(_recent_events)[-limit:]
