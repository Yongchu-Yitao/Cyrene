from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from cyrene.model_runtime.client import _normalized_candidate
from cyrene.model_runtime import client as model_client
from cyrene.model_runtime.codex_provider import (
    CODEX_BASE_URL,
    CODEX_PROVIDER,
    CodexAppServer,
    CodexTransportError,
    _codex_sdk_config,
    _normalized_effort,
    _provider_input,
    _provider_instructions,
)


def test_codex_sdk_uses_its_pinned_runtime_and_system_proxy() -> None:
    config = _codex_sdk_config()

    assert config.codex_bin is None
    assert config.config_overrides == ("features.respect_system_proxy=true",)
    assert _normalized_effort("LOW") == "low"
    assert _normalized_effort("MAX") == "max"


def test_codex_candidate_never_inherits_api_credentials() -> None:
    candidate = _normalized_candidate(
        {
            "id": "codex-primary",
            "model": "gpt-5.6-sol",
            "provider": CODEX_PROVIDER,
            "reasoning_effort": "high",
            "api_key": "must-not-survive",
            "base_url": "https://example.invalid",
        },
        active_model="fallback",
        active_base_url="https://api.example/v1",
        active_api_key="secret",
    )

    assert candidate["provider"] == CODEX_PROVIDER
    assert candidate["base_url"] == CODEX_BASE_URL
    assert candidate["api_key"] == ""
    assert candidate["reasoning_effort"] == "high"
    assert candidate["vision_capable"] is None
    assert candidate["endpoints"] == [CODEX_BASE_URL]


def test_codex_candidate_is_never_resolved_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_client, "get_models", lambda: [
        {
            "id": "custom-primary",
            "model": "deepseek-chat",
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
        },
        {
            "id": "codex-fallback",
            "model": "gpt-5.6-sol",
            "provider": CODEX_PROVIDER,
            "base_url": CODEX_BASE_URL,
        },
    ])

    candidates = model_client._resolve_llm_candidates()

    assert [candidate["provider"] for candidate in candidates] == [
        "openai_compatible"
    ]


@pytest.mark.asyncio
async def test_codex_quota_blocks_when_any_window_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def limits() -> dict:
        return {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 100},
                    "secondary": {"usedPercent": 12},
                }
            }
        }

    monkeypatch.setattr(provider, "rate_limits_cached", limits)
    assert await provider.quota_available() is False


@pytest.mark.asyncio
async def test_codex_quota_check_failure_does_not_disable_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def limits() -> dict:
        raise RuntimeError("usage endpoint unavailable")

    monkeypatch.setattr(provider, "rate_limits_cached", limits)

    assert await provider.quota_available() is True


@pytest.mark.asyncio
async def test_codex_quota_check_failure_uses_stale_exhausted_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    provider._limits_cache = (
        0,
        {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 100}}
            }
        },
    )

    async def limits() -> dict:
        raise RuntimeError("usage endpoint unavailable")

    monkeypatch.setattr(provider, "rate_limits", limits)

    assert await provider.quota_available() is False
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_codex_quota_returns_stale_value_before_background_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    provider._limits_cache = (
        time.monotonic() - 60,
        {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 25}}
            }
        },
    )
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh() -> dict:
        refresh_started.set()
        await release_refresh.wait()
        return {}

    monkeypatch.setattr(provider, "rate_limits", refresh)

    assert await provider.quota_available() is True
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert provider._limits_refresh_task is not None

    release_refresh.set()
    await provider._limits_refresh_task


@pytest.mark.asyncio
async def test_codex_snapshot_can_return_stale_limits_without_loading_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    cached_limits = {
        "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 25}}
        }
    }
    provider._limits_cache = (time.monotonic(), cached_limits)

    async def account() -> dict:
        return {
            "account": {
                "type": "chatgpt",
                "planType": "prolite",
            }
        }

    async def models() -> list[dict]:
        raise AssertionError("quota snapshot should not load models")

    async def rate_limits() -> dict:
        raise AssertionError("fresh cache should be returned immediately")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot(
        include_models=False,
        stale_limits=True,
    )

    assert snapshot["account"]["planType"] == "prolite"
    assert snapshot["models"] == []
    assert snapshot["limits"] == cached_limits


