"""Daily usage analytics persistence and compatibility backfills."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import aiosqlite

from cyrene.runtime.persistence.telemetry import TelemetryRepository

_TOPIC_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-z][a-z0-9_-]{2,}")
_TOPIC_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "about",
    "there", "would", "could", "should", "into", "your", "their", "them",
    "they", "what", "when", "where", "which", "while", "were", "been",
    "user", "assistant", "reply", "response", "just", "like", "than",
    "then", "also", "some", "more", "very", "much", "really",
    "一个", "这个", "那个", "我们", "你们", "他们", "以及", "因为", "所以", "就是",
}



def _local_tzinfo():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _normalize_day(day: str | None = None, timestamp: str | None = None) -> str:
    if day:
        return str(day).strip()[:10]
    if timestamp:
        raw = str(timestamp).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_local_tzinfo()).strftime("%Y-%m-%d")
        except Exception:
            return raw[:10]
    return datetime.now(_local_tzinfo()).strftime("%Y-%m-%d")


def activity_column(hour: int) -> str:
    """Return the persisted activity bucket for a local clock hour."""
    if hour < 4:
        return "activity_00_04"
    if hour < 8:
        return "activity_04_08"
    if hour < 12:
        return "activity_08_12"
    if hour < 16:
        return "activity_12_16"
    if hour < 20:
        return "activity_16_20"
    return "activity_20_24"


def bump_activity_sync(db_path: str, timestamp: str | None = None) -> None:
    """Increment the correct daily activity bucket for the given timestamp.

    Synchronous counterpart used by Workbench's per-session archiving, which
    runs synchronously so callers are not forced to be async.
    """
    ts = str(timestamp or datetime.now(_local_tzinfo()).isoformat())
    day = _normalize_day(timestamp=ts)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = int(dt.astimezone(_local_tzinfo()).strftime("%H"))
    except Exception:
        hour = 0
    activity_col = activity_column(hour)
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        db.execute(
            f"UPDATE daily_stats SET {activity_col} = {activity_col} + 1 WHERE day = ?",
            (day,),
        )
        db.commit()


def _extract_topic_terms(text: str, limit: int = 12) -> list[str]:
    source = str(text or "").lower()
    if not source:
        return []
    results: list[str] = []
    seen: set[str] = set()
    for token in _TOPIC_RE.findall(source):
        if token in _TOPIC_STOPWORDS:
            continue
        if token.isascii() and len(token) < 4:
            continue
        if token in seen:
            continue
        seen.add(token)
        results.append(token)
        if len(results) >= limit:
            break
    return results


def extract_topic_terms(text: str, limit: int = 12) -> list[str]:
    """Public deterministic topic extraction for repository consumers."""
    return _extract_topic_terms(text, limit)


def _ensure_day_row_sync(db: sqlite3.Connection, day: str) -> None:
    db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))


def record_memory_touch_sync(db_path: str, *, day: str | None = None, emotional_valence: float = 0, is_new: bool = False) -> None:
    target_day = _normalize_day(day=day)
    with sqlite3.connect(db_path) as db:
        _ensure_day_row_sync(db, target_day)
        db.execute(
            """
            UPDATE daily_stats
            SET memory_mentions = memory_mentions + 1,
                memory_new = memory_new + ?,
                emotion_sum = emotion_sum + ?,
                emotion_count = emotion_count + 1
            WHERE day = ?
            """,
            (1 if is_new else 0, float(emotional_valence or 0), target_day),
        )
        db.commit()


_EMPTY_DAY_STATS: dict[str, int] = {
    "llm_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
    "total_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0,
    "tool_calls": 0,
}

_PERMISSION_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS permission_decisions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    round_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    permission_kind TEXT NOT NULL DEFAULT '',
    path_hint TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT ''
)
"""

_permission_ddl_ensured: set[str] = set()
_permission_ddl_lock = threading.Lock()


@dataclass(slots=True)
class UsageStatsBatch:
    daily: dict[str, dict[str, int]] = field(default_factory=dict)
    models: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    tools: dict[tuple[str, str], int] = field(default_factory=dict)


