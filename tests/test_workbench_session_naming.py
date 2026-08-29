
import pytest


@pytest.mark.asyncio
async def test_generate_session_title_uses_exact_candidate_without_truncation(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cyrene.core import plugin as plugin_runtime
    from cyrene.workbench.sessions.session_naming import generate_session_title

    captured = {}

    async def fake_call(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"content": "  修复登录超时问题。  "}

    gateway = SimpleNamespace(complete=AsyncMock(side_effect=fake_call))
    monkeypatch.setattr(plugin_runtime, "application_plugin_service", lambda _name: gateway)

    message = "请帮我排查登录接口偶发超时" * 300
    candidate = {"id": "chosen", "model": "chosen-model"}
    title = await generate_session_title(message, limit=60, candidate=candidate)

    assert title == "修复登录超时问题"
    assert captured["messages"][-1]["content"] == message
    assert captured["kwargs"]["model_identity"] == candidate
    assert captured["kwargs"]["route"] == "secondary"
    assert captured["kwargs"]["caller"] == "workbench_session_namer"
