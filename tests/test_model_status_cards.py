from __future__ import annotations

import time

import httpx
import pytest

from cyrene.core.plugin import PluginContext
from cyrene.model import protocol_adapters
from cyrene.model.status import persist_model_status, publish_context_model_status
from cyrene.plugins.builtin.cyrene_model import _shared
from cyrene.plugins.builtin.cyrene_model._shared import ModelProvider
from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.chat.chat_runs import ChatRun, get_chat_run_manager


@pytest.mark.asyncio
async def test_model_status_seam_persists_one_card_and_promotes_fallback(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "model-status.sqlite3")
    manager = get_chat_run_manager()
    manager.configure(db_path)
    repository = ChatRepository(db_path)
    repository.write({"chats": [{
        "id": "chat-model-status",
        "projectId": "project-one",
        "messages": [],
        "status": "running",
    }]})

    await persist_model_status(
        "chat-model-status",
        "round-one",
        status="retry",
        model="primary-model",
        retry_count=1,
        retry_limit=4,
    )
    await persist_model_status(
        "chat-model-status",
        "round-one",
        status="recovered",
        model="primary-model",
    )
    await persist_model_status(
        "chat-model-status",
        "round-one",
        status="switching",
        model="fallback-model",
    )
    await persist_model_status(
        "chat-model-status",
        "round-one",
        status="switched",
        model="fallback-model",
    )

    chat = repository.get("chat-model-status")
    cards = [
        message
        for message in chat["messages"]
        if message.get("modelStatusCard") is True
    ]
    assert len(cards) == 1
    assert cards[0]["roundId"] == "round-one"
    assert cards[0]["modelStatus"] == {
        "status": "switched",
        "model": "fallback-model",
    }
    assert chat["lastModel"] == "fallback-model"


