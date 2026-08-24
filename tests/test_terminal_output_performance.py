from __future__ import annotations

import asyncio
import base64
import sqlite3
import threading
from pathlib import Path

import pytest

from cyrene.terminal.manager import (
    OUTPUT_FLUSH_THRESHOLD,
    PTY_READ_BUDGET,
    SCREEN_DRAIN_BUDGET,
    TerminalManager,
    TerminalSession,
    _now_iso,
)


def _segment_bytes(manager: TerminalManager, terminal_id: str) -> bytes:
    directory = manager._scroll_segment_dir(terminal_id)
    return b"".join(
        path.read_bytes() for path in sorted(directory.glob("*.bin"))
    )


def _session(tmp_path: Path, terminal_id: str = "term_perf") -> TerminalSession:
    now = _now_iso()
    return TerminalSession(
        id=terminal_id,
        project_id="project-1",
        title="Performance",
        cwd=str(tmp_path),
        shell="sh",
        argv=["/bin/sh"],
        created_at=now,
        updated_at=now,
        status="running",
    )


@pytest.mark.asyncio
async def test_output_is_live_before_one_batched_durable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(state_dir=tmp_path / "state")
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    manager.flush()
    writer = manager._persistence_writer
    assert writer is not None
    initial_batches = writer.batch_count
    queue = manager.subscribe(session.id)

    payload = b"x" * 1024
    for _ in range(100):
        manager._append_output(session, payload)

    event = queue.get_nowait()
    assert event["type"] == "output"
    assert event["seq"] == 0
    assert session.next_seq == len(payload) * 100
    assert not manager._scroll_segment_dir(session.id).exists()
    assert writer.batch_count == initial_batches

    manager.flush()

    assert _segment_bytes(manager, session.id) == payload * 100
    assert writer.batch_count == initial_batches + 1
    assert writer.thread_id is not None
    assert writer.thread_id != threading.get_ident()
    assert (await manager.screen_snapshot_async(session.id))["screenText"]
    manager.close_store()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_threshold_flushes_on_next_loop_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(state_dir=tmp_path / "state")
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    manager.flush()
    writer = manager._persistence_writer
    assert writer is not None
    initial_submitted = writer.submitted

    manager._append_output(session, b"x" * OUTPUT_FLUSH_THRESHOLD)
    assert not manager._scroll_segment_dir(session.id).exists()

    await asyncio.sleep(0)

    assert writer.submitted == initial_submitted + 1
    manager.flush()
    assert len(_segment_bytes(manager, session.id)) == OUTPUT_FLUSH_THRESHOLD
    manager.close_store()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_slow_persistence_does_not_block_live_output_or_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(state_dir=tmp_path / "state")
    session = _session(tmp_path, "term_slow_writer")
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    manager.flush()
    scroll_dir = manager._scroll_segment_dir(session.id)
    original_open = Path.open
    write_started = threading.Event()
    release_write = threading.Event()

    def delayed_open(path: Path, *args, **kwargs):
        mode = str(args[0] if args else kwargs.get("mode") or "r")
        if path.parent == scroll_dir and "a" in mode:
            write_started.set()
            release_write.wait(timeout=2)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", delayed_open)
    payload = b"live" * (OUTPUT_FLUSH_THRESHOLD // 4)
    queue = manager.subscribe(session.id)
    try:
        manager._append_output(session, payload)
        await asyncio.sleep(0)
        for _ in range(100):
            if write_started.is_set():
                break
            await asyncio.sleep(0.001)

        assert write_started.is_set()
        assert queue.get_nowait()["nextSeq"] == len(payload)
        snapshot = manager.scrollback_snapshot(
            session.id, cursor=0, max_bytes=len(payload)
        )
        assert base64.b64decode(snapshot["data"]) == payload
        await asyncio.sleep(0)
    finally:
        release_write.set()
    manager.flush()
    manager.close_store()


@pytest.mark.asyncio
async def test_persistence_backpressure_pauses_and_resumes_posix_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(
        state_dir=tmp_path / "state",
        persistence_backlog_limit=PTY_READ_BUDGET * 2,
    )
    session = _session(tmp_path, "term_backpressure")
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    manager.flush()
    session.master_fd = 123
    monkeypatch.setattr(manager, "_queue_screen_data", lambda _session, _data: None)
    payload = b"p" * PTY_READ_BUDGET
    monkeypatch.setattr("cyrene.terminal.manager.os.read", lambda _fd, _size: payload)
    loop = asyncio.get_running_loop()
    removed: list[int] = []
    added: list[int] = []
    monkeypatch.setattr(loop, "remove_reader", lambda fd: removed.append(fd) or True)
    monkeypatch.setattr(
        loop, "add_reader", lambda fd, _callback, *_args: added.append(fd)
    )
    try:
        manager._read_posix_ready(session.id)
        manager._read_posix_ready(session.id)

        assert session.pty_read_paused is True
        assert manager._persistence_backlog_bytes == PTY_READ_BUDGET * 2
        assert removed == [123]

        manager.flush()
        manager._poll_persistence_backpressure()

        assert session.pty_read_paused is False
        assert manager._persistence_backlog_bytes == 0
        assert added == [123]
    finally:
        session.master_fd = None
        manager.close_store()


def test_scrollback_trim_runs_in_writer_and_preserves_sequence_offsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(output_limit=64 * 1024, state_dir=tmp_path / "state")
    session = _session(tmp_path, "term_trim")
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    manager.flush()
    monkeypatch.setattr(manager, "_queue_screen_data", lambda _session, _data: None)
    payload = bytes(range(256)) * 1536

    manager._append_output(session, payload)
    manager.flush()

    expected = payload[-manager.output_limit:]
    segment_paths = sorted(manager._scroll_segment_dir(session.id).glob("*.bin"))
    assert _segment_bytes(manager, session.id) == expected
    assert all(path.stat().st_size <= manager.output_limit for path in segment_paths)
    assert len(segment_paths) == 1
    assert session.output_start_seq == len(payload) - len(expected)
    snapshot = manager.scrollback_snapshot(
        session.id, cursor=session.output_start_seq, max_bytes=len(expected)
    )
    assert base64.b64decode(snapshot["data"]) == expected
    row = manager._db.execute(
        "SELECT output_start_seq FROM terminal_sessions WHERE id = ?", (session.id,)
    ).fetchone()
    assert int(row[0]) == session.output_start_seq
    writer = manager._persistence_writer
    assert writer is not None
    metrics = writer.metrics()
    assert metrics["bytesWritten"] == len(payload)
    assert metrics["segmentsDeleted"] == 5
    assert metrics["evictions"] == 1
    manager.close_store()


def test_posix_reader_processes_at_most_one_budget_per_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager()
    session = _session(tmp_path)
    session.master_fd = 123
    manager._sessions[session.id] = session
    reads: list[int] = []
    appended: list[bytes] = []

    def fake_read(_fd: int, size: int) -> bytes:
        reads.append(size)
        return b"x" * size

    monkeypatch.setattr("cyrene.terminal.manager.os.read", fake_read)
    monkeypatch.setattr(manager, "_append_output", lambda _session, data: appended.append(data))

    manager._read_posix_ready(session.id)

    assert reads == [PTY_READ_BUDGET]
    assert [len(data) for data in appended] == [PTY_READ_BUDGET]


def test_full_subscriber_queue_requests_explicit_resync(tmp_path: Path) -> None:
    manager = TerminalManager()
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    queue = manager.subscribe(session.id)
    for index in range(queue.maxsize):
        queue.put_nowait({"type": "output", "seq": index, "nextSeq": index + 1})
    session.next_seq = 1234

    manager._publish(session, {"type": "output", "seq": 1233, "nextSeq": 1234})

    assert queue.qsize() == 1
    assert queue.get_nowait() == {"type": "resync_required", "nextSeq": 1234}


def test_replay_coalesces_small_pty_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(output_limit=2 * 1024 * 1024)
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, _data: None)
    payload = b"z" * 1024
    for _ in range(1024):
        manager._append_output(session, payload)

    replay = manager.replay(session.id, 0)

    assert len(replay) == 4
    assert b"".join(base64.b64decode(event["data"]) for event in replay) == payload * 1024


