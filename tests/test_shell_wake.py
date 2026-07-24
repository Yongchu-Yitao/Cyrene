"""Tests for wake-from-terminal (shell-exit → Workbench chat run)."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_shell_wake_service():
    from cyrene.shell_wake import get_shell_wake_service

    service = get_shell_wake_service()
    service.reset_for_tests()
    yield
    service.reset_for_tests()


def test_build_shell_wake_prompt_includes_exit_and_tail():
    from cyrene.shell_wake import build_shell_wake_prompt

    prompt = build_shell_wake_prompt(
        shell_id="shell_1",
        status="err",
        exit_code=2,
        title="train",
        cwd="experiments",
        elapsed="1h 02m",
        note="review metrics",
        lines=[
            {"kind": "prompt", "text": "$ python train.py"},
            {"kind": "out", "text": "epoch 1 loss=1.2"},
            {"kind": "err", "text": "CUDA OOM"},
        ],
    )
    assert "[Shell exited — automatic wake]" in prompt
    assert "shell_id: shell_1" in prompt
    assert "exit_code: 2" in prompt
    assert "wake_note: review metrics" in prompt
    assert "$ python train.py" in prompt
    assert "[err] CUDA OOM" in prompt
    assert "Do not sleep or poll" in prompt


@pytest.mark.asyncio
async def test_shell_wake_dispatches_when_chat_idle():
    from cyrene.shell_wake import ShellWakeService

    service = ShellWakeService()
    calls: list[dict] = []

    async def dispatcher(wake):
        calls.append(wake)
        return "started"

    service.configure(dispatcher=dispatcher, is_busy=lambda _chat_id: False)
    await service.register_wake(
        shell_id="shell_a",
        chat_id="chat_a",
        note="continue training loop",
        title="train",
    )
    ready = await service.on_shell_exit(
        "shell_a",
        status="done",
        exit_code=0,
        snapshot={
            "title": "train",
            "cwd": ".",
            "elapsed": "00:01",
            "lines": [{"kind": "out", "text": "ok"}],
        },
    )
    assert ready is not None
    assert ready["status"] == "dispatched" or calls
    assert len(calls) == 1
    assert calls[0]["chat_id"] == "chat_a"
    assert "exit_code: 0" in calls[0]["prompt"]
    assert "ok" in calls[0]["prompt"]


@pytest.mark.asyncio
async def test_shell_wake_defers_when_chat_busy_then_dispatches():
    from cyrene.shell_wake import ShellWakeService

    service = ShellWakeService()
    busy = {"chat_b": True}
    calls: list[str] = []

    async def dispatcher(wake):
        calls.append(wake["wake_id"])
        return "started"

    service.configure(
        dispatcher=dispatcher,
        is_busy=lambda chat_id: bool(busy.get(chat_id)),
    )
    record = await service.register_wake(shell_id="shell_b", chat_id="chat_b")
    await service.on_shell_exit(
        "shell_b",
        status="err",
        exit_code=1,
        snapshot={"lines": [{"kind": "err", "text": "failed"}]},
    )
    assert calls == []
    snap = service.snapshot()
    assert record["wake_id"] in (snap["pending_by_chat"].get("chat_b") or [])

    busy["chat_b"] = False
    results = await service.try_dispatch("chat_b")
    assert calls == [record["wake_id"]]
    assert results[0]["result"] == "started"


@pytest.mark.asyncio
async def test_watch_shell_triggers_wake_service(monkeypatch, tmp_path):
    from cyrene import shells
    from cyrene.shell_wake import get_shell_wake_service

    service = get_shell_wake_service()
    calls: list[dict] = []

    async def dispatcher(wake):
        calls.append(wake)
        return "started"

    service.configure(dispatcher=dispatcher, is_busy=lambda _chat_id: False)

    # Keep workspace resolution inside the temp dir.
    monkeypatch.setattr(shells, "WORKSPACE_DIR", tmp_path)
    (tmp_path / "workspace").mkdir(exist_ok=True)

    snap = await shells.start_shell(
        command="printf 'hello-wake\\n'; exit 7",
        cwd=".",
        title="wake-test",
        wake_on_exit=True,
        wake_chat_id="chat_watch",
        wake_note="check exit",
    )
    assert snap.get("wakeOnExit") is True
    assert snap.get("wakeId")

    # Wait for the process + wake dispatch.
    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.05)

    assert calls, "expected shell-exit wake dispatch"
    assert calls[0]["chat_id"] == "chat_watch"
    assert "exit_code: 7" in calls[0]["prompt"] or "exit_code: 7" in calls[0].get("prompt", "")
    # Interactive shells may wrap the command; accept either captured output or meta.
    prompt = calls[0]["prompt"]
    assert "shell_id:" in prompt
    assert "automatic wake" in prompt


@pytest.mark.asyncio
async def test_chat_run_finalize_dispatches_pending_shell_wake(monkeypatch):
    from cyrene.shell_wake import get_shell_wake_service
    from webui.workbench_chat_runs import ChatRunManager

    service = get_shell_wake_service()
    dispatched = asyncio.Event()

    async def dispatcher(wake):
        dispatched.set()
        return "started"

    service.configure(dispatcher=dispatcher, is_busy=lambda chat_id: chat_id == "chat_busy")
    await service.register_wake(shell_id="shell_c", chat_id="chat_busy")
    await service.on_shell_exit(
        "shell_c",
        status="done",
        exit_code=0,
        snapshot={"lines": [{"kind": "out", "text": "done"}]},
    )
    assert not dispatched.is_set()

    manager = ChatRunManager(retention_seconds=0)

    async def runner(run):
        # While this run is live, is_busy stays true via the checker below.
        run.outcome = {"kind": "reply", "payload": {}}
        await asyncio.sleep(0.01)

    # Rebind busy checker to the manager after the run starts.
    service.configure(
        dispatcher=dispatcher,
        is_busy=lambda chat_id: manager.get(chat_id) is not None,
    )

    # Seed a ready wake again after reconfigure (previous attempt queued).
    # The wake should still be pending from on_shell_exit.
    run, _ = manager.start_or_get("chat_busy", {"type": "ack"}, runner, stream=False)
    await asyncio.wait_for(run.done.wait(), timeout=2)
    await asyncio.wait_for(dispatched.wait(), timeout=2)
