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


def test_english_dominant_mixed_text_requires_chinese_normalization():
    assert not memory._content_matches_language("User prefers 中文 responses.", "zh")
    assert not memory._content_matches_language("The user prefers 中文 responses.", "zh")
    assert memory._content_matches_language("用户使用 React、Next.js 和 TypeScript。", "zh")


def test_search_project_memories_filters_and_excludes_stale(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entries = [
        {
            "id": "mem_new",
            "content": "项目使用 PostgreSQL 作为主数据库。",
            "type": "project",
            "category": "project",
            "source": "agent",
            "tags": ["database", "PostgreSQL"],
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 2,
        },
        {
            "id": "mem_old",
            "content": "项目曾经使用 SQLite。",
            "type": "project",
            "category": "project",
            "source": "agent",
            "tags": ["database"],
            "first_seen": "2026-06-10",
            "last_mentioned": "2026-06-10",
            "mention_count": 1,
            "stale": True,
        },
    ]
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps(entries, ensure_ascii=False),
        encoding="utf-8",
    )

    results = memory.search_project_memories(
        "project-test",
        query="database",
        category="project",
        source="agent",
    )

    assert [item["id"] for item in results] == ["mem_new"]


def test_search_project_memories_bounds_large_results(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entries = [
        {
            "id": f"mem_{index}",
            "content": "database " + ("x" * 10_000),
            "type": "project",
            "category": "project",
            "source": "agent",
            "tags": ["database"],
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
            "citations": [{"raw": "y" * 10_000}],
        }
        for index in range(20)
    ]
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps(entries),
        encoding="utf-8",
    )

    results = memory.search_project_memories(
        "project-test",
        query="database",
        limit=20,
    )
    encoded = json.dumps(results, ensure_ascii=False)

    assert len(encoded) < 8_000
    assert all(len(item["content"]) <= 801 for item in results)
    assert all(item["content_truncated"] is True for item in results)
    assert all("citations" not in item for item in results)


@pytest.mark.asyncio
async def test_search_project_memory_tool_uses_current_project(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl import search_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps(
            [{
                "id": "mem_1",
                "content": "用户偏好使用 pytest 编写回归测试。",
                "type": "preference",
                "category": "preference",
                "source": "agent",
                "tags": ["pytest"],
                "first_seen": "2026-06-21",
                "last_mentioned": "2026-06-21",
                "mention_count": 1,
            }],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_data_key_for_session",
        lambda session_id: "project-test",
    )
    token = state._current_session_id.set("chat-test")
    try:
        result = await tool._tool_search_project_memory(
            {"query": "pytest", "limit": 5},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["memories"][0]["content"] == "用户偏好使用 pytest 编写回归测试。"


@pytest.mark.asyncio
async def test_search_project_memory_allows_default_workbench_project(monkeypatch, tmp_path):
    from cyrene import short_term
    from cyrene.agent import state
    from cyrene.tool_impl import search_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    monkeypatch.setattr(short_term, "_SHORT_TERM_FILE", tmp_path / "short_term.json")
    short_term.save_entries([{
        "content": "默认项目使用 pytest。",
        "type": "fact",
        "first_seen": "2026-06-21",
        "last_mentioned": "2026-06-21",
        "mention_count": 1,
    }])
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_data_key_for_session",
        lambda session_id: "default",
    )
    token = state._current_session_id.set("task-in-default-project")
    try:
        result = await tool._tool_search_project_memory(
            {"query": "pytest"},
            None,
            0,
            "db.sqlite3",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["memories"][0]["content"] == "默认项目使用 pytest。"


def test_workbench_scope_resolver_distinguishes_default_project(monkeypatch, tmp_path):
    from cyrene import workbench_context

    projects_path = tmp_path / "workbench_projects.json"
    chats_path = tmp_path / "workbench_chats.json"
    projects_path.write_text(
        json.dumps({
            "projects": [{
                "id": "project-default",
                "dataKey": "default",
                "sessions": [{"id": "task-default"}],
            }]
        }),
        encoding="utf-8",
    )
    chats_path.write_text(
        json.dumps({
            "chats": [{
                "id": "chat-default",
                "projectId": "project-default",
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(workbench_context, "_WORKBENCH_STORE", projects_path)
    monkeypatch.setattr(workbench_context, "_WORKBENCH_CHATS_STORE", chats_path)

    assert workbench_context.resolve_workbench_project_data_key_for_session("task-default") == "default"
    assert workbench_context.resolve_workbench_project_data_key_for_session("chat-default") == "default"
    assert workbench_context.resolve_workbench_project_data_key_for_session("missing") is None


def test_memory_tools_are_registered_with_distinct_contracts():
    from cyrene import tools

    defs = {
        item["function"]["name"]: item["function"]
        for item in tools.TOOL_DEFS
    }

    assert "RecallMemory" in defs
    assert "RecallConversation" in defs
    assert "search_project_memory" in defs
    assert "session_id" not in defs["RecallMemory"]["parameters"]["properties"]
    assert "session_id" in defs["RecallConversation"]["parameters"]["properties"]
    assert defs["search_project_memory"]["parameters"]["required"] == ["query"]