@pytest.mark.asyncio
async def test_screen_parser_is_deferred_and_drained_in_bounded_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager()
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    parsed: list[bytes] = []
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, data: parsed.append(data))
    queue = manager.subscribe(session.id)
    payload = b"s" * (SCREEN_DRAIN_BUDGET * 3)

    manager._append_output(session, payload)

    assert queue.get_nowait()["nextSeq"] == len(payload)
    assert parsed == []
    for _ in range(5):
        await asyncio.sleep(0)

    assert b"".join(parsed) == payload
    assert all(len(part) <= SCREEN_DRAIN_BUDGET for part in parsed)


@pytest.mark.asyncio
async def test_deferred_screen_backlog_stays_within_scrollback_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TerminalManager(output_limit=64 * 1024)
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, _data: None)

    for _ in range(4):
        manager._append_output(session, b"b" * (64 * 1024))

    assert session.screen_pending_bytes <= manager.output_limit
    manager._drain_screen_now(session)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_exit_forces_scrollback_metadata_and_screen_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(state_dir=state_dir)
    session = _session(tmp_path)
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    payload = b"FINAL_OUTPUT\n"

    manager._append_output(session, payload)
    manager._mark_exited(session, 0)
    manager.flush()

    row = manager._db.execute(
        "SELECT next_seq, status, exit_code FROM terminal_sessions WHERE id = ?",
        (session.id,),
    ).fetchone()
    assert tuple(row) == (len(payload), "exited", 0)
    assert _segment_bytes(manager, session.id) == payload
    assert "FINAL_OUTPUT" in (
        await manager.screen_snapshot_async(session.id)
    )["screenText"]
    manager.close_store()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_screen_history_search_and_command_index_share_background_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cyrene.terminal.manager as manager_module

    manager = TerminalManager(state_dir=tmp_path / "state")
    session = _session(tmp_path, "term_background_queries")
    manager._sessions[session.id] = session
    manager._persist_session(session)
    manager.flush()
    writer = manager._persistence_writer
    assert writer is not None
    parser_threads: list[int] = []
    metadata_threads: list[int] = []
    command_threads: list[int] = []
    text_threads: list[int] = []
    original_feed = manager_module.pyte.Stream.feed
    original_metadata_feed = manager_module.OscMetadataParser.feed
    original_commands = manager_module.osc133_commands
    original_plain_text = manager_module.plain_terminal_text

    def tracked_feed(stream, data):
        parser_threads.append(threading.get_ident())
        return original_feed(stream, data)

    def tracked_commands(*args, **kwargs):
        command_threads.append(threading.get_ident())
        return original_commands(*args, **kwargs)

    def tracked_metadata(parser, data, *, start_seq):
        metadata_threads.append(threading.get_ident())
        return original_metadata_feed(parser, data, start_seq=start_seq)

    def tracked_plain_text(*args, **kwargs):
        text_threads.append(threading.get_ident())
        return original_plain_text(*args, **kwargs)

    monkeypatch.setattr(manager_module.pyte.Stream, "feed", tracked_feed)
    monkeypatch.setattr(
        manager_module.OscMetadataParser, "feed", tracked_metadata
    )
    monkeypatch.setattr(manager_module, "osc133_commands", tracked_commands)
    monkeypatch.setattr(manager_module, "plain_terminal_text", tracked_plain_text)
    payload = (
        b"\x1b]133;A\x1b\\\x1b]133;B\x1b\\echo worker\r\n"
        b"\x1b]133;C\x1b\\BACKGROUND_WORKER_OUTPUT\r\n"
        b"\x1b]133;D;0\x1b\\"
    )

    manager._append_output(session, payload)
    manager.flush()
    screen = await manager.screen_snapshot_async(session.id)
    commands = await manager.commands_async(session.id)
    matches = await manager.search_history_async(
        session.project_id, "background_worker_output", terminal_id=session.id
    )

    assert "BACKGROUND_WORKER_OUTPUT" in screen["screenText"]
    assert commands[0]["command"] == "echo worker"
    assert matches
    assert writer.thread_id is not None
    assert parser_threads and set(parser_threads) == {writer.thread_id}
    assert metadata_threads and set(metadata_threads) == {writer.thread_id}
    assert command_threads and set(command_threads) == {writer.thread_id}
    assert text_threads and set(text_threads) == {writer.thread_id}
    assert writer.thread_id != threading.get_ident()
    manager.close_store()


