from __future__ import annotations

import pytest


def test_public_run_context_binding_is_scoped_and_resettable():
    from cyrene.agent.context import bind_run_context, current_run_context

    before = current_run_context()
    binding = bind_run_context(
        agent_id="worker-1",
        caller="test",
        round_id="round-1",
        session_id="session-1",
        temporary_full_access=True,
        soul_enabled=False,
        workspace_enabled=False,
    )

    active = current_run_context()
    assert active.agent_id == "worker-1"
    assert active.caller == "test"
    assert active.round_id == "round-1"
    assert active.session_id == "session-1"
    assert active.temporary_full_access is True
    assert active.soul_context_enabled is False
    assert active.workspace_context_enabled is False

    binding.reset()
    binding.reset()
    assert current_run_context() == before


@pytest.mark.asyncio
async def test_public_model_service_scopes_caller_and_restores_it(monkeypatch):
    from cyrene.agent import state
    from cyrene.agent.context import current_caller
    from cyrene.agent.model_service import call_agent_model

    seen: list[str] = []

    async def fake_call(_messages, **_kwargs):
        seen.append(current_caller())
        return {"content": "ok"}

    monkeypatch.setattr(state, "_call_llm", fake_call)
    before = current_caller()

    result = await call_agent_model([], caller="public-test")

    assert result == {"content": "ok"}
    assert seen == ["public-test"]
    assert current_caller() == before


@pytest.mark.asyncio
async def test_public_streaming_model_service_scopes_caller(monkeypatch):
    from cyrene.agent import state
    from cyrene.agent.context import current_caller
    from cyrene.agent.model_service import stream_agent_model

    seen: list[str] = []

    async def fake_stream(_messages, **_kwargs):
        seen.append(current_caller())
        return {"content": "streamed"}

    monkeypatch.setattr(state, "_call_llm_stream", fake_stream)
    before = current_caller()

    result = await stream_agent_model([], caller="stream-test")

    assert result == {"content": "streamed"}
    assert seen == ["stream-test"]
    assert current_caller() == before


def test_public_final_reply_usage_is_consumed_once():
    from cyrene.agent.model_service import (
        set_final_reply_usage,
        take_final_reply_usage,
    )

    set_final_reply_usage({"total_tokens": 7})

    assert take_final_reply_usage() == {"total_tokens": 7}
    assert take_final_reply_usage() is None
