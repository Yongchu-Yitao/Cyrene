"""Optimization boundaries: concurrency, invalidation and resource ownership."""

import json
import logging
import os
import sqlite3
import threading
import pytest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from cyrene.core import observability
from cyrene.core.context.store import ContextTreeStore
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.workbench.chat.chat_runs import ChatRunEventStore
from cyrene.workbench.chat.context_read_cache import ContextReadCache


def database(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE data(value)")
        db.execute("INSERT INTO data VALUES ('old')")
    return path


def test_unrelated_cache_hit_proceeds_during_miss_and_close_waits(tmp_path):
    first, second = database(tmp_path / "a"), database(tmp_path / "b")
    cache = ContextReadCache()
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    cache.read(first, lambda _: ["cached"], lambda _: None)

    def load(_):
        entered.set()
        assert release.wait(5)
        return ["slow"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        slow = pool.submit(cache.read, second, load, lambda _: None)
        assert entered.wait(2)
        assert pool.submit(cache.read, first, lambda _: None, lambda _: None).result(2) == ["cached"]
        closing = pool.submit(lambda: (cache.close(), closed.set()))
        assert not closed.wait(0.05)
        release.set()
        assert slow.result(2) == ["slow"]
        closing.result(2)
    assert not cache._entries and not cache._gates


def test_same_key_load_is_coalesced_and_evicted_connection_survives_lease(tmp_path):
    first, second = database(tmp_path / "a"), database(tmp_path / "b")
    cache = ContextReadCache(max_entries=1)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def load(_):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"x": [1]}

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            slow = pool.submit(cache.read, first, load, lambda _: None)
            assert entered.wait(2)
            assert cache.read(second, lambda _: ["other"], lambda _: None) == ["other"]
            release.set()
            assert slow.result(2) == {"x": [1]}
        assert len(cache._entries) <= 1
        cache.close()
        entered.clear()
        release.clear()
        calls.clear()
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(cache.read, first, load, lambda _: None) for _ in range(4)]
            assert entered.wait(2)
            release.set()
            values = [j.result(2) for j in jobs]
        assert len(calls) == 1
        values[0]["x"].append(2)
        assert values[1]["x"] == [1]
    finally:
        release.set()
        cache.close()


def test_cache_replacement_write_during_load_failure_and_retry(tmp_path):
    path = database(tmp_path / "a")
    cache = ContextReadCache()

    def load(_):
        with sqlite3.connect(path) as db:
            return db.execute("select value from data").fetchone()[0]

    try:
        assert cache.read(path, load, lambda _: None) == "old"
        new = database(tmp_path / "replacement")
        with sqlite3.connect(new) as db:
            db.execute("update data set value='new'")
        os.replace(new, path)
        assert cache.read(path, load, lambda _: None) == "new"

        def changing(_):
            with sqlite3.connect(path) as db:
                db.execute("update data set value='changed'")
            return "before write"

        assert cache.read(path, changing, lambda _: None, variant="race") == "before write"
        assert cache.read(path, load, lambda _: None, variant="race") == "changed"

        def failure(_):
            raise RuntimeError("expected")

        import pytest

        with pytest.raises(RuntimeError):
            cache.read(path, failure, lambda _: None, variant="failure")
        assert cache.read(path, load, lambda _: None, variant="failure") == "changed"
    finally:
        cache.close()


def test_disabled_logging_does_not_serialize_and_enabled_error_is_preserved(monkeypatch):
    logger = logging.getLogger("preservation")
    logger.setLevel(logging.INFO)
    encoded = []
    original = observability._bounded_operation_payload

    def encode(value):
        encoded.append(value)
        return original(value)

    monkeypatch.setattr(observability, "_bounded_operation_payload", encode)
    observability.log_operation(logger, "c", "a", phase="completed")
    assert not encoded
    observability.log_operation(logger, "c", "a", phase="failed", password="secret")
    assert len(encoded) == 1
    observability.log_operation(logger, "c", "a", level=object())  # logging must never break the caller


