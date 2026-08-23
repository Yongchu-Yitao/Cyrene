"""Token, latency, and runtime-trace persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping, Sequence
import uuid

import aiosqlite


@dataclass(frozen=True, slots=True)
class TokenUsageEvent:
    model: str
    round_id: str
    session_id: str
    caller: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    duration_ms: int
    created_at: str

    @classmethod
    def from_mapping(cls, event: Mapping[str, Any], now: str) -> TokenUsageEvent:
        return cls(
            model=str(event.get("model") or ""),
            round_id=str(event.get("round_id") or ""),
            session_id=str(event.get("session_id") or ""),
            caller=str(event.get("caller") or "main"),
            prompt_tokens=int(event.get("prompt_tokens") or 0),
            completion_tokens=int(event.get("completion_tokens") or 0),
            total_tokens=int(event.get("total_tokens") or 0),
            cache_hit_tokens=int(event.get("cache_hit_tokens") or 0),
            cache_miss_tokens=int(event.get("cache_miss_tokens") or 0),
            duration_ms=int(event.get("duration_ms") or 0),
            created_at=str(event.get("created_at") or now),
        )

    def as_row(self) -> tuple[Any, ...]:
        from cyrene.model_runtime.pricing import cost_to_cny, effective_price, estimate_cost

        pricing = effective_price(self.model)
        cost = estimate_cost(
            pricing,
            self.prompt_tokens,
            self.completion_tokens,
            cache_hit_tokens=self.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens,
        )
        estimated_cost = round(
            cost_to_cny(cost, str(pricing.get("currency") or "CNY")),
            6,
        )
        return (
            self.created_at,
            self.model,
            self.round_id,
            self.session_id,
            self.caller,
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.cache_hit_tokens,
            self.cache_miss_tokens,
            self.duration_ms,
            estimated_cost,
        )


@dataclass(frozen=True, slots=True)
class RuntimeTraceSpan:
    span_id: str
    trace_id: str
    parent_span_id: str
    run_id: str
    session_id: str
    round_id: str
    kind: str
    name: str
    status: str
    started_at: str
    ended_at: str
    duration_ms: float
    attributes: dict[str, Any]

    @classmethod
    def from_mapping(cls, event: Mapping[str, Any], now: str) -> RuntimeTraceSpan:
        attributes = event.get("attributes")
        return cls(
            span_id=str(event.get("span_id") or f"span_{uuid.uuid4().hex}"),
            trace_id=str(event.get("trace_id") or ""),
            parent_span_id=str(event.get("parent_span_id") or ""),
            run_id=str(event.get("run_id") or ""),
            session_id=str(event.get("session_id") or ""),
            round_id=str(event.get("round_id") or ""),
            kind=str(event.get("kind") or "unknown"),
            name=str(event.get("name") or "unknown"),
            status=str(event.get("status") or "unknown"),
            started_at=str(event.get("started_at") or now),
            ended_at=str(event.get("ended_at") or now),
            duration_ms=float(event.get("duration_ms") or 0),
            attributes=attributes if isinstance(attributes, dict) else {},
        )

    def as_row(self) -> tuple[Any, ...]:
        return (
            self.span_id,
            self.trace_id,
            self.parent_span_id,
            self.run_id,
            self.session_id,
            self.round_id,
            self.kind,
            self.name,
            self.status,
            self.started_at,
            self.ended_at,
            self.duration_ms,
            json.dumps(self.attributes, ensure_ascii=False),
        )


class TelemetryRepository:
    """SQLite repository for best-effort model and runtime telemetry.

    The repository does no task scheduling or event publication.  Callers keep
    ownership of batching and background delivery, so moving these queries does
    not add a database round trip to the real-time event path.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    @staticmethod
    async def _ensure_latency_table(db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_latency_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL,
                created_at TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '',
                round_id TEXT NOT NULL DEFAULT '', caller TEXT NOT NULL DEFAULT '',
                phase TEXT NOT NULL DEFAULT '', model_type TEXT NOT NULL DEFAULT 'primary',
                candidate_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                endpoint TEXT NOT NULL DEFAULT '', candidate_rank INTEGER NOT NULL DEFAULT 0,
                endpoint_rank INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
                outcome TEXT NOT NULL, status_code INTEGER NOT NULL DEFAULT 0,
                error_type TEXT NOT NULL DEFAULT '', queue_wait_ms REAL NOT NULL DEFAULT 0,
                pre_attempt_wait_ms REAL NOT NULL DEFAULT 0,
                request_ms REAL NOT NULL DEFAULT 0, response_headers_ms REAL,
                ttft_ms REAL, first_token_after_headers_ms REAL, generation_ms REAL,
                retry_backoff_ms REAL NOT NULL DEFAULT 0, total_call_ms REAL NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                cache_hit_ratio REAL NOT NULL DEFAULT 0,
                output_tokens_per_second REAL, fallback_used INTEGER NOT NULL DEFAULT 0,
                client_pool_reused INTEGER NOT NULL DEFAULT 0,
                connection_pool_key TEXT NOT NULL DEFAULT '',
                model_lease_id TEXT NOT NULL DEFAULT '',
                request_messages_fingerprint TEXT NOT NULL DEFAULT '',
                request_tools_fingerprint TEXT NOT NULL DEFAULT '',
                request_payload_fingerprint TEXT NOT NULL DEFAULT '',
                previous_payload_fingerprint TEXT NOT NULL DEFAULT '',
                cache_prefix_status TEXT NOT NULL DEFAULT '',
                cache_invalidation_reason TEXT NOT NULL DEFAULT '',
                cache_prefix_message_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(llm_latency_events)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        migrations = {
            "pre_attempt_wait_ms": "REAL NOT NULL DEFAULT 0",
            "response_headers_ms": "REAL",
            "first_token_after_headers_ms": "REAL",
            "client_pool_reused": "INTEGER NOT NULL DEFAULT 0",
            "prompt_cache_hit_tokens": "INTEGER NOT NULL DEFAULT 0",
            "prompt_cache_miss_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_hit_ratio": "REAL NOT NULL DEFAULT 0",
            "model_lease_id": "TEXT NOT NULL DEFAULT ''",
            "request_messages_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "request_tools_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "request_payload_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "previous_payload_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "cache_prefix_status": "TEXT NOT NULL DEFAULT ''",
            "cache_invalidation_reason": "TEXT NOT NULL DEFAULT ''",
            "cache_prefix_message_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in migrations.items():
            if name not in columns:
                await db.execute(
                    f"ALTER TABLE llm_latency_events ADD COLUMN {name} {definition}"
                )

    @staticmethod
    async def _ensure_trace_table(db: aiosqlite.Connection) -> None:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_trace_spans (
                span_id TEXT NOT NULL, trace_id TEXT NOT NULL,
                parent_span_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '', round_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ok',
                started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
                duration_ms REAL NOT NULL DEFAULT 0,
                attributes_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(trace_id, span_id)
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_trace_spans_trace
                ON runtime_trace_spans(trace_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_runtime_trace_spans_run
                ON runtime_trace_spans(run_id, started_at);
            """
        )

    @staticmethod
    def _latency_row(event: Mapping[str, Any], now: str) -> tuple[Any, ...]:
        prompt_tokens = int(event.get("prompt_tokens") or 0)
        cache_hit_tokens = int(event.get("prompt_cache_hit_tokens") or 0)
        cache_miss_tokens = int(event.get("prompt_cache_miss_tokens") or 0)
        cache_denominator = cache_hit_tokens + cache_miss_tokens
        cache_hit_ratio = (
            cache_hit_tokens / cache_denominator if cache_denominator > 0 else 0.0
        )
        return (
            str(event.get("call_id") or ""),
            str(event.get("created_at") or now),
            str(event.get("session_id") or ""),
            str(event.get("round_id") or ""),
            str(event.get("caller") or ""),
            str(event.get("phase") or ""),
            str(event.get("model_type") or "primary"),
            str(event.get("candidate_id") or ""),
            str(event.get("model") or ""),
            str(event.get("endpoint") or ""),
            int(event.get("candidate_rank") or 0),
            int(event.get("endpoint_rank") or 0),
            int(event.get("attempt") or 1),
            str(event.get("outcome") or "unknown"),
            int(event.get("status_code") or 0),
            str(event.get("error_type") or ""),
            float(event.get("queue_wait_ms") or 0),
            float(event.get("pre_attempt_wait_ms") or event.get("queue_wait_ms") or 0),
            float(event.get("request_ms") or 0),
            event.get("response_headers_ms"),
            event.get("ttft_ms"),
            event.get("first_token_after_headers_ms"),
            event.get("generation_ms"),
            float(event.get("retry_backoff_ms") or 0),
            float(event.get("total_call_ms") or 0),
            prompt_tokens,
            int(event.get("completion_tokens") or 0),
            cache_hit_tokens,
            cache_miss_tokens,
            cache_hit_ratio,
            event.get("output_tokens_per_second"),
            1 if event.get("fallback_used") else 0,
            1 if event.get("client_pool_reused") else 0,
            str(event.get("connection_pool_key") or ""),
            str(event.get("model_lease_id") or ""),
            str(event.get("request_messages_fingerprint") or ""),
            str(event.get("request_tools_fingerprint") or ""),
            str(event.get("request_payload_fingerprint") or ""),
            str(event.get("previous_payload_fingerprint") or ""),
            str(event.get("cache_prefix_status") or ""),
            str(event.get("cache_invalidation_reason") or ""),
            int(event.get("cache_prefix_message_count") or 0),
        )

    @staticmethod
    def _trace_from_latency(event: Mapping[str, Any], now: str) -> RuntimeTraceSpan:
        duration_ms = float(event.get("request_ms") or event.get("total_call_ms") or 0)
        ended_at = str(event.get("created_at") or now)
        try:
            ended = datetime.fromisoformat(ended_at)
            started_at = (ended - timedelta(milliseconds=duration_ms)).isoformat()
        except (TypeError, ValueError):
            started_at = ended_at
        return RuntimeTraceSpan.from_mapping(
            {
                "span_id": str(event.get("span_id") or ""),
                "trace_id": str(event.get("trace_id") or ""),
                "parent_span_id": str(event.get("parent_span_id") or ""),
                "run_id": str(event.get("run_id") or ""),
                "session_id": str(event.get("session_id") or ""),
                "round_id": str(event.get("round_id") or ""),
                "kind": "model",
                "name": f"{event.get('caller') or 'unknown'}.{event.get('phase') or 'unknown'}",
                "status": "ok" if event.get("outcome") == "success" else "error",
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "attributes": {
                    "call_id": str(event.get("call_id") or ""),
                    "attempt": int(event.get("attempt") or 1),
                    "model": str(event.get("model") or ""),
                    "outcome": str(event.get("outcome") or "unknown"),
                    "prompt_tokens": int(event.get("prompt_tokens") or 0),
                    "completion_tokens": int(event.get("completion_tokens") or 0),
                    "prompt_cache_hit_tokens": int(event.get("prompt_cache_hit_tokens") or 0),
                    "prompt_cache_miss_tokens": int(event.get("prompt_cache_miss_tokens") or 0),
                    "fallback_used": bool(event.get("fallback_used")),
                    "queue_wait_ms": float(event.get("queue_wait_ms") or 0),
                    "pre_attempt_wait_ms": float(event.get("pre_attempt_wait_ms") or 0),
                    "request_ms": float(event.get("request_ms") or 0),
                    "response_headers_ms": event.get("response_headers_ms"),
                    "ttft_ms": event.get("ttft_ms"),
                    "first_token_after_headers_ms": event.get("first_token_after_headers_ms"),
                    "generation_ms": event.get("generation_ms"),
                    "retry_backoff_ms": float(event.get("retry_backoff_ms") or 0),
                    "model_lease_id": str(event.get("model_lease_id") or ""),
                    "request_messages_fingerprint": str(event.get("request_messages_fingerprint") or ""),
                    "request_tools_fingerprint": str(event.get("request_tools_fingerprint") or ""),
                    "request_payload_fingerprint": str(event.get("request_payload_fingerprint") or ""),
                    "previous_payload_fingerprint": str(event.get("previous_payload_fingerprint") or ""),
                    "cache_prefix_status": str(event.get("cache_prefix_status") or ""),
                    "cache_invalidation_reason": str(event.get("cache_invalidation_reason") or ""),
                    "cache_prefix_message_count": int(event.get("cache_prefix_message_count") or 0),
                },
            },
            now,
        )

    async def record_trace_spans(self, events: Sequence[Mapping[str, Any]]) -> None:
        if not events:
            return
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_trace_table(db)
            await db.executemany(
                """
                INSERT OR REPLACE INTO runtime_trace_spans
                (span_id, trace_id, parent_span_id, run_id, session_id, round_id,
                 kind, name, status, started_at, ended_at, duration_ms, attributes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [RuntimeTraceSpan.from_mapping(event, now).as_row() for event in events],
            )
            await db.commit()

    async def get_trace(self, trace_id: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_trace_table(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM runtime_trace_spans "
                "WHERE trace_id = ? ORDER BY started_at, span_id",
                (str(trace_id),),
            )
            rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["attributes"] = json.loads(item.pop("attributes_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["attributes"] = {}
                item.pop("attributes_json", None)
            result.append(item)
        return result

    async def record_batch(
        self,
        *,
        token_events: Sequence[Mapping[str, Any]] = (),
        latency_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if not token_events and not latency_events:
            return
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            if latency_events:
                await self._ensure_latency_table(db)
            traced_latency_events = [
                event for event in latency_events if str(event.get("trace_id") or "")
            ]
            if traced_latency_events:
                await self._ensure_trace_table(db)
            if token_events:
                await db.executemany(
                    """INSERT INTO token_usage
                       (created_at, model, round_id, session_id, caller,
                        prompt_tokens, completion_tokens, total_tokens,
                        cache_hit_tokens, cache_miss_tokens, duration_ms, estimated_cost)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [TokenUsageEvent.from_mapping(event, now).as_row() for event in token_events],
                )
            if latency_events:
                await db.executemany(
                    """
                    INSERT INTO llm_latency_events
                    (call_id, created_at, session_id, round_id, caller, phase, model_type,
                     candidate_id, model, endpoint, candidate_rank, endpoint_rank, attempt,
                     outcome, status_code, error_type, queue_wait_ms, pre_attempt_wait_ms,
                     request_ms, response_headers_ms, ttft_ms, first_token_after_headers_ms,
                     generation_ms, retry_backoff_ms, total_call_ms, prompt_tokens,
                     completion_tokens, prompt_cache_hit_tokens,
                     prompt_cache_miss_tokens, cache_hit_ratio,
                     output_tokens_per_second, fallback_used,
                     client_pool_reused, connection_pool_key, model_lease_id,
                     request_messages_fingerprint, request_tools_fingerprint,
                     request_payload_fingerprint, previous_payload_fingerprint,
                     cache_prefix_status, cache_invalidation_reason,
                     cache_prefix_message_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._latency_row(event, now) for event in latency_events],
                )
            if traced_latency_events:
                await db.executemany(
                    """
                    INSERT OR REPLACE INTO runtime_trace_spans
                    (span_id, trace_id, parent_span_id, run_id, session_id, round_id,
                     kind, name, status, started_at, ended_at, duration_ms, attributes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._trace_from_latency(event, now).as_row() for event in traced_latency_events],
                )
            await db.commit()

    async def cache_stats_by_phase(
        self,
        *,
        since: datetime | None = None,
        caller: str = "main_agent",
    ) -> list[dict[str, Any]]:
        since_value = since or (datetime.now(timezone.utc) - timedelta(days=7))
        if since_value.tzinfo is None:
            since_value = since_value.replace(tzinfo=timezone.utc)
        async with aiosqlite.connect(self.db_path) as db:
            await self._ensure_latency_table(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT phase, COUNT(*) AS requests,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(prompt_cache_hit_tokens), 0) AS cache_hit_tokens,
                       COALESCE(SUM(prompt_cache_miss_tokens), 0) AS cache_miss_tokens
                FROM llm_latency_events
                WHERE created_at >= ? AND caller = ? AND outcome = 'success'
                GROUP BY phase ORDER BY requests DESC, phase
                """,
                (since_value.isoformat(), str(caller)),
            )
            rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            denominator = int(item["cache_hit_tokens"]) + int(item["cache_miss_tokens"])
            item["cache_hit_ratio"] = (
                int(item["cache_hit_tokens"]) / denominator if denominator > 0 else 0.0
            )
            result.append(item)
        return result

    async def token_usage_stats(
        self,
        *,
        days: int = 7,
        model: str = "",
        since: datetime | None = None,
    ) -> dict[str, Any]:
        since_value = since or (datetime.now(timezone.utc) - timedelta(days=days))
        if since_value.tzinfo is None:
            since_value = since_value.replace(tzinfo=timezone.utc)
        since_iso = since_value.isoformat()
        model_filter = " AND model = ?" if model else ""
        model_params = (since_iso, model) if model else (since_iso,)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(total_tokens), 0) AS total_tokens,
                          COALESCE(MAX(total_tokens), 0) AS max_total_tokens,
                          COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                          COALESCE(SUM(estimated_cost), 0) AS total_cost,
                          COALESCE(MAX(estimated_cost), 0) AS max_cost
                   FROM token_usage WHERE created_at >= ?""",
                (since_iso,),
            )
            total_row = await cursor.fetchone()
            cursor = await db.execute(
                f"""SELECT model, COUNT(*) AS requests,
                           COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens,
                           COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                           COALESCE(SUM(estimated_cost), 0) AS cost
                    FROM token_usage WHERE created_at >= ?{model_filter}
                    GROUP BY model ORDER BY cost DESC""",
                model_params,
            )
            by_model = [dict(row) for row in await cursor.fetchall()]
            cursor = await db.execute(
                f"""SELECT DATE(created_at) AS day, COUNT(*) AS requests,
                           COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens,
                           COALESCE(SUM(estimated_cost), 0) AS cost
                    FROM token_usage WHERE created_at >= ?{model_filter}
                    GROUP BY day ORDER BY day ASC""",
                model_params,
            )
            by_day = [dict(row) for row in await cursor.fetchall()]
        total = dict(total_row) if total_row else {}
        return {
            "total": {
                "requests": total.get("requests", 0),
                "prompt_tokens": total.get("prompt_tokens", 0),
                "completion_tokens": total.get("completion_tokens", 0),
                "total_tokens": total.get("total_tokens", 0),
                "max_total_tokens": total.get("max_total_tokens", 0),
                "cache_hit_tokens": total.get("cache_hit_tokens", 0),
                "total_cost": round(float(total.get("total_cost", 0)), 6),
                "max_cost": round(float(total.get("max_cost", 0)), 6),
            },
            "by_model": by_model,
            "by_day": by_day,
        }
