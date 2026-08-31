import json

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.plugins.builtin.cyrene_memory.definitions import get_native_tool_def
from cyrene.platform import settings_store
from cyrene.plugins.builtin.cyrene_memory import structured as memory


class _MemoryGateway:
    def __init__(self, handler):
        self.handler = handler

    async def complete(self, messages, **kwargs):
        return await self.handler(messages, **kwargs)


def _isolate_memory_store(monkeypatch, tmp_path, language):
    def path(workspace_id):
        return tmp_path / f"wb_memory_{memory._resolve_workspace_id(workspace_id)}.json"

    def load(workspace_id):
        target = path(workspace_id)
        return json.loads(target.read_text(encoding="utf-8")) if target.exists() else []

    def save(workspace_id, entries, *, base_value=None):
        del base_value
        path(workspace_id).write_text(
            json.dumps(entries, ensure_ascii=False),
            encoding="utf-8",
        )

    monkeypatch.setattr(memory, "_load", load)
    monkeypatch.setattr(memory, "_save", save)
    monkeypatch.setattr(
        settings_store,
        "get",
        lambda key, default="": language if key == "app_language" else default,
    )


def test_sampled_memory_injection_keeps_top_five_and_samples_ten_from_tail(
    monkeypatch,
):
    entries = [
        {
            "id": f"mem-{index:02d}",
            "content": f"memory {index:02d}",
            "category": "fact",
            "mention_count": 20 - index,
            "last_mentioned": "2026-08-31",
        }
        for index in range(20)
    ]
    sampled = {}

    def choose(population, count):
        sampled["population"] = list(population)
        sampled["count"] = count
        return list(population[-count:])

    monkeypatch.setattr(memory.random, "sample", choose)

    selected = memory.sample_memory_injection_ids("project-test", entries=entries)
    rendered = memory.render_sampled_memory_for_injection(
        "project-test",
        entries=entries,
        max_chars=10_000,
        header="memories",
        language="en",
    )

    assert selected == [
        "mem-00",
        "mem-01",
        "mem-02",
        "mem-03",
        "mem-04",
        "mem-10",
        "mem-11",
        "mem-12",
        "mem-13",
        "mem-14",
        "mem-15",
        "mem-16",
        "mem-17",
        "mem-18",
        "mem-19",
    ]
    assert sampled == {
        "population": [f"mem-{index:02d}" for index in range(5, 20)],
        "count": 10,
    }
    assert all(f"memory {index:02d}" in rendered for index in range(5))
    assert all(f"memory {index:02d}" in rendered for index in range(10, 20))
    assert all(f"memory {index:02d}" not in rendered for index in range(5, 10))


