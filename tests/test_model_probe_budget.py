import pytest

from cyrene.plugins.builtin.cyrene_model import probe


@pytest.mark.asyncio
async def test_probes_allow_reasoning_before_short_answer(monkeypatch):
    calls = []

    async def reasoning_model(*args, **kwargs):
        calls.append(kwargs)
        # A short visible answer can still require a larger reasoning budget.
        if kwargs["max_tokens"] < 512:
            raise ValueError("output_limit_without_tool_calls")
        return {"content": "OK"}

    monkeypatch.setattr(probe, "_complete", reasoning_model)
    service = probe.ModelProbeService()
    assert await service.test_connection("", "http://localhost/v1", "reasoning-model") == "OK"
    assert (await service.probe_vision("", "http://localhost/v1", "reasoning-model"))["vision_capable"]
    assert len(calls) == 2
    assert all(call["max_tokens"] <= 4096 for call in calls)
