import json

import pytest

from cyrene import settings_store
from cyrene.tooling import native_definitions
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
        for item in native_definitions.get_native_tool_defs()
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


def test_search_project_memories_uses_or_for_multiple_terms(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entries = [
        {
            "id": "mem_photo",
            "content": "用户本人照片可用于身份识别。",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "tags": ["用户"],
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
        },
    ]
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps(entries, ensure_ascii=False),
        encoding="utf-8",
    )

    results = memory.search_project_memories(
        "project-test",
        query="照片 人物 头像 识别",
    )

    assert [item["id"] for item in results] == ["mem_photo"]


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


def test_workbench_memory_payload_hides_internal_task_reports(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entries = [
        {
            "id": "mem_visible",
            "content": "用户偏好简洁回答。",
            "type": "preference",
            "category": "preference",
            "source": "conversation",
            "first_seen": "2026-06-23",
            "last_mentioned": "2026-06-24",
            "mention_count": 2,
        },
        {
            "id": "mem_report",
            "content": "任务：修复记忆页面\n验收：全部通过",
            "type": "task_report",
            "category": "task_report",
            "source": "agent",
            "tags": ["任务报告", "自动生成"],
            "first_seen": "2026-06-24",
            "last_mentioned": "2026-06-24",
            "mention_count": 1,
        },
    ]
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps(entries, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = memory._build_payload("project-test")

    assert [item["id"] for item in payload["memories"]] == ["mem_visible"]
    assert payload["overview"]["total"] == 1
    assert payload["overview"]["total_citations"] == 2
    assert sum(source["count"] for source in payload["sources"]) == 1
    assert all(category["id"] != "task_report" for category in payload["categories"])
    assert "修复记忆页面" in memory.render_task_reports_for_planning("project-test")


@pytest.mark.asyncio
async def test_search_project_memory_tool_uses_current_project(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl.memory import search_project_memory as tool

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
        "resolve_workbench_project_id_for_session",
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
async def test_list_memories_combines_short_term_and_current_project(
    monkeypatch, tmp_path
):
    from cyrene import short_term
    from cyrene.agent import state
    from cyrene.tool_impl.memory import list_memories as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    short_term.init_short_term(tmp_path)
    short_term.save_entries([{
        "content": "用户偏好简洁回答。",
        "type": "preference",
        "first_seen": "2026-06-19",
        "last_mentioned": "2026-06-20",
    }])
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_project",
            "content": "项目必须使用 PostgreSQL。",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyrene.workbench_context.resolve_workbench_project_id_for_session",
        lambda session_id: "project-test",
    )

    token = state._current_session_id.set("chat-test")
    try:
        result = await tool._tool_list_memories(
            {"scope": "all", "status": "all"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["total"] == 2
    assert payload["total_by_scope"] == {"short_term": 1, "project": 1}
    assert payload["project_memory_available"] is True
    assert {item["scope"] for item in payload["memories"]} == {
        "short_term",
        "project",
    }


@pytest.mark.asyncio
async def test_search_project_memory_allows_default_workbench_project(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl.memory import search_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-default.json").write_text(json.dumps([{
        "content": "默认项目使用 pytest。",
        "type": "fact",
        "category": "fact",
        "source": "conversation",
        "first_seen": "2026-06-21",
        "last_mentioned": "2026-06-21",
        "mention_count": 1,
    }], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project-default",
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
    assert workbench_context.resolve_workbench_project_id_for_session("task-default") == "project-default"
    assert workbench_context.resolve_workbench_project_id_for_session("chat-default") == "project-default"
    assert workbench_context.resolve_workbench_project_data_key_for_session("missing") is None
    assert workbench_context.resolve_workbench_project_id_for_session("missing") is None


def test_default_project_memory_does_not_alias_global_short_term(monkeypatch, tmp_path):
    from cyrene import short_term
    from webui import routes

    monkeypatch.setattr(memory, "STORE_DIR", tmp_path)
    monkeypatch.setattr(memory, "_STORE_DB_PATH", "")
    monkeypatch.setattr(memory, "_CONFIGURED_STORE_DIR", None)
    monkeypatch.setattr(short_term, "_SHORT_TERM_FILE", tmp_path / "short_term.json")
    monkeypatch.setattr(
        routes,
        "_read_workbench_store",
        lambda: {
            "projects": [{
                "id": "project-default",
                "dataKey": "default",
            }]
        },
    )
    monkeypatch.setattr(
        routes,
        "_workbench_find_project",
        lambda payload, project_id: next(
            (
                project
                for project in payload["projects"]
                if project["id"] == project_id
            ),
            None,
        ),
    )

    short_term.save_entries([{
        "content": "旧 UI 的全局记忆。",
        "type": "fact",
        "first_seen": "2026-06-20",
        "last_mentioned": "2026-06-20",
        "mention_count": 1,
    }])
    (tmp_path / "wb_memory_project-default.json").write_text(
        json.dumps([{
            "id": "mem_project",
            "content": "默认项目自己的记忆。",
            "type": "fact",
            "category": "fact",
            "source": "conversation",
            "first_seen": "2026-06-21",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
        }], ensure_ascii=False),
        encoding="utf-8",
    )

    assert memory._resolve_workspace_id("project-default") == "project-default"
    payload = memory._build_payload("project-default")
    assert payload["overview"]["total"] == 1
    assert payload["memories"][0]["content"] == "默认项目自己的记忆。"


def test_memory_tools_are_registered_with_distinct_contracts():
    from cyrene.tooling import catalog as tools

    defs = {
        item["function"]["name"]: item["function"]
        for item in tools.TOOL_DEFS
    }

    assert "ListMemories" in defs
    assert "RecallMemory" in defs
    assert "RecallConversation" in defs
    assert "retire_short_term_memory" in defs
    assert "search_project_memory" in defs
    assert "retire_project_memory" in defs
    assert defs["ListMemories"]["parameters"]["required"] == []
    assert "query" not in defs["ListMemories"]["parameters"]["properties"]
    assert "session_id" not in defs["RecallMemory"]["parameters"]["properties"]
    assert "session_id" in defs["RecallConversation"]["parameters"]["properties"]
    assert defs["retire_short_term_memory"]["parameters"]["required"] == ["memory_id"]
    assert defs["search_project_memory"]["parameters"]["required"] == ["query"]
    assert defs["retire_project_memory"]["parameters"]["required"] == ["memory_id"]
    assert tools.is_tool_allowed_for_actor("retire_short_term_memory", "main")
    assert not tools.is_tool_allowed_for_actor("retire_short_term_memory", "subagent")
    assert tools.is_tool_allowed_for_actor("retire_project_memory", "main")
    assert not tools.is_tool_allowed_for_actor("retire_project_memory", "subagent")


@pytest.mark.asyncio
async def test_retire_project_memory_tool_marks_exact_memory_stale(
    monkeypatch, tmp_path
):
    from cyrene.agent import state
    from cyrene.tool_impl.memory import retire_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_old",
            "content": "方案文档是 13 页。",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "tags": ["方案文档"],
            "first_seen": "2026-06-10",
            "last_mentioned": "2026-06-10",
            "mention_count": 1,
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project-test",
    )

    token = state._current_session_id.set("chat-test")
    try:
        result = await tool._tool_retire_project_memory(
            {"memory_id": "mem_old", "reason": "最新版文档为 10 页 v3"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["changed"] is True
    assert payload["stale"] is True

    stored = json.loads(
        (tmp_path / "wb_memory_project-test.json").read_text(encoding="utf-8")
    )
    assert stored[0]["stale"] is True
    assert stored[0]["retiredAt"]
    assert stored[0]["history"][-1]["action"] == "stale"
    assert stored[0]["history"][-1]["detail"] == "最新版文档为 10 页 v3"
    assert memory.search_project_memories(
        "project-test", query="方案文档"
    ) == []
    assert "方案文档是 13 页" not in memory.render_memory_for_injection(
        "project-test"
    )


@pytest.mark.asyncio
async def test_retire_project_memory_tool_is_idempotent(monkeypatch, tmp_path):
    from cyrene.agent import state
    from cyrene.tool_impl.memory import retire_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_old",
            "content": "旧结论。",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "first_seen": "2026-06-10",
            "last_mentioned": "2026-06-20",
            "mention_count": 1,
            "stale": True,
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project-test",
    )

    token = state._current_session_id.set("chat-test")
    try:
        result = await tool._tool_retire_project_memory(
            {"memory_id": "mem_old"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["changed"] is False
    assert payload["message"] == "Project memory was already retired."


@pytest.mark.asyncio
async def test_retire_project_memory_tool_supports_default_workbench_project(
    monkeypatch, tmp_path
):
    from cyrene.agent import state
    from cyrene.tool_impl.memory import retire_project_memory as tool

    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    path = tmp_path / "wb_memory_project-default.json"
    path.write_text(json.dumps([{
        "id": "mem_default_old",
        "content": "默认项目的旧配置。",
        "type": "fact",
        "category": "fact",
        "source": "conversation",
        "first_seen": "2026-06-10",
        "last_mentioned": "2026-06-20",
        "mention_count": 1,
    }], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        tool,
        "resolve_workbench_project_id_for_session",
        lambda session_id: "project-default",
    )

    token = state._current_session_id.set("task-default")
    try:
        result = await tool._tool_retire_project_memory(
            {"memory_id": "mem_default_old"},
            None,
            0,
            "",
            None,
        )
    finally:
        state._current_session_id.reset(token)

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["changed"] is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored[0]["stale"] is True
    assert memory.search_project_memories(
        "project-default", query="旧配置"
    ) == []


@pytest.mark.asyncio
async def test_agent_memory_records_citation_and_history_on_create(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")

    async def fake_call_llm(messages, **kwargs):
        return {"content": '{"content": "用户使用 pytest 编写测试。"}'}

    monkeypatch.setattr(agent_state, "_call_llm", fake_call_llm)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户使用 pytest 编写测试。",
        category="habit",
    )

    assert retired == []
    assert saved is not None
    assert saved["source"] == "agent"
    assert len(saved["citations"]) == 1
    assert saved["citations"][0]["source"] == "agent"
    assert saved["citations"][0]["snippet"]
    assert len(saved["history"]) == 1
    assert saved["history"][0]["action"] == "created"


@pytest.mark.asyncio
async def test_agent_memory_records_citation_and_history_on_reinforce(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_existing",
            "content": "用户使用 pytest 编写测试。",
            "type": "habit",
            "category": "habit",
            "source": "agent",
            "tags": [],
            "first_seen": "2026-06-10",
            "last_mentioned": "2026-06-10",
            "mention_count": 1,
        }], ensure_ascii=False),
        encoding="utf-8",
    )

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("reinforcement should not call the LLM")

    monkeypatch.setattr(agent_state, "_call_llm", unexpected_call)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户使用 pytest 编写测试。",
        category="habit",
    )

    assert retired == []
    assert saved is not None
    assert saved["id"] == "mem_existing"
    assert saved["citation_count"] == 2
    assert len(saved["citations"]) == 1  # one new citation from this reinforcement
    assert saved["citations"][0]["source"] == "agent"
    history_actions = [h["action"] for h in saved["history"]]
    assert "reinforced" in history_actions


@pytest.mark.asyncio
async def test_agent_memory_revives_stale_and_records_history(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_old",
            "content": "用户使用 SQLite 作为数据库。",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "tags": [],
            "first_seen": "2026-06-01",
            "last_mentioned": "2026-06-01",
            "mention_count": 1,
            "stale": True,
        }], ensure_ascii=False),
        encoding="utf-8",
    )

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("reinforcement of existing text should not call the LLM")

    monkeypatch.setattr(agent_state, "_call_llm", unexpected_call)

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户使用 SQLite 作为数据库。",
        category="fact",
    )

    assert retired == []
    assert saved is not None
    assert saved["stale"] is False
    history_actions = [h["action"] for h in saved["history"]]
    assert "reinforced" in history_actions
    assert "revived" in history_actions


