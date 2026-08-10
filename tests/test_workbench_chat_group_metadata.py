import json

import pytest

from cyrene.agent import model_service
from cyrene.workbench import chat


@pytest.mark.asyncio
async def test_group_metadata_generation_uses_requested_language(monkeypatch):
    captured = {}

    async def fake_call(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {
            "content": json.dumps(
                {"title": "浏览器操作", "summary": "集中整理网站打开与浏览结果。"},
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(model_service, "call_agent_model", fake_call)
    result = await chat.generate_chat_group_metadata(
        [
            {"title": "打开 B 站", "preview": "浏览器已打开首页"},
            {"title": "打开 Google", "preview": "搜索首页加载完成"},
        ],
        lang="zh",
    )

    assert result == {
        "title": "浏览器操作",
        "summary": "集中整理网站打开与浏览结果。",
        "lang": "zh",
    }
    assert "标题和摘要必须使用简体中文" in captured["messages"][0]["content"]
    assert "新对话组" in captured["messages"][0]["content"]
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}
    assert captured["kwargs"]["secondary"] is True


@pytest.mark.asyncio
async def test_group_metadata_generation_omits_locked_title(monkeypatch):
    async def fake_call(messages, **kwargs):
        return {"content": '{"title":"should be ignored","summary":"Browser tasks and results."}'}

    monkeypatch.setattr(model_service, "call_agent_model", fake_call)
    result = await chat.generate_chat_group_metadata(
        [
            {"title": "Open Bilibili", "preview": "Homepage opened"},
            {"title": "Open Google", "preview": "Search loaded"},
        ],
        lang="en",
        title_locked=True,
        current_title="My web chats",
    )

    assert result["title"] == ""
    assert result["summary"] == "Browser tasks and results."
    assert result["lang"] == "en"


@pytest.mark.asyncio
async def test_group_metadata_rejects_missing_generated_title(monkeypatch):
    async def fake_call(messages, **kwargs):
        return {"content": '{"title":"","summary":"Browser tasks."}'}

    monkeypatch.setattr(model_service, "call_agent_model", fake_call)
    with pytest.raises(RuntimeError, match="empty chat group title"):
        await chat.generate_chat_group_metadata(
            [
                {"title": "Open Bilibili", "preview": "Homepage opened"},
                {"title": "Open Google", "preview": "Search loaded"},
            ],
            lang="en",
        )