def test_legacy_scrollback_migrates_to_segments_without_changing_sequences(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    manager = TerminalManager(state_dir=state_dir)
    session = _session(tmp_path, "term_legacy")
    session.next_seq = 12_345 + 200_000
    session.output_start_seq = 12_345
    manager._sessions[session.id] = session
    manager._persist_session(session)
    manager.flush()
    manager.close_store()

    payload = bytes(range(251)) * 796 + b"tail"
    payload = payload[:200_000]
    legacy = state_dir / "scrollback" / f"{session.id}.bin"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(payload)
    connection = sqlite3.connect(state_dir / "terminals.sqlite3")
    connection.execute(
        "UPDATE terminal_sessions SET next_seq=?, output_start_seq=? WHERE id=?",
        (12_345 + len(payload), 12_345, session.id),
    )
    connection.commit()
    connection.close()

    restored = TerminalManager(state_dir=state_dir)
    snapshot = restored.scrollback_snapshot(
        session.id, cursor=12_345, max_bytes=len(payload)
    )

    assert snapshot["startSeq"] == 12_345
    assert snapshot["endSeq"] == 12_345 + len(payload)
    assert base64.b64decode(snapshot["data"]) == payload
    assert not legacy.exists()
    assert _segment_bytes(restored, session.id) == payload
    restored.close_store()


@pytest.mark.asyncio
async def test_contiguous_output_events_are_coalesced_and_cleanup_requires_eviction(
    tmp_path: Path,
) -> None:
    manager = TerminalManager(output_limit=128 * 1024, state_dir=tmp_path / "state")
    session = _session(tmp_path, "term_events")
    manager._sessions[session.id] = session
    manager._persist_session(session)
    manager.flush()
    writer = manager._persistence_writer
    assert writer is not None

    for _ in range(100):
        manager._append_output(session, b"event\n")
    manager.flush()
    row_count = manager._db.execute(
        "SELECT COUNT(*) FROM terminal_output_events WHERE terminal_id=?",
        (session.id,),
    ).fetchone()[0]
    assert row_count == 1
    assert writer.metrics()["evictions"] == 0

    manager._append_output(session, b"x" * (256 * 1024))
    manager.flush()
    after_eviction = writer.metrics()["evictions"]
    manager._append_output(session, b"tail")
    manager.flush()

    assert after_eviction == 1
    assert writer.metrics()["evictions"] == after_eviction
    assert manager._db.execute(
        "SELECT MIN(end_seq) FROM terminal_output_events WHERE terminal_id=?",
        (session.id,),
    ).fetchone()[0] > session.output_start_seq
    manager.close_store()
