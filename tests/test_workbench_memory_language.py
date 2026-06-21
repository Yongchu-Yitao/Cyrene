import json

import pytest

from cyrene import settings_store
from cyrene import tool_legacy
from cyrene.agent import state as agent_state
from webui import routes_workbench_memory as memory


def _isolate_memory_store(monkeypatch, tmp_path, language):
    monkeypatch.setattr(memory, "STORE_DIR", tmp_path)
    monkeypatch.setattr(memory, "_resolve_workspace_id", lambda workspace_id: str(workspace_id))
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default="": language if key == "app_language" else default,
    )


@pytest.mark.asyncio
async def test_agent_memory_is_translated_to_configured_chinese(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    calls = []

    async def fake_call_llm(messages, **kwargs):
        calls.append((messages, kwargs))
        return {
            "content": json.dumps(
                {"content": "用户是中国公民，正在申请澳大利亚旅游签证。"},
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(agent_state, "_call_llm", fake_call_llm)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "The user is a Chinese citizen applying for an Australian tourist visa.",
        category="fact",
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == "用户是中国公民，正在申请澳大利亚旅游签证。"
    assert saved["source"] == "agent"
    assert len(calls) == 1
    stored = json.loads((tmp_path / "wb_memory_project-test.json").read_text())
    assert stored[0]["content"] == saved["content"]


@pytest.mark.asyncio
async def test_agent_memory_is_translated_to_configured_english(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "en")

    async def fake_call_llm(messages, **kwargs):
        return {
            "content": json.dumps(
                {"content": "The user prefers concise, structured answers."}
            )
        }

    monkeypatch.setattr(agent_state, "_call_llm", fake_call_llm)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户偏好简洁、结构化的回答。",
        category="preference",
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == "The user prefers concise, structured answers."


@pytest.mark.asyncio
async def test_agent_memory_in_correct_language_skips_translation(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("language normalization should not call the LLM")

    monkeypatch.setattr(agent_state, "_call_llm", unexpected_call)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户偏好简洁、结构化的回答。",
        category="preference",
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == "用户偏好简洁、结构化的回答。"


@pytest.mark.asyncio
async def test_failed_translation_does_not_persist_wrong_language(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")

    async def fake_call_llm(messages, **kwargs):
        return {"content": '{"content":"Still written in English."}'}

    monkeypatch.setattr(agent_state, "_call_llm", fake_call_llm)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "The user prefers concise answers.",
        category="preference",
    )

    assert saved is None
    assert retired == []
    assert not (tmp_path / "wb_memory_project-test.json").exists()


def test_save_project_memory_tool_requires_user_language():
    content_description = next(
        item
        for item in tool_legacy.TOOL_DEFS
        if item["function"]["name"] == "save_project_memory"
    )["function"]["parameters"]["properties"]["content"]["description"]

    assert "MUST use the user's configured language" in content_description


def test_language_neutral_path_does_not_require_translation():
    assert memory._content_matches_language("src/app.py", "zh")
    assert memory._content_matches_language("MAX_RETRIES=3", "zh")
