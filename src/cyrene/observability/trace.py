"""Run-scoped tracing primitives for agent performance diagnostics.

The trace context is carried by ``ContextVar`` so child asyncio tasks inherit
the active run/span without plumbing identifiers through every tool API. Span
payloads are deliberately metadata-only: prompts, queries, URLs, tool output,
and credentials must never be attached here.
"""

from __future__ import annotations

import inspect
import logging
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

TraceSink = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str = ""
    run_id: str = ""
    parent_span_id: str = ""
    db_path: str = ""
    session_id: str = ""
    round_id: str = ""
    buffer: list[dict[str, Any]] | None = None


_trace_context: ContextVar[TraceContext] = ContextVar(
    "cyrene_trace_context", default=TraceContext()
)
_trace_sink: ContextVar[TraceSink | None] = ContextVar(
    "cyrene_trace_sink", default=None
)


def current_trace_context() -> TraceContext:
    return _trace_context.get()


def new_trace_id(prefix: str = "trace") -> str:
    return f"{prefix}_{uuid4().hex}"


class TraceBinding:
    def __init__(self, token: Token[TraceContext]) -> None:
        self._token: Token[TraceContext] | None = token

    def reset(self) -> None:
        if self._token is not None:
            _trace_context.reset(self._token)
            self._token = None

    def __enter__(self) -> "TraceBinding":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.reset()


def bind_trace_context(
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    parent_span_id: str | None = None,
    db_path: str | None = None,
    session_id: str | None = None,
    round_id: str | None = None,
) -> TraceBinding:
    current = current_trace_context()
    updated = TraceContext(
        trace_id=current.trace_id if trace_id is None else str(trace_id),
        run_id=current.run_id if run_id is None else str(run_id),
        parent_span_id=(
            current.parent_span_id
            if parent_span_id is None
            else str(parent_span_id)
        ),
        db_path=current.db_path if db_path is None else str(db_path),
        session_id=current.session_id if session_id is None else str(session_id),
        round_id=current.round_id if round_id is None else str(round_id),
        buffer=current.buffer,
    )
    return TraceBinding(_trace_context.set(updated))


class TraceSinkBinding:
    def __init__(self, token: Token[TraceSink | None]) -> None:
        self._token: Token[TraceSink | None] | None = token

    def reset(self) -> None:
        if self._token is not None:
            _trace_sink.reset(self._token)
            self._token = None

    def __enter__(self) -> "TraceSinkBinding":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.reset()


def bind_trace_sink(sink: TraceSink | None) -> TraceSinkBinding:
    """Install an in-process sink, primarily for deterministic benchmarks."""
    return TraceSinkBinding(_trace_sink.set(sink))


async def _emit_to_sink(event: dict[str, Any]) -> None:
    sink = _trace_sink.get()
    if sink is not None:
        try:
            result = sink(dict(event))
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("Trace sink failed", exc_info=True)


@dataclass(slots=True)
class TraceSpan:
    kind: str
    name: str
    span_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    run_id: str = ""
    db_path: str = ""
    _context_token: Token[TraceContext] | None = field(default=None, init=False)
    _started_at: str = field(default="", init=False)
    _started_monotonic: float = field(default=0.0, init=False)
    _parent_span_id: str = field(default="", init=False)
    _status: str = field(default="ok", init=False)
    _session_id: str = field(default="", init=False)
    _round_id: str = field(default="", init=False)
    _buffer: list[dict[str, Any]] | None = field(default=None, init=False)
    _owns_buffer: bool = field(default=False, init=False)

    @property
    def is_active(self) -> bool:
        return self._context_token is not None

    def start(self) -> "TraceSpan":
        if self._context_token is not None:
            return self
        current = current_trace_context()
        self.trace_id = self.trace_id or current.trace_id or new_trace_id()
        self.run_id = self.run_id or current.run_id or self.trace_id
        self.db_path = self.db_path or current.db_path
        self.span_id = self.span_id or new_trace_id("span")
        self._parent_span_id = current.parent_span_id
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._started_monotonic = time.perf_counter()
        self._session_id = current.session_id
        self._round_id = current.round_id
        self._buffer = current.buffer
        if self._buffer is None:
            self._buffer = []
            self._owns_buffer = True
        self._context_token = _trace_context.set(
            TraceContext(
                trace_id=self.trace_id,
                run_id=self.run_id,
                parent_span_id=self.span_id,
                db_path=self.db_path,
                session_id=self._session_id,
                round_id=self._round_id,
                buffer=self._buffer,
            )
        )
        return self

    def set_status(self, status: str) -> None:
        self._status = str(status or "unknown")

    def set_attribute(self, name: str, value: Any) -> None:
        self.attributes[str(name)] = value

    async def finish(self, *, status: str | None = None) -> dict[str, Any]:
        if self._context_token is None:
            return {}
        ended_at = datetime.now(timezone.utc).isoformat()
        duration_ms = max(0.0, (time.perf_counter() - self._started_monotonic) * 1000)
        _trace_context.reset(self._context_token)
        self._context_token = None
        event = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self._parent_span_id,
            "run_id": self.run_id,
            "session_id": self._session_id,
            "round_id": self._round_id,
            "kind": self.kind,
            "name": self.name,
            "status": str(status or self._status),
            "started_at": self._started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "attributes": dict(self.attributes),
        }
        await _emit_to_sink(event)
        if self.db_path and self._buffer is not None:
            self._buffer.append(event)
            if self._owns_buffer:
                try:
                    from cyrene.runtime.database import record_runtime_trace_spans

                    await record_runtime_trace_spans(self.db_path, self._buffer)
                except Exception:
                    # Observability must never change the outcome of an agent run.
                    logger.warning("Trace persistence failed", exc_info=True)
                finally:
                    self._buffer.clear()
        return event

    async def __aenter__(self) -> "TraceSpan":
        return self.start()

    async def __aexit__(self, exc_type, _exc, _traceback) -> None:
        await self.finish(status="error" if exc_type else self._status)


def trace_span(
    kind: str,
    name: str,
    *,
    span_id: str = "",
    attributes: dict[str, Any] | None = None,
    trace_id: str = "",
    run_id: str = "",
    db_path: str = "",
) -> TraceSpan:
    return TraceSpan(
        kind=str(kind),
        name=str(name),
        span_id=str(span_id),
        attributes=dict(attributes or {}),
        trace_id=str(trace_id),
        run_id=str(run_id),
        db_path=str(db_path),
    )


__all__ = [
    "TraceContext",
    "TraceSpan",
    "bind_trace_context",
    "bind_trace_sink",
    "current_trace_context",
    "new_trace_id",
    "trace_span",
]
