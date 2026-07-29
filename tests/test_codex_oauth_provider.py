from __future__ import annotations

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

    monkeypatch.setattr(provider, "rate_limits_cached", limits)

    assert await provider.quota_available() is False


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
