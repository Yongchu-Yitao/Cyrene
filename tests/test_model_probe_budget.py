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


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["openai", "openai_responses", "anthropic", "gemini"])
async def test_settings_probe_allows_reasoning_before_reply(adapter):
    from types import SimpleNamespace
    from cyrene.plugins.builtin.cyrene_model.routes import _test_model

    async def call(name, arguments, context):
        assert arguments["max_tokens"] == 1024
        assert context.data["caller"] == "settings_model_test"
        return SimpleNamespace(success=True, value={"content": "OK"})

    service = SimpleNamespace(
        resolve=lambda *_: SimpleNamespace(name="TestProvider"),
        runtime=SimpleNamespace(call=call),
    )
    result = await _test_model(
        {"id": "test-provider", "adapter": adapter, "base_url": "https://provider.test/v1",
         "options": {"provider_preset": "test-provider"}},
        {"id": "flash", "connection_id": "test-provider", "model": "deepseek-v4.1-flash",
         "capabilities": ["chat"]},
        service=service,
    )
    assert result["connected"] is True