def test_debug_cache_append_rewrite_rotate_delete_and_return_isolation(tmp_path):
    path = tmp_path / "debug_1.jsonl"

    def event(n):
        return {"type": "llm_call", "event_id": str(n), "timestamp": str(n), "context_trace": {"token_by_type": {"x": n}}}

    path.write_text(json.dumps(event(1)) + "\ninvalid\n")
    repo = DebugEventRepository(tmp_path, recent_events=lambda _: [], full_event=lambda _: None, subscribe_events=lambda **_: None)
    first = repo.context_events(120)
    first["events"][0]["token_by_type"]["x"] = 99
    assert repo.context_events(120)["events"][0]["token_by_type"]["x"] == 1
    with path.open("a") as f:
        f.write(json.dumps(event(2)) + "\n")
    assert [x["id"] for x in repo.context_events(1)["events"]] == ["2"]
    stat = path.stat()
    path.write_text(json.dumps(event(3)) + "\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert [x["id"] for x in repo.context_events(120)["events"]] == ["3"]
    replacement = tmp_path / "new"
    replacement.write_text(json.dumps(event(4)) + "\n")
    os.replace(replacement, path)
    assert repo.context_events(120)["events"][0]["id"] == "4"
    path.unlink()
    assert repo.context_events(120) == {"events": []}


def test_missing_token_index_still_detects_external_null_and_backfills(tmp_path):
    path = tmp_path / "tree"
    tree = ContextTreeStore.create(path, tree_id="t", root_id="r", root_value={"role": "system", "content": "hello"})
    tree.close()
    with sqlite3.connect(path) as db:
        db.execute("update context_nodes set self_token_count=NULL,path_token_count=NULL")
    with ContextTreeStore(path) as tree:
        assert tree._connection.execute("SELECT self_token_count,path_token_count FROM context_nodes").fetchone()[0] is not None
        assert tree._connection.execute("SELECT name FROM sqlite_master WHERE name='idx_context_missing_tokens'").fetchone()


def test_event_store_closes_every_operation_connection(tmp_path, monkeypatch):
    store = ChatRunEventStore(str(tmp_path / "events"))
    opened = []
    original = store._connect

    def connect():
        connection = original()
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    store.create(SimpleNamespace(run_id="r", chat_id="c", status="running", created_at="2026", seq=0, events=[]))
    store.append_many("r", [{"_seq": 2, "type": "reply_delta", "delta": "kept"}])
    import pytest

    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT count(*) FROM workbench_chat_run_events").fetchone()[0] == 1


def test_cache_nested_cross_key_reads_and_reentrant_close(tmp_path):
    """Two loaders can read each other's keys without an ABBA gate deadlock."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from cyrene.workbench.chat.context_read_cache import ContextReadCache

    paths = [tmp_path / f"nested-{i}.db" for i in range(2)]
    for path in paths:
        connection = sqlite3.connect(path)
        connection.execute("create table x(n)")
        connection.close()
    cache = ContextReadCache()
    barrier = Barrier(2)

    def outer(index):
        def load(owner):
            barrier.wait(timeout=2)
            return cache.read(paths[1 - index], lambda _: ["nested"], owner)

        return cache.read(paths[index], load, lambda _: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(outer, i) for i in range(2)]
        assert [f.result(timeout=2) for f in futures] == [["nested"], ["nested"]]
    cache.close()

    def closing_loader(_):
        cache.close()
        return ["closed inside loader"]

    assert cache.read(paths[0], closing_loader, lambda _: None) == ["closed inside loader"]
    assert not cache._entries and not cache._gates and cache._active == 0
    assert cache.read(paths[0], lambda _: ["reopened"], lambda _: None) == ["reopened"]
    cache.close()


@pytest.mark.asyncio
async def test_context_debug_route_keeps_event_loop_available():
    import asyncio
    import threading
    from fastapi import APIRouter
    from starlette.requests import Request
    from cyrene.workbench.http.system.events import register_event_routes

    started, release = threading.Event(), threading.Event()
    worker_ids = []

    class Repository:
        def context_events(self, limit):
            worker_ids.append(threading.get_ident())
            started.set()
            assert release.wait(2)
            return {"events": [], "observed_limit": limit}

    router = APIRouter()
    register_event_routes(router, Repository())
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/context-debug/events")
    request = Request({"type": "http", "query_string": b"limit=999"})
    call = asyncio.create_task(endpoint(request))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set() and not call.done()
        assert worker_ids == [worker_ids[0]] and worker_ids[0] != threading.get_ident()
    finally:
        release.set()
    assert await call == {"events": [], "observed_limit": 500}


@pytest.mark.parametrize("failure", ["open", "midstream"])
def test_debug_cache_retries_transient_read_failures(tmp_path, monkeypatch, failure):
    import errno

    path = tmp_path / "debug_archived.jsonl"
    path.write_text("\n".join(json.dumps({"type": "llm_call", "event_id": str(i), "timestamp": str(i)}) for i in range(2)) + "\n")
    repo = DebugEventRepository(tmp_path, recent_events=lambda _: [], full_event=lambda _: None, subscribe_events=lambda **_: None)
    original_open = Path.open

    class InterruptedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __iter__(self):
            yield next(self.handle)
            raise OSError(errno.EIO, "temporary read failure")

    def failing_open(self, *args, **kwargs):
        if self != path:
            return original_open(self, *args, **kwargs)
        if failure == "open":
            raise OSError(errno.EMFILE, "temporary descriptor exhaustion")
        return InterruptedReader(original_open(self, *args, **kwargs))

    before = path.stat()
    with monkeypatch.context() as context:
        context.setattr(Path, "open", failing_open)
        assert len(repo.context_events(10)["events"]) == (0 if failure == "open" else 1)
        assert not repo._summary_cache
    assert [e["id"] for e in repo.context_events(10)["events"]] == ["1", "0"]
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    assert path in repo._summary_cache


def test_debug_cache_admission_cannot_break_limited_http_result(tmp_path):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from cyrene.workbench.http.system.events import register_event_routes

    events = [{"type": "llm_call", "event_id": "old", "timestamp": "2020", "model": "\ud800"}, {"type": "llm_call", "event_id": "new", "timestamp": "2026", "model": "normal"}]
    (tmp_path / "debug_unicode.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    repo = DebugEventRepository(tmp_path, recent_events=lambda _: [], full_event=lambda _: None, subscribe_events=lambda **_: None)
    app, router = FastAPI(), APIRouter()
    register_event_routes(router, repo)
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(2):
            response = client.get("/api/context-debug/events?limit=1")
            assert response.status_code == 200
            assert [e["id"] for e in response.json()["events"]] == ["new"]
    assert repo._summary_cache


def test_debug_cache_measurement_failure_only_disables_caching(tmp_path, monkeypatch):
    import cyrene.observability.debug_event_repository as repository_module

    path = tmp_path / "debug_1.jsonl"
    path.write_text('{"type":"llm_call","event_id":"kept"}\n')
    repo = DebugEventRepository(tmp_path, recent_events=lambda _: [], full_event=lambda _: None, subscribe_events=lambda **_: None)

    def failed_measurement(*args, **kwargs):
        raise ValueError("unmeasurable")

    with monkeypatch.context() as context:
        context.setattr(repository_module.json, "dumps", failed_measurement)
        assert repo.context_events(1)["events"][0]["id"] == "kept"
        assert not repo._summary_cache
    assert repo.context_events(1)["events"][0]["id"] == "kept"
    assert path in repo._summary_cache


def test_terminal_printable_runs_preserve_control_and_utf8_boundaries():
    from cyrene.plugins.builtin.cyrene_code.terminal.history import IncrementalPlainTextParser

    long = b"a" * 48
    data = b"first\r" + long + b"\b!\n\x1b]0;" + long + b"\x07" + "中文🦊".encode() + b"\xff" + long + b"\n"
    expected = ["first", "a" * 47 + "!", "中文🦊\ufffd" + "a" * 48]
    for width in [1, 2, 15, 16, 17, 31, 64, len(data)]:
        parser = IncrementalPlainTextParser()
        lines = []
        for offset in range(0, len(data), width):
            lines.extend(parser.feed(data[offset : offset + width], start_seq=offset))
            parser = IncrementalPlainTextParser(parser.state())
        assert [line["text"] for line in lines] == expected
        assert lines[-1]["endSeq"] == len(data)
        assert parser.current_line()["text"] == ""
