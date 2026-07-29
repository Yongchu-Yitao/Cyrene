from __future__ import annotations

import asyncio
import time

import pytest

from cyrene.model_runtime.client import _normalized_candidate
from cyrene.model_runtime import client as model_client
from cyrene.model_runtime.codex_provider import (
    CODEX_BASE_URL,
    CODEX_PROVIDER,
    CodexAppServer,
    _provider_input,
    _provider_instructions,
)


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
async def test_codex_completion_routes_streams_per_thread_and_runs_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    thread_counter = 0
    turns_started = 0
    both_turns_started = asyncio.Event()

    async def ensure_started() -> None:
        return None

    async def request_raw(
        method: str,
        params: dict,
        *,
        timeout: float = 30,
    ) -> dict:
        nonlocal thread_counter, turns_started
        if method == "thread/start":
            thread_counter += 1
            return {"thread": {"id": f"thread-{thread_counter}"}}
        if method != "turn/start":
            raise AssertionError(f"unexpected method: {method}")

        thread_id = str(params["threadId"])
        turn_id = f"turn-{thread_id}"
        turns_started += 1
        if turns_started == 2:
            both_turns_started.set()
        await asyncio.wait_for(both_turns_started.wait(), timeout=1)

        async def emit() -> None:
            await asyncio.sleep(0)
            provider._route_notification({
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "delta": f"reply:{thread_id}",
                },
            })
            provider._route_notification({
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "completed"},
                },
            })

        asyncio.create_task(emit())
        return {"turn": {"id": turn_id}}

    monkeypatch.setattr(provider, "_ensure_started", ensure_started)
    monkeypatch.setattr(provider, "_request_raw", request_raw)
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
    assert provider._notification_queues == {}


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