@pytest.mark.asyncio
async def test_transient_provider_retry_publishes_retry_card_status(
    monkeypatch,
) -> None:
    attempts = 0
    published: list[dict[str, object]] = []

    async def fake_handle_stream(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary disconnect")
        return {
            "role": "assistant",
            "content": "recovered",
            "model": "primary-model",
        }

    async def capture_status(chat_id, round_id, **status):
        published.append({"chat_id": chat_id, "round_id": round_id, **status})

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(protocol_adapters, "handle_stream", fake_handle_stream)
    monkeypatch.setattr("cyrene.model.status.persist_model_status", capture_status)
    monkeypatch.setattr(_shared.asyncio, "sleep", no_sleep)

    result = await _shared._complete_stream_endpoint(
        adapter="openai",
        client=None,
        endpoint="https://provider.example/v1/chat/completions",
        request=object(),
        stream_callback=None,
        provider=ModelProvider(
            id="test-provider",
            name="Test Provider",
            plugin_name="TestProvider",
            adapter="openai",
            default_base_url="https://provider.example/v1",
        ),
        context=PluginContext(data={
            "session_id": "chat-retry",
            "run_id": "round-retry",
        }),
        model="primary-model",
        started=time.perf_counter(),
        has_fallback=True,
    )

    assert result["content"] == "recovered"
    assert published == [
        {
            "chat_id": "chat-retry",
            "round_id": "round-retry",
            "status": "retry",
            "model": "primary-model",
            "retry_count": 1,
            "retry_limit": 5,
        },
        {
            "chat_id": "chat-retry",
            "round_id": "round-retry",
            "status": "recovered",
            "model": "primary-model",
            "retry_count": 0,
            "retry_limit": 0,
        },
    ]


@pytest.mark.asyncio
async def test_model_connect_retry_waits_ten_seconds_and_stops_after_five(
    monkeypatch,
) -> None:
    attempts = 0
    delays: list[float] = []
    published: list[dict[str, object]] = []

    async def always_disconnected(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("still disconnected")

    async def capture_status(chat_id, round_id, **status):
        published.append({"chat_id": chat_id, "round_id": round_id, **status})

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(protocol_adapters, "handle_stream", always_disconnected)
    monkeypatch.setattr("cyrene.model.status.persist_model_status", capture_status)
    monkeypatch.setattr(_shared.asyncio, "sleep", capture_sleep)

    with pytest.raises(httpx.ConnectError):
        await _shared._complete_stream_endpoint(
            adapter="openai",
            client=None,
            endpoint="https://provider.example/v1/chat/completions",
            request=object(),
            stream_callback=None,
            provider=ModelProvider(
                id="test-provider",
                name="Test Provider",
                plugin_name="TestProvider",
                adapter="openai",
                default_base_url="https://provider.example/v1",
            ),
            context=PluginContext(data={
                "session_id": "chat-retry-limit",
                "run_id": "round-retry-limit",
            }),
            model="primary-model",
            started=time.perf_counter(),
            has_fallback=False,
        )

    assert attempts == 6
    assert delays == [10.0] * 5
    assert [item["retry_count"] for item in published] == [1, 2, 3, 4, 5]
    assert {item["retry_limit"] for item in published} == {5}


@pytest.mark.asyncio
async def test_failed_model_status_does_not_promote_last_model(tmp_path) -> None:
    db_path = str(tmp_path / "model-status-failed.sqlite3")
    manager = get_chat_run_manager()
    manager.configure(db_path)
    repository = ChatRepository(db_path)
    repository.write({"chats": [{
        "id": "chat-model-failed",
        "projectId": "project-one",
        "messages": [],
        "status": "running",
        "lastModel": "previous-model",
    }]})

    await persist_model_status(
        "chat-model-failed",
        "round-failed",
        status="failed",
        model="unavailable-model",
    )

    chat = repository.get("chat-model-failed")
    assert chat["lastModel"] == "previous-model"
    assert chat["messages"][0]["modelStatus"] == {
        "status": "failed",
        "model": "unavailable-model",
    }


@pytest.mark.asyncio
async def test_model_status_is_published_live_as_an_intermediate_message(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "model-status-live.sqlite3")
    manager = get_chat_run_manager()
    manager.configure(db_path)
    repository = ChatRepository(db_path)
    repository.write({"chats": [{
        "id": "chat-model-live",
        "projectId": "project-one",
        "messages": [],
        "status": "running",
    }]})
    run = ChatRun(
        "chat-model-live",
        {"type": "ack"},
        persist_live_message=manager._persist_live_public_message,
    )
    await publish_context_model_status(
        PluginContext(data={
            "session_id": run.chat_id,
            "run_id": "round-live",
            "run_context": {"runtime_event_writer": run.publish},
        }),
        status="retry",
        model="primary-model",
        retry_count=1,
        retry_limit=5,
    )

    event = run.events[-1]
    assert event["type"] == "intermediate_message"
    assert event["message"]["modelStatus"] == {
        "status": "retry",
        "model": "primary-model",
        "retryCount": 1,
        "retryLimit": 5,
    }
    assert repository.get(run.chat_id)["messages"][0]["modelStatusCard"] is True


@pytest.mark.asyncio
async def test_reasoning_only_stream_is_reset_before_endpoint_fallback(
    monkeypatch,
) -> None:
    streamed: list[dict[str, object]] = []

    async def fake_handle_stream(
        _adapter,
        _client,
        _endpoint,
        _request,
        stream_callback,
        _timing,
    ):
        await stream_callback({"type": "reasoning_delta", "delta": "partial"})
        raise httpx.ConnectError("endpoint disconnected")

    async def capture_stream(event):
        streamed.append(dict(event))

    monkeypatch.setattr(protocol_adapters, "handle_stream", fake_handle_stream)

    with pytest.raises(httpx.ConnectError):
        await _shared._complete_stream_endpoint(
            adapter="openai",
            client=None,
            endpoint="https://provider.example/v1/chat/completions",
            request=object(),
            stream_callback=capture_stream,
            provider=ModelProvider(
                id="test-provider",
                name="Test Provider",
                plugin_name="TestProvider",
                adapter="openai",
                default_base_url="https://provider.example/v1",
            ),
            context=PluginContext(data={}),
            model="primary-model",
            started=time.perf_counter(),
            has_fallback=True,
        )

    assert streamed == [
        {"type": "reasoning_delta", "delta": "partial"},
        {"type": "reply_start", "reset": True},
    ]