def test_serialize_backfills_history_from_timestamps_for_legacy_entries(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entry = {
        "id": "mem_legacy",
        "content": "Legacy memory without history.",
        "type": "fact",
        "category": "fact",
        "source": "manual",
        "tags": [],
        "first_seen": "2026-06-01",
        "last_mentioned": "2026-06-15",
        "mention_count": 2,
    }
    serialized = memory._serialize(entry)
    actions = [h["action"] for h in serialized["history"]]
    assert "created" in actions
    assert "reinforced" in actions


def test_serialize_populates_citation_and_history_fields(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    entry = {
        "id": "mem_full",
        "content": "Full memory with events.",
        "type": "fact",
        "category": "fact",
        "source": "agent",
        "tags": ["test"],
        "first_seen": "2026-06-01",
        "last_mentioned": "2026-06-20",
        "mention_count": 3,
        "citations": [
            {"at": "2026-06-15", "source": "agent", "snippet": "first cite"},
            {"at": "2026-06-20", "source": "conversation", "snippet": "second cite"},
        ],
        "history": [
            {"at": "2026-06-01", "action": "created"},
            {"at": "2026-06-15", "action": "reinforced"},
            {"at": "2026-06-20", "action": "edited", "detail": "updated content"},
        ],
    }
    serialized = memory._serialize(entry)
    assert len(serialized["citations"]) == 2
    assert serialized["citations"][0]["source_label"]
    assert serialized["citations"][0]["snippet"] == "first cite"
    assert len(serialized["history"]) == 3
    assert serialized["history"][0]["action"] == "created"
    assert serialized["history"][0]["action_label"]
    assert serialized["history"][2]["detail"] == "updated content"


def test_search_project_memories_excludes_history_field(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    (tmp_path / "wb_memory_project-test.json").write_text(
        json.dumps([{
            "id": "mem_1",
            "content": "database config",
            "type": "fact",
            "category": "fact",
            "source": "agent",
            "tags": ["database"],
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
            "history": [{"at": "2026-06-20", "action": "created"}],
        }]),
        encoding="utf-8",
    )

    results = memory.search_project_memories("project-test", query="database", limit=5)

    assert len(results) == 1
    assert "history" not in results[0]
    assert "citations" not in results[0]
