from __future__ import annotations

import asyncio
import base64
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
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, _data: None)
    statements: list[str] = []
    assert manager._db is not None
    manager._db.set_trace_callback(statements.append)
    queue = manager.subscribe(session.id)

    payload = b"x" * 1024
    for _ in range(100):
        manager._append_output(session, payload)

    event = queue.get_nowait()
    assert event["type"] == "output"
    assert event["seq"] == 0
    assert session.next_seq == len(payload) * 100
    assert not manager._scroll_path(session.id).exists()
    assert not any(statement.strip().upper() == "COMMIT" for statement in statements)

    manager.flush()

    assert manager._scroll_path(session.id).read_bytes() == payload * 100
    commits = [
        statement for statement in statements
        if statement.strip().upper() == "COMMIT"
    ]
    assert len(commits) == 1
    manager._drain_screen_now(session)
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
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, _data: None)

    manager._append_output(session, b"x" * OUTPUT_FLUSH_THRESHOLD)
    assert not manager._scroll_path(session.id).exists()

    await asyncio.sleep(0)

    assert manager._scroll_path(session.id).stat().st_size == OUTPUT_FLUSH_THRESHOLD
    manager._drain_screen_now(session)
    await asyncio.sleep(0)


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
    parsed: list[bytes] = []
    monkeypatch.setattr(manager, "_feed_screen", lambda _session, data: parsed.append(data))
    manager._persist_session(session)
    payload = b"FINAL_OUTPUT\n"

    manager._append_output(session, payload)
    manager._mark_exited(session, 0)

    row = manager._db.execute(
        "SELECT next_seq, status, exit_code FROM terminal_sessions WHERE id = ?",
        (session.id,),
    ).fetchone()
    assert tuple(row) == (len(payload), "exited", 0)
    assert manager._scroll_path(session.id).read_bytes() == payload
    assert b"".join(parsed) == payload
    await asyncio.sleep(0)