@pytest.mark.asyncio
async def test_agent_memory_is_saved_verbatim_without_llm(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("agent memory save must not call the LLM")

    # Mixed technical/Chinese content (as an agent would write it) must be
    # stored exactly as-is — no language detection, translation, or rejection.
    content = (
        "cifar_challenge 参赛经验：最终成绩 Cyrene v1=0.7633。"
        "① train.py 的 safe_import 只兼容顶层导入（from torch import nn 可）；"
        "② MPS 上 AMP float16 无提速，弃用；"
        "③ w=32 ResNet(1.21M)+batch=256 最优，实测 14.3 iter/s；"
        "④ 运行用 /opt/miniconda3/envs/torch/bin/python train.py --candidate name.py"
    )
    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        content,
        category="fact",
        model_gateway=_MemoryGateway(unexpected_call),
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == content
    assert saved["source"] == "agent"
    stored = json.loads((tmp_path / "wb_memory_project-test.json").read_text())
    assert stored[0]["content"] == content


@pytest.mark.asyncio
async def test_agent_memory_keeps_original_when_translation_fails(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")

    async def failing_llm(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "The user prefers concise answers.",
        category="preference",
        model_gateway=_MemoryGateway(failing_llm),
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == "The user prefers concise answers."
    assert (tmp_path / "wb_memory_project-test.json").exists()


@pytest.mark.asyncio
async def test_agent_memory_is_translated_to_configured_language(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    calls = []

    async def fake_call_llm(messages, **kwargs):
        calls.append((messages, kwargs))
        return {
            "content": json.dumps(
                {"content": "用户偏好简洁、结构化的回答。"},
                ensure_ascii=False,
            )
        }

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "The user prefers concise, structured answers.",
        category="preference",
        model_gateway=_MemoryGateway(fake_call_llm),
    )

    assert retired == []
    assert saved is not None
    assert saved["content"] == "用户偏好简洁、结构化的回答。"
    assert len(calls) == 1
    assert calls[0][1]["route"] == "secondary"
    assert calls[0][1]["caller"] == "workbench_memory"
    assert calls[0][1]["tools"][0]["function"]["name"] == "submit_memory_result"
    assert calls[0][1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_memory_result"},
    }


def test_save_project_memory_tool_suggests_but_does_not_require_user_language():
    content_description = get_native_tool_def("save_project_memory")["function"][
        "parameters"
    ]["properties"]["content"]["description"]

    assert "user's configured language" in content_description
    assert "MUST" not in content_description


def test_verified_tool_evidence_includes_successful_current_results_only():
    messages = [
        {
            "id": "old",
            "role": "assistant",
            "tool_calls": [{
                "id": "old-call",
                "name": "StartShell",
                "arguments": {},
            }],
        },
        {"role": "tool", "tool_call_id": "old-call", "content": "old result"},
        {
            "id": "new",
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "ok-call",
                    "name": "RemoteCyreneStatus",
                    "arguments": {},
                },
                {
                    "id": "bad-call",
                    "name": "StartShell",
                    "arguments": {},
                },
                {
                    "id": "memory-call",
                    "name": "save_project_memory",
                    "arguments": {"content": "saved"},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "ok-call",
            "content": '{"status":"success","result":"16GB RAM"}',
        },
        {
            "role": "tool",
            "tool_call_id": "bad-call",
            "content": '{"status":"error","message":"failed"}',
        },
        {
            "role": "tool",
            "tool_call_id": "memory-call",
            "content": '{"status":"success","result":"saved"}',
        },
    ]

    evidence = memory.build_verified_tool_evidence(messages, {"old"})

    assert "RemoteCyreneStatus" in evidence
    assert "16GB RAM" in evidence
    assert "old result" not in evidence
    assert "failed" not in evidence
    assert "save_project_memory" not in evidence


@pytest.mark.asyncio
async def test_background_extractor_accepts_verified_tool_facts(monkeypatch, tmp_path):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    captured = {}

    async def fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {
            "content": "",
            "tool_calls": [{
                "id": "memory-result-1",
                "type": "function",
                "function": {
                    "name": "submit_memory_result",
                    "arguments": json.dumps({
                        "memories": [{
                            "content": "远程设备配备16GB内存。",
                            "category": "fact",
                            "confidence": "high",
                            "evidence": "Memory: 16GB",
                        }]
                    }, ensure_ascii=False),
                },
            }],
        }

    added = await memory.capture_from_exchange(
        "project-test",
        "看看硬件信息",
        "已查到机器配置。",
        verified_evidence=(
            '[tool:remote_tools verified result]\n'
            '{"status":"success","result":"Memory: 16GB"}'
        ),
        model_gateway=_MemoryGateway(fake_call_llm),
    )

    assert added == 1
    assert captured["messages"][0]["role"] == "system"
    assert "成功工具结果直接验证" in captured["messages"][0]["content"]
    assert "Memory: 16GB" in captured["messages"][1]["content"]
    assert captured["kwargs"]["route"] == "secondary"
    assert captured["kwargs"]["caller"] == "workbench_memory"
    assert captured["kwargs"]["tools"][0]["function"]["name"] == "submit_memory_result"
    assert captured["kwargs"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_memory_result"},
    }
    assert "response_format" not in captured["kwargs"]
    stored = json.loads(
        (tmp_path / "wb_memory_project-test.json").read_text(encoding="utf-8")
    )
    assert stored[0]["source"] == "conversation"
    assert stored[0]["content"] == "远程设备配备16GB内存。"


@pytest.mark.asyncio
async def test_retry_supersedes_and_relearns_structured_memory_by_turn_id(
    monkeypatch,
    tmp_path,
):
    _isolate_memory_store(monkeypatch, tmp_path, "en")

    async def extract(_user_text, _agent_text, **_kwargs):
        return [
            {
                "content": "You prefer the plugin boundary.",
                "category": "preference",
                "confidence": "high",
                "evidence": "plugin boundary",
            }
        ]

    monkeypatch.setattr(memory, "_extract_memories_llm", extract)
    await memory.capture_from_exchange(
        "project-retry",
        "Use the plugin boundary",
        "done",
        session_id="chat-1",
        turn_id="turn-1",
        run_id="run-1",
    )
    assert memory.supersede_conversation_turn(
        "project-retry",
        session_id="chat-1",
        turn_id="turn-1",
        replacement_run_id="run-2",
    ) == 1
    retired = memory._load("project-retry")[0]
    assert retired["stale"] is True
    assert retired["citations"] == []

    await memory.capture_from_exchange(
        "project-retry",
        "Use the plugin boundary",
        "corrected",
        session_id="chat-1",
        turn_id="turn-1",
        run_id="run-2",
    )
    relearned = memory._load("project-retry")[0]
    assert "stale" not in relearned
    assert relearned["mention_count"] == 1
    assert relearned["citations"][0]["turnId"] == "turn-1"
    assert relearned["citations"][0]["runId"] == "run-2"


@pytest.mark.asyncio
async def test_background_extractor_rejects_assistant_only_memory(
    monkeypatch,
    tmp_path,
):
    _isolate_memory_store(monkeypatch, tmp_path, "zh")
    captured = {}

    async def fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        return {
            "content": "",
            "tool_calls": [{
                "id": "memory-result-unsupported",
                "type": "function",
                "function": {
                    "name": "submit_memory_result",
                    "arguments": json.dumps({
                        "memories": [{
                            "content": "你长期维护多个工程项目。",
                            "category": "project",
                            "confidence": "high",
                            "evidence": "多个工程项目",
                        }]
                    }, ensure_ascii=False),
                },
            }],
        }

    added = await memory.capture_from_exchange(
        "project-test",
        "你能做什么",
        "我可以帮助你维护多个工程项目。",
        verified_evidence="",
        model_gateway=_MemoryGateway(fake_call_llm),
    )

    assert added == 0
    work_record = captured["messages"][1]["content"]
    assert '"assistant_summary": ""' in work_record
    assert not (tmp_path / "wb_memory_project-test.json").exists()


def test_language_neutral_path_does_not_require_translation():
    assert memory._content_matches_language("src/app.py", "zh")
    assert memory._content_matches_language("MAX_RETRIES=3", "zh")


def test_english_dominant_mixed_text_requires_chinese_normalization():
    assert not memory._content_matches_language("User prefers 中文 responses.", "zh")
    assert not memory._content_matches_language("The user prefers 中文 responses.", "zh")
    assert memory._content_matches_language("用户使用 React、Next.js 和 TypeScript。", "zh")


def test_technical_chinese_text_matches_without_translation():
    # Dense technical content written in Chinese passes as-is: code, paths,
    # and model names are language-neutral, not English prose.
    content = (
        "cifar_challenge 参赛经验：最终成绩 Cyrene v1=0.7633。"
        "① train.py 的 safe_import 只兼容顶层导入（from torch import nn 可）；"
        "② MPS 上 AMP float16 无提速，弃用；"
        "③ w=32 ResNet(1.21M)+batch=256 最优，实测 14.3 iter/s"
    )
    assert memory._content_matches_language(content, "zh")


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


def test_workbench_memory_payload_hides_internal_reflections(monkeypatch, tmp_path):
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
            "id": "mem_reflection",
            "content": "应避免重复使用已经失败的迁移路径。",
            "type": "reflection",
            "category": "reflection",
            "source": "agent",
            "tags": ["reflection", "dead_end"],
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
    assert all(category["id"] != "reflection" for category in payload["categories"])


@pytest.mark.asyncio
async def test_search_project_memory_tool_uses_current_project(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_memory import search_project_memory as tool

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
    result = await tool._tool_search_project_memory(
        {"query": "pytest", "limit": 5},
        PluginContext(data={"project_id": "project-test"}),
    )

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["search_mode"] == "keyword"
    assert payload["uses_embeddings"] is False
    assert payload["count"] == 1
    assert payload["memories"][0]["content"] == "用户偏好使用 pytest 编写回归测试。"


@pytest.mark.asyncio
async def test_list_memories_combines_short_term_and_current_project(
    monkeypatch, tmp_path
):
    from cyrene.plugins.builtin.cyrene_memory import short_term
    from cyrene.plugins.builtin.cyrene_memory import list_memories as tool

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
    result = await tool._tool_list_memories(
        {"scope": "all", "status": "all"},
        PluginContext(data={"project_id": "project-test"}),
    )

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
    from cyrene.plugins.builtin.cyrene_memory import search_project_memory as tool

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
    result = await tool._tool_search_project_memory(
        {"query": "pytest"},
        PluginContext(data={"project_id": "project-default"}),
    )

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["memories"][0]["content"] == "默认项目使用 pytest。"


def test_workbench_scope_resolver_distinguishes_default_project(monkeypatch, tmp_path):
    import sqlite3

    from cyrene.workbench.sessions import context as workbench_context
    from cyrene.workbench.persistence.store import ensure_schema

    db_path = tmp_path / "workbench.db"
    ensure_schema(db_path)
    projects = {
            "projects": [{
                "id": "project-default",
                "dataKey": "default",
            }]
        }
    chat = {"id": "chat-default", "projectId": "project-default"}
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO workbench_state(key, payload_json, updated_at) VALUES (?, ?, ?)",
            ("projects", json.dumps(projects), "2026-08-28T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO workbench_chats(chat_id, ordinal, payload_json, summary_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "chat-default",
                0,
                json.dumps(chat),
                "{}",
                "2026-08-28T00:00:00+00:00",
            ),
        )
    workbench_context.configure_store(str(db_path))

    assert workbench_context.resolve_workbench_project_data_key_for_session("chat-default") == "default"
    assert workbench_context.resolve_workbench_project_id_for_session("chat-default") == "project-default"
    assert workbench_context.resolve_workbench_project_data_key_for_session("missing") is None
    assert workbench_context.resolve_workbench_project_id_for_session("missing") is None


def test_memory_tools_are_registered_with_distinct_contracts():
    from cyrene.plugins.builtin.cyrene_memory import plugin_pack

    plugins = {plugin.name: plugin for plugin in plugin_pack.plugins}

    assert {
        "ListMemories",
        "RecallMemory",
        "RecallConversation",
        "retire_short_term_memory",
        "search_project_memory",
        "retire_project_memory",
    } <= set(plugins)
    assert plugins["ListMemories"].input_schema["required"] == []
    assert "query" not in plugins["ListMemories"].input_schema["properties"]
    assert "session_id" not in plugins["RecallMemory"].input_schema["properties"]
    assert "session_id" in plugins["RecallConversation"].input_schema["properties"]
    assert plugins["retire_short_term_memory"].input_schema["required"] == [
        "memory_id"
    ]
    assert plugins["search_project_memory"].input_schema["required"] == ["query"]
    assert "keyword" in plugins["search_project_memory"].description.lower()
    assert "does not use embeddings" in plugins[
        "search_project_memory"
    ].description.lower()
    assert plugins["retire_project_memory"].input_schema["required"] == [
        "memory_id"
    ]
    assert plugins["retire_short_term_memory"].metadata["main_only"] is True
    assert plugins["retire_project_memory"].metadata["main_only"] is True


@pytest.mark.asyncio
async def test_retire_project_memory_tool_marks_exact_memory_stale(
    monkeypatch, tmp_path
):
    from cyrene.plugins.builtin.cyrene_memory import retire_project_memory as tool

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
    result = await tool._tool_retire_project_memory(
        {"memory_id": "mem_old", "reason": "最新版文档为 10 页 v3"},
        PluginContext(data={"project_id": "project-test"}),
    )

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
    from cyrene.plugins.builtin.cyrene_memory import retire_project_memory as tool

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
    result = await tool._tool_retire_project_memory(
        {"memory_id": "mem_old"},
        PluginContext(data={"project_id": "project-test"}),
    )

    payload = json.loads(result)
    assert payload["status"] == "success"
    assert payload["changed"] is False
    assert payload["message"] == "项目记忆已经处于过时状态。"


@pytest.mark.asyncio
async def test_retire_project_memory_tool_supports_default_workbench_project(
    monkeypatch, tmp_path
):
    from cyrene.plugins.builtin.cyrene_memory import retire_project_memory as tool

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
    result = await tool._tool_retire_project_memory(
        {"memory_id": "mem_default_old"},
        PluginContext(data={"project_id": "project-default"}),
    )

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

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户使用 pytest 编写测试。",
        category="habit",
        model_gateway=_MemoryGateway(unexpected_call),
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

    saved, retired = await memory.add_agent_memory_checked(
        "project-test",
        "用户使用 SQLite 作为数据库。",
        category="fact",
        model_gateway=_MemoryGateway(unexpected_call),
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
    assert serialized["citations"][0]["source_label"] == "agent"
    assert serialized["citations"][0]["snippet"] == "first cite"
    assert len(serialized["history"]) == 3
    assert serialized["history"][0]["action"] == "created"
    assert serialized["history"][0]["action_label"] == "created"
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
