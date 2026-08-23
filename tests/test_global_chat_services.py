from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cyrene.workbench.subagent_messaging_service import (
    AgentBroadcastCommand,
    AgentMentionCommand,
    SubagentMessagingService,
)


@pytest.mark.asyncio
async def test_broadcast_preserves_inbox_then_reactivate_then_event_order(monkeypatch):
    from cyrene.workbench import subagent_messaging_service as messaging

    calls: list[tuple[str, object]] = []
    info = {
        "status": "done",
        "task": "inspect",
        "round_id": "round_1",
    }
    monkeypatch.setattr(messaging.subagent, "_registry", {"agent_1": info})

    async def clear(agent_id: str):
        calls.append(("clear", agent_id))

    async def send(*args, **kwargs):
        calls.append(("send", args[3]))
        return "message_1"

    async def reactivate(agent_id: str):
        calls.append(("reactivate", agent_id))
        return True

    async def raw_messages(agent_id: str):
        calls.append(("raw", agent_id))
        return [{"role": "user", "content": "old"}]

    async def resumed_run(*args, **kwargs):
        return None

    def spawn(awaitable, agent_id: str):
        calls.append(("spawn", agent_id))
        awaitable.close()

    async def publish(event, **kwargs):
        calls.append(("event", event["message"]["id"]))

    monkeypatch.setattr(messaging, "clear_inbox", clear)
    monkeypatch.setattr(messaging, "send_message", send)
    monkeypatch.setattr(messaging.subagent, "reactivate", reactivate)
    monkeypatch.setattr(messaging.subagent, "get_raw_messages", raw_messages)
    monkeypatch.setattr(messaging.subagent, "_run_subagent", resumed_run)
    monkeypatch.setattr(messaging.subagent, "_spawn_subagent_task", spawn)
    monkeypatch.setattr(messaging.debug, "publish_event", publish)

    result = await SubagentMessagingService(None, "db.sqlite3").broadcast(
        AgentBroadcastCommand(
            round_id="round_1",
            text="review",
            mentions=["agent_1"],
            attachments=[{"name": "brief.md", "path": "/tmp/brief.md"}],
        )
    )

    assert result == {"ok": True, "sent_to": ["agent_1"]}
    assert [name for name, _ in calls] == [
        "clear", "send", "reactivate", "raw", "spawn", "event"
    ]
    assert "[/tmp/brief.md]" not in str(calls[1][1])
    assert "[brief.md](/tmp/brief.md)" in str(calls[1][1])


@pytest.mark.asyncio
async def test_mentions_persist_only_valid_targets_after_delivery(monkeypatch):
    from cyrene.workbench import subagent_messaging_service as messaging

    monkeypatch.setattr(
        messaging.subagent,
        "_registry",
        {"agent_1": {"status": "running", "task": "inspect"}},
    )
    send = AsyncMock(return_value="message_1")
    persist = AsyncMock()
    monkeypatch.setattr(messaging, "send_message", send)
    monkeypatch.setattr(messaging.agent_service, "_append_session_message", persist)

    result = await SubagentMessagingService(None, "").send_mentions(
        AgentMentionCommand(
            "new guidance",
            ["missing", "agent_1"],
            [{"id": "file_1", "name": "brief.md"}],
            "request_1",
        )
    )

    assert result == {"response": "Message sent to @agent_1."}
    send.assert_awaited_once()
    entry = persist.await_args.args[0]
    assert entry["content"] == "@agent_1 new guidance"
    assert entry["mentions"] == ["agent_1"]
    assert entry["client_request_id"] == "request_1"