def _aggregate_usage_events(
    runtime_events: Sequence[tuple],
    model_events: Sequence[tuple],
    tool_events: Sequence[tuple],
) -> UsageStatsBatch:
    batch = UsageStatsBatch()
    for timestamp, raw_usage in runtime_events:
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        stats = batch.daily.setdefault(
            _normalize_day(timestamp=timestamp), dict(_EMPTY_DAY_STATS)
        )
        stats["llm_requests"] += 1
        stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        stats["total_tokens"] += int(usage.get("total_tokens") or 0)
        stats["cache_hit_tokens"] += int(usage.get("prompt_cache_hit_tokens") or 0)
        stats["cache_miss_tokens"] += int(usage.get("prompt_cache_miss_tokens") or 0)
    for timestamp, model, raw_usage in model_events:
        if not model:
            continue
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        key = (_normalize_day(timestamp=timestamp), str(model).strip())
        counts = batch.models.setdefault(key, [0, 0, 0])
        counts[0] += 1
        counts[1] += int(usage.get("prompt_tokens") or 0)
        counts[2] += int(usage.get("completion_tokens") or 0)
    for timestamp, tool in tool_events:
        day = _normalize_day(timestamp=timestamp)
        batch.daily.setdefault(day, dict(_EMPTY_DAY_STATS))["tool_calls"] += 1
        tool_name = str(tool or "").strip()
        if tool_name:
            key = (day, tool_name)
            batch.tools[key] = batch.tools.get(key, 0) + 1
    return batch