@pytest.mark.asyncio
async def test_codex_completion_routes_streams_per_thread_and_runs_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    thread_counter = 0
    turns_started = 0
    both_turns_started = asyncio.Event()
    notification_queues: dict[str, asyncio.Queue] = {}

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            nonlocal thread_counter
            thread_counter += 1
            return {"thread": {"id": f"thread-{thread_counter}"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            nonlocal turns_started
            turn_id = f"turn-{thread_id}"
            notification_queues[turn_id] = asyncio.Queue()
            turns_started += 1
            if turns_started == 2:
                both_turns_started.set()
            await asyncio.wait_for(both_turns_started.wait(), timeout=1)
            queue = notification_queues[turn_id]
            queue.put_nowait(SimpleNamespace(
                method="item/agentMessage/delta",
                payload={"delta": f"reply:{thread_id}"},
            ))
            queue.put_nowait(SimpleNamespace(
                method="turn/completed",
                payload={"turn": {"id": turn_id, "status": "completed"}},
            ))
            return {"turn": {"id": turn_id}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await notification_queues[turn_id].get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            notification_queues.pop(turn_id, None)

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    fake_client = FakeClient()

    async def ready_client() -> FakeClient:
        return fake_client

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    streams: list[list[dict]] = [[], []]

    async def run(index: int) -> dict:
        async def collect(event: dict) -> None:
            streams[index].append(event)

        return await provider.complete(
            messages=[{"role": "user", "content": f"request {index}"}],
            tools=None,
            model="gpt-5.6-sol",
            timeout=2,
            stream_callback=collect,
        )

    first, second = await asyncio.gather(run(0), run(1))

    assert {first["content"], second["content"]} == {
        "reply:thread-1",
        "reply:thread-2",
    }
    assert turns_started == 2
    for events in streams:
        assert events[0] == {"type": "reply_start"}
        assert set(events[1]) == {"type", "delta"}
        assert events[1]["type"] == "reply_delta"
        assert set(events[2]) == {"type", "response"}
        assert events[2]["type"] == "reply_done"
    assert notification_queues == {}


@pytest.mark.asyncio
async def test_codex_completion_forwards_reasoning_summary_and_low_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    seen_turn_params: dict = {}

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            seen_turn_params.update(params)
            queue.put_nowait(SimpleNamespace(
                method="item/reasoning/summaryTextDelta",
                payload={"delta": "Checked the request."},
            ))
            queue.put_nowait(SimpleNamespace(
                method="item/agentMessage/delta",
                payload={"delta": "OK"},
            ))
            queue.put_nowait(SimpleNamespace(
                method="turn/completed",
                payload={
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [{"type": "agentMessage", "text": "OK"}],
                    }
                },
            ))
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    events: list[dict] = []

    async def collect_stream(event: dict) -> None:
        events.append(event)

    response = await provider.complete(
        messages=[{"role": "user", "content": "Say OK"}],
        tools=None,
        model="gpt-5.6-sol",
        reasoning_effort="LOW",
        timeout=2,
        stream_callback=collect_stream,
    )

    assert seen_turn_params["effort"] == "low"
    assert seen_turn_params["summary"] == "auto"
    assert response["reasoning_content"] == "Checked the request."
    assert [event["type"] for event in events] == [
        "reply_start",
        "reasoning_start",
        "reasoning_delta",
        "reply_delta",
        "reasoning_done",
        "reply_done",
    ]


@pytest.mark.asyncio
async def test_codex_transport_retry_interrupts_and_falls_back_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    interrupted: list[tuple[str, str]] = []

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            queue.put_nowait(SimpleNamespace(
                method="error",
                payload={
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "willRetry": True,
                    "error": {
                        "message": "stream disconnected",
                        "codexErrorInfo": {
                            "responseStreamDisconnected": {
                                "httpStatusCode": None,
                            }
                        },
                    },
                },
            ))
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            interrupted.append((thread_id, turn_id))
            return {}

    fake_client = FakeClient()

    async def ready_client() -> FakeClient:
        return fake_client

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    transport_events: list[dict] = []

    async def collect_transport(event: dict) -> None:
        transport_events.append(event)

    with pytest.raises(CodexTransportError, match="stream disconnected"):
        await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            timeout=2,
            transport_callback=collect_transport,
        )

    assert interrupted == [("thread-1", "turn-1")]
    assert transport_events[-1]["status"] == "retrying"
    assert transport_events[-1]["error_kind"] == "responseStreamDisconnected"


@pytest.mark.asyncio
async def test_codex_without_an_upstream_signal_interrupts_before_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    never_notified = asyncio.Event()
    interrupted: list[tuple[str, str]] = []

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            await never_notified.wait()
            return SimpleNamespace(
                method="turn/completed",
                payload={"turn": {"id": turn_id, "status": "interrupted"}},
            )

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            interrupted.append((thread_id, turn_id))
            never_notified.set()
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    monkeypatch.setattr(
        "cyrene.model_runtime.codex_provider._first_signal_timeout",
        lambda _timeout: 0.01,
    )
    transport_events: list[dict] = []

    async def collect_transport(event: dict) -> None:
        transport_events.append(event)

    with pytest.raises(CodexTransportError, match="no upstream model signal"):
        await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            model="gpt-5.6-sol",
            timeout=20,
            transport_callback=collect_transport,
        )

    assert interrupted == [("thread-1", "turn-1")]
    assert transport_events[-1]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_codex_snapshot_survives_rate_limit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def account() -> dict:
        return {"account": {"type": "chatgpt", "email": "user@example.com"}}

    async def models() -> list[dict]:
        return [{"id": "gpt-5.6-sol"}]

    async def rate_limits() -> dict:
        raise RuntimeError("quota service unavailable")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot()

    assert snapshot["connected"] is True
    assert snapshot["models"] == [{"id": "gpt-5.6-sol"}]
    assert snapshot["limits"] == {}
    assert snapshot["errors"] == {"limits": "quota service unavailable"}


@pytest.mark.asyncio
async def test_codex_snapshot_can_skip_slow_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def account() -> dict:
        return {"account": {"type": "chatgpt", "email": "user@example.com"}}

    async def models() -> list[dict]:
        return [{"id": "gpt-5.6-sol"}]

    async def rate_limits() -> dict:
        raise AssertionError("rate limits should not be requested")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot(include_limits=False)

    assert snapshot["connected"] is True
    assert snapshot["models"] == [{"id": "gpt-5.6-sol"}]
    assert snapshot["limits"] == {}
    assert "errors" not in snapshot


def test_codex_provider_replays_conversation_without_system_duplication() -> None:
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]
    instructions = _provider_instructions(messages, None)
    replay = _provider_input(messages)

    assert "Be concise." in instructions
    assert '"role": "system"' not in replay
    assert '"role": "user"' in replay
