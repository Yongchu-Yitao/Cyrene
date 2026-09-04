from __future__ import annotations

import pytest

from cyrene.workbench.core_adapter import conversation_runtime


def _config(tmp_path, chat_id: str = "chat-outbox"):
    return conversation_runtime.ConversationConfig(
        session_id=chat_id,
        workspace_dir=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "workbench.sqlite3"),
    )


@pytest.mark.asyncio
async def test_send_never_waits_for_commit_outbox(tmp_path, monkeypatch):
    runtime = conversation_runtime.ConversationRuntime()
    config = _config(tmp_path)
    result = object()
    kicks: list[str] = []

    async def fail_if_drained(*_args, **_kwargs):
        raise AssertionError("the foreground send path must not drain the outbox")

    async def use_bridge(_config, _operation, *, publish):
        assert publish is not None
        return result

    monkeypatch.setattr(runtime, "drain_commit_outbox", fail_if_drained)
    monkeypatch.setattr(runtime, "_with_bridge", use_bridge)
    monkeypatch.setattr(runtime, "kick_commit_outbox", kicks.append)

    actual = await runtime.send(config, "hello", run_id="run-1")

    assert actual is result
    assert kicks == [config.session_id]


@pytest.mark.asyncio
async def test_background_outbox_drains_all_events_with_one_bridge(
    tmp_path,
    monkeypatch,
):
    runtime = conversation_runtime.ConversationRuntime()
    config = _config(tmp_path)
    runtime._configs[config.session_id] = config
    pending = [
        {
            "event_id": f"event-{index}",
            "run_id": f"run-{index}",
            "node_id": f"node-{index}",
        }
        for index in range(25)
    ]
    completed: list[str] = []
    opened = 0

    class Repository:
        def __init__(self, _db_path):
            pass

        def pending_commit_events(self, _chat_id, *, limit):
            assert limit == 1
            return [pending.pop(0)] if pending else []

        def complete_commit_event(self, event_id):
            completed.append(event_id)

        def fail_commit_event(self, _event_id, _error):
            raise AssertionError("valid events must not fail")

    class Bridge:
        async def commit_public_turn(self, _run_id, _node_id, _event):
            return None

    async def use_bridge(_config, operation, *, publish):
        nonlocal opened
        opened += 1
        assert publish is None
        return await operation(Bridge())

    from cyrene.workbench.chat import chat_repository

    monkeypatch.setattr(chat_repository, "ChatRepository", Repository)
    monkeypatch.setattr(runtime, "_with_bridge", use_bridge)

    count = await runtime.drain_commit_outbox(chat_id=config.session_id)

    assert count == 25
    assert opened == 1
    assert completed == [f"event-{index}" for index in range(25)]


@pytest.mark.asyncio
async def test_bridge_open_and_close_publish_component_durations(
    tmp_path,
    monkeypatch,
):
    runtime = conversation_runtime.ConversationRuntime()
    config = _config(tmp_path, "chat-timing")
    events: list[dict] = []

    class Bridge:
        def close(self):
            return None

    monkeypatch.setattr(runtime, "_open_bridge", lambda *_args, **_kwargs: Bridge())

    async def publish(event):
        events.append(dict(event))

    result = await runtime._with_bridge(
        config,
        lambda _bridge: _completed("done"),
        publish=publish,
    )

    assert result == "done"
    by_stage = {event.get("stage"): event for event in events}
    assert by_stage["agent_bridge_open"]["durationMs"] >= 0
    assert by_stage["agent_bridge_close"]["durationMs"] >= 0


async def _completed(value):
    return value