def _permission_rows(events: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for event in events:
        event_type = str(event.get("type") or "permission_decision")
        approved = event.get("approved") is True or str(
            event.get("decision") or ""
        ).strip().lower() == "approved"
        rows.append((
            str(event.get("event_id") or uuid.uuid4().hex),
            str(event.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            str(event.get("session_id") or ""), str(event.get("round_id") or ""),
            event_type,
            str(event.get("source") or ("auto_reviewer" if event_type == "auto_review" else "user")),
            str(event.get("tool_name") or event.get("tool") or ""),
            str(event.get("operation") or ""), str(event.get("permission_kind") or ""),
            str(event.get("path_hint") or ""), 1 if approved else 0,
            str(event.get("rationale") or ""), str(event.get("fingerprint") or ""),
        ))
    return rows


async def record_usage_stats_batch(
    db_path: str,
    *,
    runtime_events: list[tuple] | tuple = (),
    model_events: list[tuple] | tuple = (),
    tool_events: list[tuple] | tuple = (),
    permission_events: list[dict] | tuple = (),
) -> None:
    """Persist per-day usage counters and permission decisions with one commit.

    Events are aggregated in memory per day / (day, model) / (day, tool) and
    written with a single connection and transaction.
    """
    if not (runtime_events or model_events or tool_events or permission_events):
        return
    batch = _aggregate_usage_events(runtime_events, model_events, tool_events)

    async with aiosqlite.connect(db_path) as db:
        for day, stats in batch.daily.items():
            await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
            await db.execute(
                """
                UPDATE daily_stats
                SET llm_requests = llm_requests + ?,
                    prompt_tokens = prompt_tokens + ?,
                    completion_tokens = completion_tokens + ?,
                    total_tokens = total_tokens + ?,
                    cache_hit_tokens = cache_hit_tokens + ?,
                    cache_miss_tokens = cache_miss_tokens + ?,
                    tool_calls = tool_calls + ?
                WHERE day = ?
                """,
                (
                    stats["llm_requests"], stats["prompt_tokens"],
                    stats["completion_tokens"], stats["total_tokens"],
                    stats["cache_hit_tokens"], stats["cache_miss_tokens"],
                    stats["tool_calls"], day,
                ),
            )
        for (day, model), counts in batch.models.items():
            await db.execute(
                "INSERT OR IGNORE INTO daily_model_stats (day, model) VALUES (?, ?)",
                (day, model),
            )
            await db.execute(
                """
                UPDATE daily_model_stats
                SET requests = requests + ?, prompt_tokens = prompt_tokens + ?,
                    completion_tokens = completion_tokens + ?
                WHERE day = ? AND model = ?
                """,
                (counts[0], counts[1], counts[2], day, model),
            )
        for (day, tool), count in batch.tools.items():
            await db.execute(
                """
                INSERT INTO daily_tool_stats (day, tool, count) VALUES (?, ?, ?)
                ON CONFLICT(day, tool) DO UPDATE SET count = count + ?
                """,
                (day, tool, count, count),
            )
        if permission_events:
            # The schema init creates the table for the main DB; this guard
            # covers temp/other DB paths once per process instead of re-parsing
            # the DDL on every flush.
            with _permission_ddl_lock:
                if db_path not in _permission_ddl_ensured:
                    await db.execute(_PERMISSION_DECISIONS_DDL)
                    _permission_ddl_ensured.add(db_path)
            await db.executemany(
                """
                INSERT OR REPLACE INTO permission_decisions (
                    id, created_at, session_id, round_id, event_type, source,
                    tool_name, operation, permission_kind, path_hint, approved,
                    rationale, fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _permission_rows(permission_events),
            )
        await db.commit()


async def get_model_stats_range(db_path: str, day_from: str, day_to: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT day, model, requests, prompt_tokens, completion_tokens FROM daily_model_stats WHERE day >= ? AND day <= ? ORDER BY day ASC, model ASC",
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def record_tool_call(db_path: str, timestamp: str, tool_name: str = "") -> None:
    day = _normalize_day(timestamp=timestamp)
    tool = str(tool_name or "").strip()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        await db.execute(
            "UPDATE daily_stats SET tool_calls = tool_calls + 1 WHERE day = ?",
            (day,),
        )
        if tool:
            await db.execute(
                """
                INSERT INTO daily_tool_stats (day, tool, count) VALUES (?, ?, 1)
                ON CONFLICT(day, tool) DO UPDATE SET count = count + 1
                """,
                (day, tool),
            )
        await db.commit()


async def record_permission_decision(db_path: str, event: dict) -> None:
    """Persist one auditable permission decision with its exact scope."""
    event_type = str(event.get("type") or "permission_decision")
    raw_decision = str(event.get("decision") or "").strip().lower()
    approved = (
        event.get("approved") is True
        or raw_decision == "approved"
    )
    async with aiosqlite.connect(db_path) as db:
        # Keep this writer safe during a rolling upgrade where the process may
        # publish a decision before the next full ``init_db`` pass.
        with _permission_ddl_lock:
            if db_path not in _permission_ddl_ensured:
                await db.execute(_PERMISSION_DECISIONS_DDL)
                _permission_ddl_ensured.add(db_path)
        await db.execute(
            """
            INSERT OR REPLACE INTO permission_decisions (
                id, created_at, session_id, round_id, event_type, source,
                tool_name, operation, permission_kind, path_hint, approved,
                rationale, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.get("event_id") or uuid.uuid4().hex),
                str(event.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                str(event.get("session_id") or ""),
                str(event.get("round_id") or ""),
                event_type,
                str(event.get("source") or ("auto_reviewer" if event_type == "auto_review" else "user")),
                str(event.get("tool_name") or event.get("tool") or ""),
                str(event.get("operation") or ""),
                str(event.get("permission_kind") or ""),
                str(event.get("path_hint") or ""),
                1 if approved else 0,
                str(event.get("rationale") or ""),
                str(event.get("fingerprint") or ""),
            ),
        )
        await db.commit()


def _canonical_tool_for_stats(tool_name: str) -> str:
    """Return the stable feature key used by profile usage stats."""
    raw = str(tool_name or "").strip()
    if not raw:
        return ""

    compact = re.sub(r"\s+", "", raw).lower()
    localized_aliases = {
        "浏览器": "browser",
        "浏览器操作": "browser",
        "用户浏览器操作": "browser",
        "网络搜索": "web_search",
        "联网搜索": "web_search",
        "网页抓取": "web_fetch",
        "获取网页": "web_fetch",
        "终端": "bash",
        "执行命令": "bash",
    }
    if compact in localized_aliases:
        return localized_aliases[compact]

    snake = re.sub(r"[\s.\-]+", "_", raw)
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", snake)
    snake = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", snake)
    snake = re.sub(r"_+", "_", snake).strip("_").lower()
    if not snake:
        return ""
    if snake == "browser" or snake.startswith("browser_"):
        return "browser"

    aliases = {
        "websearch": "web_search",
        "web_search": "web_search",
        "webfetch": "web_fetch",
        "web_fetch": "web_fetch",
        "fetch_url": "web_fetch",
        "bash": "bash",
        "run_shell": "bash",
        "run_command": "bash",
        "start_shell": "bash",
        "send_shell": "bash",
        "read": "read_file",
        "read_file": "read_file",
        "write": "write_file",
        "write_file": "write_file",
        "edit": "edit_file",
        "edit_file": "edit_file",
        "recallmemory": "recall_memory",
        "recall_memory": "recall_memory",
        "recallconversation": "recall_conversation",
        "recall_conversation": "recall_conversation",
        "listknowledgedocuments": "list_knowledge_documents",
        "list_knowledge_documents": "list_knowledge_documents",
        "searchknowledge": "search_knowledge",
        "search_knowledge": "search_knowledge",
    }
    return aliases.get(snake, snake)


async def get_tool_counts_range(db_path: str, day_from: str, day_to: str, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT tool, SUM(count) AS count
            FROM daily_tool_stats
            WHERE day >= ? AND day <= ?
            GROUP BY tool
            """,
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        merged: dict[str, int] = {}
        for row in rows:
            tool = _canonical_tool_for_stats(str(row["tool"] or ""))
            if not tool:
                continue
            merged[tool] = merged.get(tool, 0) + int(row["count"] or 0)
        top = sorted(merged.items(), key=lambda item: (-item[1], item[0]))[: int(limit)]
        return [{"tool": tool, "count": count} for tool, count in top]


async def record_archive_exchange(
    db_path: str,
    *,
    timestamp: str,
    user_message: str,
    assistant_response: str,
) -> None:
    day = _normalize_day(timestamp=timestamp)
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hour = int(dt.astimezone(_local_tzinfo()).strftime("%H"))
    except Exception:
        hour = 0
    activity_col = activity_column(hour)
    topic_terms = _extract_topic_terms(" ".join([user_message or "", assistant_response or ""]))
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
        await db.execute(
            f"""
            UPDATE daily_stats
            SET archive_entries = archive_entries + 1,
                {activity_col} = {activity_col} + 1
            WHERE day = ?
            """,
            (day,),
        )
        for term in topic_terms:
            await db.execute(
                """
                INSERT INTO daily_topic_terms (day, term, count)
                VALUES (?, ?, 1)
                ON CONFLICT(day, term) DO UPDATE SET count = count + 1
                """,
                (day, term),
            )
        await db.commit()


async def get_daily_stats_range(db_path: str, day_from: str, day_to: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM daily_stats WHERE day >= ? AND day <= ? ORDER BY day ASC",
            (day_from, day_to),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_topic_counts_range(db_path: str, day_from: str, day_to: str, limit: int = 18) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT term, SUM(count) AS count
            FROM daily_topic_terms
            WHERE day >= ? AND day <= ?
            GROUP BY term
            ORDER BY count DESC, term ASC
            LIMIT ?
            """,
            (day_from, day_to, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def count_stat_days(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM daily_stats WHERE archive_entries > 0")
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------

def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> float:
    from cyrene.model_runtime.pricing import cost_to_cny, effective_price, estimate_cost

    pricing = effective_price(model)
    cost = estimate_cost(
        pricing,
        prompt_tokens,
        completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )
    # ``token_usage.estimated_cost`` has one canonical unit.  Prices may be
    # configured in CNY or USD, but persisted and aggregated costs are CNY.
    return round(cost_to_cny(cost, str(pricing.get("currency") or "CNY")), 6)


estimate_cost = _estimate_cost


async def record_token_usage(
    db_path: str,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    duration_ms: int = 0,
    round_id: str = "",
    session_id: str = "",
    caller: str = "main",
) -> None:
    await record_llm_telemetry_batch(
        db_path,
        token_events=[{
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
            "duration_ms": duration_ms,
            "round_id": round_id,
            "session_id": session_id,
            "caller": caller,
        }],
    )


async def record_runtime_trace_span(db_path: str, event: dict) -> None:
    """Persist one metadata-only performance span."""
    await record_runtime_trace_spans(db_path, [event])


async def record_runtime_trace_spans(
    db_path: str, events: list[dict] | tuple[dict, ...]
) -> None:
    """Persist one run's metadata-only spans in a single transaction."""
    await TelemetryRepository(db_path).record_trace_spans(events)


async def get_runtime_trace(db_path: str, trace_id: str) -> list[dict]:
    """Return a start-time ordered waterfall for one trace."""
    return await TelemetryRepository(db_path).get_trace(trace_id)


async def record_llm_telemetry_batch(
    db_path: str,
    *,
    token_events: list[dict] | tuple[dict, ...] = (),
    latency_events: list[dict] | tuple[dict, ...] = (),
) -> None:
    """Persist usage and latency events with one connection and one commit."""
    await TelemetryRepository(db_path).record_batch(
        token_events=token_events,
        latency_events=latency_events,
    )


async def record_llm_latency(
    db_path: str,
    **event,
) -> None:
    """Persist one endpoint attempt with optimization-oriented latency spans."""
    await record_llm_telemetry_batch(db_path, latency_events=[event])


async def get_llm_cache_stats_by_phase(
    db_path: str,
    *,
    since: datetime | None = None,
    caller: str = "main_agent",
) -> list[dict]:
    """Aggregate provider-reported prompt-cache usage by execution phase."""
    return await TelemetryRepository(db_path).cache_stats_by_phase(
        since=since,
        caller=caller,
    )


async def get_token_usage_stats(
    db_path: str,
    *,
    days: int = 7,
    model: str = "",
    since: datetime | None = None,
) -> dict:
    """Return aggregated token usage stats.

    Returns::
        {"total": {"requests": N, "prompt_tokens": N, ...},
         "by_model": [{"model": "...", "requests": N, ...}],
         "by_day": [{"day": "...", "requests": N, ...}],
         "total_cost": N}
    """
    return await TelemetryRepository(db_path).token_usage_stats(
        days=days,
        model=model,
        since=since,
    )


async def _backfill_runtime_logs(db_path: str) -> None:
    from cyrene.runtime.paths import DATA_DIR

    if not DATA_DIR.exists():
        return
    async with aiosqlite.connect(db_path) as db:
        for log_path in sorted(DATA_DIR.glob("debug_*.jsonl")):
            try:
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    timestamp = str(entry.get("timestamp") or "").strip()
                    if not timestamp:
                        continue
                    day = _normalize_day(timestamp=timestamp)
                    await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
                    if entry.get("type") == "llm_call":
                        usage = entry.get("usage")
                        if not isinstance(usage, dict):
                            response = entry.get("response")
                            usage = response.get("usage") if isinstance(response, dict) else {}
                        usage = usage if isinstance(usage, dict) else {}
                        await db.execute(
                            """
                            UPDATE daily_stats
                            SET llm_requests = llm_requests + 1,
                                prompt_tokens = prompt_tokens + ?,
                                completion_tokens = completion_tokens + ?,
                                total_tokens = total_tokens + ?,
                                cache_hit_tokens = cache_hit_tokens + ?,
                                cache_miss_tokens = cache_miss_tokens + ?
                            WHERE day = ?
                            """,
                            (
                                int(usage.get("prompt_tokens") or 0),
                                int(usage.get("completion_tokens") or 0),
                                int(usage.get("total_tokens") or 0),
                                int(usage.get("prompt_cache_hit_tokens") or 0),
                                int(usage.get("prompt_cache_miss_tokens") or 0),
                                day,
                            ),
                        )
                    elif entry.get("type") == "tool_call":
                        await db.execute(
                            "UPDATE daily_stats SET tool_calls = tool_calls + 1 WHERE day = ?",
                            (day,),
                        )
            except Exception:
                continue
        await db.execute(
            "INSERT OR REPLACE INTO analytics_backfills (source, completed_at) VALUES (?, ?)",
            ("runtime_logs_v1", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _backfill_conversation_archives(db_path: str) -> None:
    from cyrene.runtime.memory.archive_format import parse_archive_sections
    from cyrene.runtime.paths import WORKSPACE_DIR, cyrene_dir

    conversations_dir = cyrene_dir(WORKSPACE_DIR) / "conversations"
    if not conversations_dir.exists():
        return
    async with aiosqlite.connect(db_path) as db:
        for filepath in sorted(conversations_dir.glob("*.md")):
            date_str = filepath.stem
            try:
                sections = parse_archive_sections(
                    filepath.read_text(encoding="utf-8"),
                    date_str,
                )
            except Exception:
                continue
            for section in sections:
                day = str(section.get("date") or date_str).strip()[:10]
                await db.execute("INSERT OR IGNORE INTO daily_stats (day) VALUES (?)", (day,))
                stamp = str(section.get("timestamp") or "").strip()
                try:
                    hour = int(stamp[:2])
                except Exception:
                    hour = 0
                activity_col = activity_column(hour)
                await db.execute(
                    f"""
                    UPDATE daily_stats
                    SET archive_entries = archive_entries + 1,
                        {activity_col} = {activity_col} + 1
                    WHERE day = ?
                    """,
                    (day,),
                )
                topic_terms = _extract_topic_terms(" ".join([
                    str(section.get("user_body") or ""),
                    str(section.get("assistant_body") or ""),
                ]))
                for term in topic_terms:
                    await db.execute(
                        """
                        INSERT INTO daily_topic_terms (day, term, count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(day, term) DO UPDATE SET count = count + 1
                        """,
                        (day, term),
                    )
        await db.execute(
            "INSERT OR REPLACE INTO analytics_backfills (source, completed_at) VALUES (?, ?)",
            ("conversation_archives_v1", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def backfill_analytics(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT source FROM analytics_backfills")
        rows = await cursor.fetchall()
        completed = {str(row["source"]) for row in rows}
    if "runtime_logs_v1" not in completed:
        await _backfill_runtime_logs(db_path)
    if "conversation_archives_v1" not in completed:
        await _backfill_conversation_archives(db_path)


class AnalyticsRepository:
    """Domain entry point for daily aggregates and compatibility backfills.

    The module-level functions remain for the historical database facade;
    application code can depend on this scoped repository without receiving a
    bag of unrelated scheduler or schema operations.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def record_usage_batch(
        self,
        *,
        runtime_events: Sequence[tuple] = (),
        model_events: Sequence[tuple] = (),
        tool_events: Sequence[tuple] = (),
        permission_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        await record_usage_stats_batch(
            self.db_path,
            runtime_events=runtime_events,
            model_events=model_events,
            tool_events=tool_events,
            permission_events=permission_events,
        )

    async def daily_range(self, day_from: str, day_to: str) -> list[dict[str, Any]]:
        return await get_daily_stats_range(self.db_path, day_from, day_to)

    async def model_range(self, day_from: str, day_to: str) -> list[dict[str, Any]]:
        return await get_model_stats_range(self.db_path, day_from, day_to)

    async def topic_range(
        self,
        day_from: str,
        day_to: str,
        limit: int = 18,
    ) -> list[dict[str, Any]]:
        return await get_topic_counts_range(self.db_path, day_from, day_to, limit)

    async def tool_range(
        self,
        day_from: str,
        day_to: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return await get_tool_counts_range(self.db_path, day_from, day_to, limit)

    async def run_backfills(self) -> None:
        await backfill_analytics(self.db_path)


# --- Task CRUD ---
