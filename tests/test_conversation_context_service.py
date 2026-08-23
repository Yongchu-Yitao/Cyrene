import json
from types import SimpleNamespace

import pytest

from cyrene.workbench.conversation_context_service import (
    ConversationContextQueryService,
    ConversationInboxQueryService,
    SessionStateRepository,
)


class _Chats:
    def __init__(self, chat):
        self.chat = chat
        self.read_calls = 0

    def read(self):
        self.read_calls += 1
        return {"chats": [self.chat] if self.chat else []}

    def find(self, payload, chat_id):
        return next(
            (item for item in payload["chats"] if item["id"] == chat_id),
            None,
        )


class _ExternalAgentRuntime:
    BUILTIN_INSTALLATION_ID = "builtin"

    @staticmethod
    def chat_agent_fields(_chat):
        return {"agent": {"installationId": "external"}}


def _context_service(tmp_path, chat, state):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    async def compact(*_args, **_kwargs):
        return {"compacted": True}

    return ConversationContextQueryService(
        states=SessionStateRepository(lambda _session_id: state_path),
        chats=_Chats(chat),
        agent_runtime=_ExternalAgentRuntime(),
        context_payload=lambda *_args, **_kwargs: {},
        context_segments=lambda messages: {
            "user": 5 * sum(item.get("role") == "user" for item in messages),
        },
        subagent_payload=lambda *_args: {},
        compact_session=compact,
        default_model=lambda: "model",
        context_limit=lambda _model: 128_000,
        approx_token_count=lambda text: len(text),
    )


@pytest.mark.asyncio
async def test_context_blocks_preserve_agent_report_layer_order(tmp_path):
    service = _context_service(
        tmp_path,
        {
            "id": "chat_1",
            "agentContextReport": {
                "used": 12,
                "size": 100,
                "segments": [{"label": "Agent memory", "tokens": 10}],
            },
        },
        {
            "messages": [{"role": "user", "content": "hello"}],
            "system_context_blocks": [{"id": "system", "tokens_est": 3}],
            "ephemeral_context": "xy",
        },
    )

    result = await service.blocks("chat_1", "chat_1", legacy=False)

    assert [layer["id"] for layer in result["layers"]] == [
        "agent_segment_1",
        "agent_other",
        "system_prefix",
        "ephemeral",
        "messages",
    ]
    assert result["totalTokensEst"] == 22
    assert result["messageTokens"] == 5
    assert result["compositionSource"] == "agent_report"
    assert result["agentContextDetailAvailable"] is True
    assert result["contextUsed"] == 12
    assert result["contextLimit"] == 100


@pytest.mark.asyncio
async def test_context_blocks_use_public_transcript_only_when_agent_state_is_empty(
    tmp_path,
):
    service = _context_service(
        tmp_path,
        {
            "id": "chat_1",
            "messages": [{"role": "user", "content": "durable transcript"}],
        },
        {"messages": []},
    )

    result = await service.blocks("chat_1", "chat_1", legacy=False)

    assert result["compositionSource"] == "public_transcript"
    assert result["agentContextDetailAvailable"] is False
    assert result["messageTokens"] == 5
    assert result["layers"][-1]["id"] == "messages"


@pytest.mark.asyncio
async def test_live_inbox_snapshot_does_not_read_durable_chat_store():
    chats = _Chats(None)
    live = {
        "events": [{"status": "queued", "createdAt": "2026-01-01"}],
        "tools": [],
    }
    run = SimpleNamespace(
        run_id="run_1",
        status="running",
        inbox=SimpleNamespace(live_snapshot=lambda: live),
    )
    service = ConversationInboxQueryService(
        chats=chats,
        run_manager=SimpleNamespace(get=lambda _chat_id: run),
        utc_now=lambda: "2026-01-02",
    )

    result = await service.snapshot("chat_1")

    assert chats.read_calls == 0
    assert result["active"] is True
    assert result["counts"] == {
        "queued": 1,
        "claimed": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "total": 1,
    }
