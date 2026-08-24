"""Deterministic benchmarks for Cyrene's local feature runtimes.

The scenarios exercise real in-process event delivery, terminal buffering,
knowledge persistence/search, scheduled-task persistence and file hashing.
They use temporary data and perform no model or network calls.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import sqlite3
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from cyrene.observability.benchmark_cache import (
    IdealPrefixCacheTracker,
    aggregate_ideal_cache_metrics,
    ideal_cache_percent,
    ideal_cache_progression,
)


@dataclass(frozen=True, slots=True)
class FeatureBenchmarkConfig:
    parallel_workers: int = 3
    rounds_per_worker: int = 2
    event_subscribers: int = 64
    event_count: int = 100
    terminal_chunks: int = 512
    terminal_chunk_bytes: int = 4096
    knowledge_documents: int = 24
    knowledge_chunks_per_document: int = 64
    knowledge_searches: int = 32
    scheduled_tasks: int = 48
    hash_file_bytes: int = 8 * 1024 * 1024
    hash_cache_reads: int = 200


DEFAULT_CONFIG = FeatureBenchmarkConfig()


def _utc_now_iso() -> str:
    """Return a benchmark fixture timestamp without depending on terminal internals."""
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _result(
    feature: str,
    scenario: str,
    wall_ms: float,
    operations: int,
    *,
    latencies_ms: list[float] | None = None,
    quality: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latencies = latencies_ms or []
    preserved = bool((quality or {}).get("preserved", True))
    return {
        "feature": feature,
        "scenario": scenario,
        "wall_ms": round(wall_ms, 3),
        "operations": int(operations),
        "operations_per_second": round(
            operations / max(wall_ms / 1000, 0.000001), 1
        ),
        "latency_p50_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
        "latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "quality": {"preserved": preserved, **(quality or {})},
        "details": details or {},
    }


async def _initialize_knowledge_fixture(db_path: Path) -> None:
    """Initialize a fixture DB without leaving maintenance outside the sample."""
    from cyrene.runtime.persistence import knowledge as knowledge_persistence

    await knowledge_persistence.init_knowledge_db(str(db_path))
    key = str(db_path.expanduser().resolve())
    task = knowledge_persistence._KNOWLEDGE_FTS_MAINTENANCE_TASKS.pop(key, None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _benchmark_event_bus(config: FeatureBenchmarkConfig, root: Path) -> dict[str, Any]:
    from cyrene.observability import debug

    subscriber_count = max(1, config.event_subscribers)
    event_count = max(1, config.event_count)
    scope = f"benchmark_{root.parent.name}_{root.name}"
    generators = [debug.subscribe(session_id=scope) for _ in range(subscriber_count)]
    delivery_latencies: list[float] = []

    async def consume(generator: Any) -> int:
        received = 0
        for _ in range(event_count):
            event = await anext(generator)
            delivery_latencies.append(
                max(0.0, (time.perf_counter() - float(event["benchmarkSentAt"])) * 1000)
            )
            received += 1
        return received

    consumers = [asyncio.create_task(consume(generator)) for generator in generators]
    await asyncio.sleep(0)
    started = time.perf_counter()
    for index in range(event_count):
        await debug.publish_event(
            {
                "type": "benchmark_event",
                "index": index,
                "benchmarkSentAt": time.perf_counter(),
            },
            session_id=scope,
        )
        await asyncio.sleep(0)
    received = await asyncio.gather(*consumers)
    wall_ms = (time.perf_counter() - started) * 1000
    for generator in generators:
        await generator.aclose()

    expected = subscriber_count * event_count
    actual = sum(received)
    return _result(
        "realtime_events",
        "event_bus_fanout",
        wall_ms,
        expected,
        latencies_ms=delivery_latencies,
        quality={
            "preserved": actual == expected,
            "expected_deliveries": expected,
            "actual_deliveries": actual,
        },
        details={"subscribers": subscriber_count, "events": event_count},
    )


async def _benchmark_terminal(
    config: FeatureBenchmarkConfig,
    root: Path,
    *,
    now_iso: Callable[[], str] = _utc_now_iso,
) -> dict[str, Any]:
    from cyrene.terminal.manager import TerminalManager, TerminalSession

    manager = TerminalManager(
        output_limit=max(16 * 1024 * 1024, config.terminal_chunks * config.terminal_chunk_bytes),
        state_dir=root / "terminal-state",
    )
    now = now_iso()
    session = TerminalSession(
        id="terminal_benchmark",
        project_id="benchmark",
        title="Benchmark",
        cwd=str(root),
        shell="sh",
        argv=["/bin/sh"],
        created_at=now,
        updated_at=now,
        status="running",
    )
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    live_queue = manager.subscribe(session.id)
    chunk = (b"cyrene terminal benchmark output 0123456789\n" * 128)[
        : max(1, config.terminal_chunk_bytes)
    ]
    if len(chunk) < config.terminal_chunk_bytes:
        chunk = chunk.ljust(config.terminal_chunk_bytes, b"x")
    chunk_count = max(1, config.terminal_chunks)
    expected_bytes = len(chunk) * chunk_count

    started = time.perf_counter()
    manager._append_output(session, chunk)
    first_live_ms = (time.perf_counter() - started) * 1000
    first_event = live_queue.get_nowait()
    manager.unsubscribe(session.id, live_queue)
    for _ in range(chunk_count - 1):
        manager._append_output(session, chunk)
    append_ms = (time.perf_counter() - started) * 1000

    flush_started = time.perf_counter()
    manager.flush()
    flush_ms = (time.perf_counter() - flush_started) * 1000
    screen_started = time.perf_counter()
    await manager.screen_snapshot_async(session.id)
    screen_ms = (time.perf_counter() - screen_started) * 1000
    replay_started = time.perf_counter()
    replay = manager.replay(session.id, 0)
    replay_bytes = sum(len(base64.b64decode(event["data"])) for event in replay)
    replay_ms = (time.perf_counter() - replay_started) * 1000
    await asyncio.sleep(0)

    durable_directory = manager._scroll_segment_dir(session.id)
    durable_bytes = sum(
        path.stat().st_size for path in durable_directory.glob("*.bin")
    )
    manager.close_store()
    wall_ms = append_ms + flush_ms + screen_ms + replay_ms
    return _result(
        "terminal",
        "output_stream_persist_screen_replay",
        wall_ms,
        chunk_count,
        latencies_ms=[first_live_ms],
        quality={
            "preserved": (
                first_event.get("type") == "output"
                and session.next_seq == expected_bytes
                and durable_bytes == expected_bytes
                and replay_bytes == expected_bytes
            ),
            "expected_bytes": expected_bytes,
            "durable_bytes": durable_bytes,
            "replay_bytes": replay_bytes,
        },
        details={
            "first_live_ms": round(first_live_ms, 3),
            "append_ms": round(append_ms, 3),
            "flush_ms": round(flush_ms, 3),
            "screen_parse_ms": round(screen_ms, 3),
            "replay_ms": round(replay_ms, 3),
        },
    )


async def _seed_knowledge(
    db_path: Path,
    *,
    documents: int,
    chunks_per_document: int,
) -> None:
    await _initialize_knowledge_fixture(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        document_rows = []
        chunk_rows = []
        fts_rows = []
        for document_index in range(documents):
            document_id = f"doc_{document_index}"
            document_rows.append((
                document_id,
                f"Benchmark document {document_index}",
                f"/benchmark/document-{document_index}.txt",
                "text/plain",
                "file",
                "ready",
                "benchmark",
                now,
                now,
            ))
            for chunk_index in range(chunks_per_document):
                chunk_id = f"chunk_{document_index}_{chunk_index}"
                content = (
                    f"Cyrene benchmark knowledge content document {document_index} "
                    f"chunk {chunk_index}. Deterministic retrieval fixture."
                )
                chunk_rows.append((
                    chunk_id,
                    document_id,
                    chunk_index,
                    content,
                    chunk_index * 100,
                    chunk_index * 100 + len(content),
                    24,
                    now,
                ))
                fts_rows.append((content, chunk_id, document_id))
        conn.executemany(
            """INSERT INTO kb_documents(
                   id, name, path, content_type, kind, status, source,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            document_rows,
        )
        conn.executemany(
            """INSERT INTO kb_chunks(
                   id, document_id, ordinal, content, char_start, char_end,
                   token_count, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            chunk_rows,
        )
        conn.executemany(
            "INSERT INTO kb_chunks_fts(content, chunk_id, document_id) VALUES (?, ?, ?)",
            fts_rows,
        )
        conn.commit()


async def _benchmark_knowledge_search(
    config: FeatureBenchmarkConfig, root: Path
) -> dict[str, Any]:
    from cyrene.knowledge.retrieve import search_knowledge

    db_path = root / "knowledge-search.db"
    documents = max(1, config.knowledge_documents)
    chunks = max(1, config.knowledge_chunks_per_document)
    searches = max(1, config.knowledge_searches)
    await _seed_knowledge(db_path, documents=documents, chunks_per_document=chunks)

    async def search_once(index: int) -> tuple[float, list[dict[str, Any]]]:
        started = time.perf_counter()
        result = await search_knowledge(
            str(db_path),
            "cyrene benchmark",
            k=8,
            document_id=(f"doc_{index % documents}" if index % 2 else None),
        )
        return (time.perf_counter() - started) * 1000, result

    started = time.perf_counter()
    outcomes = await asyncio.gather(*(search_once(index) for index in range(searches)))
    wall_ms = (time.perf_counter() - started) * 1000
    latencies = [item[0] for item in outcomes]
    counts = [len(item[1]) for item in outcomes]
    return _result(
        "knowledge",
        "fts_search_concurrency",
        wall_ms,
        searches,
        latencies_ms=latencies,
        quality={
            "preserved": all(count == 8 for count in counts),
            "expected_results_per_search": 8,
            "minimum_results": min(counts, default=0),
        },
        details={
            "documents": documents,
            "chunks": documents * chunks,
            "concurrency": searches,
        },
    )


async def _benchmark_knowledge_write(
    config: FeatureBenchmarkConfig, root: Path
) -> dict[str, Any]:
    from cyrene.knowledge import store
    db_path = root / "knowledge-write.db"
    await _initialize_knowledge_fixture(db_path)
    documents = max(1, min(config.knowledge_documents, 12))
    chunks_per_document = max(1, config.knowledge_chunks_per_document)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """INSERT INTO kb_documents(
                   id, name, path, status, source, created_at, updated_at
               ) VALUES (?, ?, ?, 'pending', 'benchmark', ?, ?)""",
            [
                (f"write_doc_{index}", f"Write {index}", f"/write/{index}", now, now)
                for index in range(documents)
            ],
        )
        conn.commit()

    async def replace(index: int) -> tuple[float, bool | Exception]:
        fixture = [
            {
                "id": f"write_chunk_{index}_{chunk_index}",
                "ordinal": chunk_index,
                "content": f"Cyrene write benchmark {index} {chunk_index}",
                "char_start": chunk_index * 40,
                "char_end": chunk_index * 40 + 36,
                "token_count": 9,
            }
            for chunk_index in range(chunks_per_document)
        ]
        started = time.perf_counter()
        try:
            result: bool | Exception = await store.replace_chunks(
                str(db_path), f"write_doc_{index}", fixture
            )
        except Exception as exc:  # benchmark records failures instead of hiding them
            result = exc
        return (time.perf_counter() - started) * 1000, result

    started = time.perf_counter()
    outcomes = await asyncio.gather(*(replace(index) for index in range(documents)))
    wall_ms = (time.perf_counter() - started) * 1000
    failures = [str(value) for _, value in outcomes if value is not True]
    with sqlite3.connect(db_path) as conn:
        durable_chunks = int(conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0])
    expected_chunks = documents * chunks_per_document
    return _result(
        "knowledge",
        "chunk_replace_concurrency",
        wall_ms,
        documents,
        latencies_ms=[latency for latency, _ in outcomes],
        quality={
            "preserved": not failures and durable_chunks == expected_chunks,
            "expected_chunks": expected_chunks,
            "durable_chunks": durable_chunks,
            "failed_writes": len(failures),
        },
        details={"concurrency": documents, "failure_messages": failures[:5]},
    )


async def _benchmark_database_init(
    _config: FeatureBenchmarkConfig, root: Path
) -> dict[str, Any]:
    from cyrene.runtime.database import init_db

    db_path = root / "runtime-init.db"
    started = time.perf_counter()
    await init_db(str(db_path))
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    await init_db(str(db_path))
    warm_ms = (time.perf_counter() - started) * 1000
    with sqlite3.connect(db_path) as conn:
        table_count = int(conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0])
    return _result(
        "persistence",
        "runtime_database_init",
        cold_ms + warm_ms,
        2,
        latencies_ms=[cold_ms, warm_ms],
        quality={"preserved": table_count > 0, "table_count": table_count},
        details={"cold_ms": round(cold_ms, 3), "warm_ms": round(warm_ms, 3)},
    )


async def _benchmark_scheduled_tasks(
    config: FeatureBenchmarkConfig, root: Path
) -> dict[str, Any]:
    from cyrene.runtime import database

    db_path = root / "scheduled-tasks.db"
    await database.init_db(str(db_path))
    task_count = max(1, config.scheduled_tasks)
    next_run = datetime.now(timezone.utc).isoformat()

    async def timed(operation: Awaitable[Any]) -> tuple[float, Any]:
        started = time.perf_counter()
        try:
            value = await operation
        except Exception as exc:  # benchmark records failures for the report
            value = exc
        return (time.perf_counter() - started) * 1000, value

    started = time.perf_counter()
    created = await asyncio.gather(*(
        timed(database.create_task(
            str(db_path),
            index,
            f"Benchmark scheduled task {index}",
            "once",
            "",
            next_run,
            project_id="benchmark",
        ))
        for index in range(task_count)
    ))
    task_ids = [value for _, value in created if isinstance(value, str)]
    updated = await asyncio.gather(*(
        timed(database.update_task_status(str(db_path), task_id, "paused"))
        for task_id in task_ids
    ))
    listed = await database.get_all_tasks(str(db_path), project_id="benchmark")
    deleted = await asyncio.gather(*(
        timed(database.delete_task(str(db_path), task_id))
        for task_id in task_ids[::2]
    ))
    remaining = await database.get_all_tasks(str(db_path), project_id="benchmark")
    wall_ms = (time.perf_counter() - started) * 1000
    failures = [
        value
        for _, value in [*created, *updated, *deleted]
        if isinstance(value, Exception) or value is False
    ]
    expected_remaining = len(task_ids) - len(task_ids[::2])
    operations = len(created) + len(updated) + len(deleted) + 2
    return _result(
        "scheduler",
        "scheduled_task_crud_concurrency",
        wall_ms,
        operations,
        latencies_ms=[latency for latency, _ in [*created, *updated, *deleted]],
        quality={
            "preserved": (
                not failures
                and len(task_ids) == task_count
                and len(listed) == task_count
                and len(remaining) == expected_remaining
            ),
            "expected_created": task_count,
            "actual_created": len(task_ids),
            "remaining": len(remaining),
            "failed_operations": len(failures),
        },
        details={"concurrency": task_count},
    )


async def _benchmark_file_hashing(
    config: FeatureBenchmarkConfig, root: Path
) -> dict[str, Any]:
    from cyrene.runtime.file_hashing import cached_sha256_file, sha256_file

    file_size = max(1, config.hash_file_bytes)
    reads = max(1, config.hash_cache_reads)
    path = root / "hash-fixture.bin"
    block = hashlib.sha256(b"cyrene-benchmark").digest()
    content = (block * ((file_size + len(block) - 1) // len(block)))[:file_size]
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    cached_sha256_file.cache_clear()

    started = time.perf_counter()
    cold_digest = sha256_file(path)
    cold_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    hot_digests = [sha256_file(path) for _ in range(reads)]
    hot_ms = (time.perf_counter() - started) * 1000
    return _result(
        "files",
        "content_hash_cold_and_cached",
        cold_ms + hot_ms,
        reads + 1,
        latencies_ms=[cold_ms, hot_ms / reads],
        quality={
            "preserved": cold_digest == expected and all(item == expected for item in hot_digests),
            "digest_matches": cold_digest == expected,
        },
        details={
            "file_bytes": file_size,
            "cold_ms": round(cold_ms, 3),
            "cached_reads_ms": round(hot_ms, 3),
        },
    )


Scenario = Callable[[FeatureBenchmarkConfig, Path], Awaitable[dict[str, Any]]]
SCENARIOS: tuple[Scenario, ...] = (
    _benchmark_event_bus,
    _benchmark_terminal,
    _benchmark_knowledge_search,
    _benchmark_knowledge_write,
    _benchmark_database_init,
    _benchmark_scheduled_tasks,
    _benchmark_file_hashing,
)


async def _run_parallel_rounds(
    scenario: Scenario,
    config: FeatureBenchmarkConfig,
    root: Path,
) -> dict[str, Any]:
    workers = max(2, int(config.parallel_workers))
    rounds = max(2, int(config.rounds_per_worker))
    cache_tracker = IdealPrefixCacheTracker(series_dimension="round")

    async def run_worker(worker: int) -> list[dict[str, Any]]:
        samples = []
        scope = f"{scenario.__name__}:{worker}"
        for round_index in range(rounds):
            round_root = root / f"worker-{worker}" / f"round-{round_index}"
            round_root.mkdir(parents=True)
            cache_tracker.record(
                scope,
                [scenario.__name__],
                series_index=round_index,
            )
            samples.append(await scenario(config, round_root))
        return samples

    started = time.perf_counter()
    worker_samples = await asyncio.gather(*(
        run_worker(worker) for worker in range(workers)
    ))
    wall_ms = (time.perf_counter() - started) * 1000
    samples = [sample for worker in worker_samples for sample in worker]
    representative = samples[-1]
    failed_rounds = sum(
        not bool(sample["quality"]["preserved"]) for sample in samples
    )
    result = _result(
        str(representative["feature"]),
        str(representative["scenario"]),
        wall_ms,
        sum(int(sample["operations"]) for sample in samples),
        latencies_ms=[float(sample["wall_ms"]) for sample in samples],
        quality={
            **dict(representative["quality"]),
            "preserved": failed_rounds == 0,
            "completed_rounds": len(samples),
            "failed_rounds": failed_rounds,
        },
        details={
            **dict(representative["details"]),
            "parallel_workers": workers,
            "rounds_per_worker": rounds,
        },
    )
    result["ideal_cache"] = cache_tracker.metrics()
    return result


async def run_benchmark(
    *,
    repeats: int = 1,
    config: FeatureBenchmarkConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    repeats = max(1, int(repeats))
    grouped_samples: list[list[dict[str, Any]]] = [[] for _ in SCENARIOS]
    with tempfile.TemporaryDirectory(prefix="cyrene-feature-benchmark-") as temporary:
        root = Path(temporary)
        for repeat in range(repeats):
            repeat_root = root / str(repeat)
            repeat_root.mkdir()
            for index, scenario in enumerate(SCENARIOS):
                grouped_samples[index].append(
                    await _run_parallel_rounds(
                        scenario,
                        config,
                        repeat_root / f"scenario-{index}",
                    )
                )

    results: list[dict[str, Any]] = []
    for samples in grouped_samples:
        combined = dict(samples[-1])
        for key in ("wall_ms", "operations_per_second", "latency_p50_ms", "latency_p95_ms"):
            combined[key] = round(
                statistics.median(float(sample[key]) for sample in samples), 3
            )
        combined["samples_ms"] = [sample["wall_ms"] for sample in samples]
        combined["quality"] = dict(combined["quality"])
        combined["quality"]["preserved"] = all(
            sample["quality"]["preserved"] for sample in samples
        )
        results.append(combined)

    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "repeats": repeats,
            "network_access": False,
            "real_credentials": False,
            "real_llm_calls": False,
        },
        "config": asdict(config),
        "results": results,
        "quality": {
            "preserved": all(item["quality"]["preserved"] for item in results),
            "failed_scenarios": [
                item["scenario"] for item in results if not item["quality"]["preserved"]
            ],
        },
        "ideal_cache": aggregate_ideal_cache_metrics(
            item["ideal_cache"] for item in results
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene local feature performance benchmark",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Feature | Scenario | Parallel | Rounds | Wall ms | p95 ms | Operations/s | Cache progression | Ideal cache | Quality |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            "| {feature} | {scenario} | {parallel_workers} | {rounds_per_worker} | "
            "{wall_ms:.3f} | {latency_p95_ms:.3f} | "
            "{operations_per_second:.1f} | {cache_progression} | "
            "{ideal_cache_rate:.2f}% | {quality_label} |".format(
                **item,
                parallel_workers=item["details"]["parallel_workers"],
                rounds_per_worker=item["details"]["rounds_per_worker"],
                cache_progression=ideal_cache_progression(item["ideal_cache"]),
                ideal_cache_rate=ideal_cache_percent(item["ideal_cache"]),
                quality_label="pass" if item["quality"]["preserved"] else "FAIL",
            )
        )
    lines.extend([
        "",
        "> All fixtures are local and deterministic; no network, credentials or LLM calls are used.",
        "> Ideal cache rate is theoretical fixture reuse; no runtime or model cache is read.",
        "",
    ])
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CONFIG",
    "FeatureBenchmarkConfig",
    "render_markdown",
    "run_benchmark",
]
