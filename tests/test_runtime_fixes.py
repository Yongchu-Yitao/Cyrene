import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent.plugin import PluginContext


def test_core_soul_projection_is_empty_when_plugin_is_unavailable(monkeypatch):
    from cyrene.workbench import presentation_runtime

    monkeypatch.setattr(
        presentation_runtime,
        "active_plugin_service",
        lambda _name: None,
    )

    assert presentation_runtime._soul_presentation() == {
        "path": "",
        "content": "",
        "updated_at": "",
        "recent_items": [],
        "section_count": 0,
    }


def _patch_memory_archive(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import archive
    from agent.plugin.plugin_impl.cyrene_memory.application import MemoryApplication
    from cyrene.workbench import presentation_runtime

    conversations = tmp_path / "conversations"
    conversations.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(archive, "CONVERSATIONS_DIR", conversations)
    application = MemoryApplication("", object(), object(), tmp_path / "data")
    monkeypatch.setattr(
        presentation_runtime,
        "_memory_service",
        lambda: application,
    )
    return conversations, application


def _patch_active_memory_service(monkeypatch, service, *module_aliases):
    from agent import plugin as plugin_runtime

    def resolve(name):
        return service if name == "memory" else None

    monkeypatch.setattr(plugin_runtime, "active_plugin_service", resolve)
    for module in module_aliases:
        monkeypatch.setattr(module, "active_plugin_service", resolve)


def test_get_memory_context_includes_short_term_by_default(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_memory import application as memory_application
    from agent.plugin.plugin_impl.cyrene_memory import short_term

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-19",
            "mention_count": 2,
            "emotional_valence": 0,
        }
    ])
    monkeypatch.setattr(
        memory_application,
        "_soul_application",
        lambda: SimpleNamespace(
            persona_context=lambda: "## SELF:IDENTITY\n- test memory"
        ),
    )
    application = memory_application.MemoryApplication("", object(), object(), tmp_path)
    context = application.memory_context()

    assert "SELF:IDENTITY" in context
    assert "Short-term cross-session memory" in context
    assert "user prefers concise replies" in context


def test_get_memory_context_can_skip_short_term(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_memory import application as memory_application
    from agent.plugin.plugin_impl.cyrene_memory import short_term

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user likes jasmine tea",
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-19",
            "mention_count": 1,
            "emotional_valence": 0,
        }
    ])
    monkeypatch.setattr(
        memory_application,
        "_soul_application",
        lambda: SimpleNamespace(
            persona_context=lambda: "## SELF:BELIEFS\n- test belief"
        ),
    )
    application = memory_application.MemoryApplication("", object(), object(), tmp_path)
    context = application.memory_context(include_short_term=False)

    assert "SELF:BELIEFS" in context
    assert "Short-term cross-session memory" not in context
    assert "user likes jasmine tea" not in context


def test_legacy_memory_modules_are_not_source_backends():
    import importlib.util
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[1] / "src"
    deleted = (
        "cyrene/memory.py",
        "cyrene/runtime/memory/__init__.py",
        "cyrene/runtime/memory/archive_format.py",
        "cyrene/runtime/memory/conversations.py",
        "cyrene/runtime/memory/short_term.py",
        "cyrene/runtime/memory/soul.py",
        "cyrene/workbench/memory.py",
        "cyrene/workbench/project_memory_prompt.py",
        "cyrene/tool_impl/memory/__init__.py",
        "route/memory.py",
        "route/workbench/memory.py",
        "route/workbench/project_memory.py",
    )
    for relative in deleted:
        assert not (source_root / relative).exists(), relative
    for relative in (
        "cyrene/runtime/memory",
        "cyrene/tool_impl/memory",
    ):
        assert not (source_root / relative).exists(), relative
    for module_name in (
        "cyrene.memory",
        "cyrene.runtime.memory",
        "cyrene.workbench.memory",
        "cyrene.workbench.project_memory_prompt",
        "cyrene.tool_impl.memory",
        "route.memory",
        "route.workbench.memory",
        "route.workbench.project_memory",
    ):
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        assert spec is None, module_name


def test_filesystem_tools_offload_blocking_io_and_bound_scans():
    from pathlib import Path

    source_root = Path(__file__).resolve().parent.parent / "src" / "agent" / "plugin"
    read_source = (source_root / "core_impl" / "read.py").read_text(encoding="utf-8")
    write_source = (source_root / "core_impl" / "write.py").read_text(encoding="utf-8")
    edit_source = (source_root / "plugin_impl" / "edit.py").read_text(encoding="utf-8")
    glob_source = (source_root / "plugin_impl" / "glob.py").read_text(encoding="utf-8")
    grep_source = (source_root / "plugin_impl" / "grep.py").read_text(encoding="utf-8")

    assert "await asyncio.to_thread(path.read_text" in read_source
    assert "await asyncio.to_thread(write_file)" in write_source
    assert "await asyncio.to_thread(" in edit_source
    assert "await asyncio.to_thread(_scan" in glob_source
    assert "await asyncio.to_thread(" in grep_source
    assert "_MAX_CANDIDATES" in glob_source
    assert "_MAX_CANDIDATES" in grep_source
    assert "_MAX_FILE_BYTES" in grep_source


async def test_recall_memory_tool_returns_recent_short_term_entries(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
            "emotional_valence": 0,
        },
        {
            "content": "user prefers detailed reports",
            "type": "preference",
            "first_seen": "2026-05-17",
            "last_mentioned": "2026-05-19",
            "mention_count": 2,
            "emotional_valence": 0,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])

    result = await tools._tool_recall_memory(
        {"query": "prefers", "type": "preference", "limit": 2},
        PluginContext(),
    )
    payload = json.loads(result)

    assert [item["content"] for item in payload["memories"]] == [
        "user prefers concise replies",
        "user prefers detailed reports",
    ]
    assert all(item["memory_id"].startswith("stm_") for item in payload["memories"])
    assert "matches" not in payload
    assert "soul_memory" not in payload


async def test_list_memories_reports_total_and_supports_filters_and_pagination(
    tmp_path,
):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import list_memories as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user prefers concise replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 2,
        },
        {
            "content": "superseded preference",
            "type": "preference",
            "first_seen": "2026-05-15",
            "last_mentioned": "2026-05-19",
            "mention_count": 1,
            "stale": True,
            "retired_at": "2026-05-22T10:00:00+08:00",
            "retire_reason": "corrected",
        },
    ])

    result = await tools._tool_list_memories(
        {"status": "all", "type": "preference", "limit": 1, "offset": 1},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["total"] == 2
    assert payload["returned"] == 1
    assert payload["has_more"] is False
    assert payload["memories"][0]["status"] == "retired"
    assert payload["memories"][0]["retire_reason"] == "corrected"


async def test_list_memories_defaults_to_all_active_memories(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import list_memories as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": f"memory {index}",
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": f"2026-05-{index + 1:02d}",
        }
        for index in range(3)
    ] + [{
        "content": "retired memory",
        "type": "fact",
        "first_seen": "2026-05-01",
        "last_mentioned": "2026-05-01",
        "stale": True,
    }])

    result = await tools._tool_list_memories(
        {},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["status"] == "active"
    assert payload["total"] == 3
    assert payload["total_by_scope"] == {"short_term": 3, "project": 0}
    assert payload["returned"] == 3
    assert payload["has_more"] is False


async def test_retire_short_term_memory_tool_marks_entry_stale(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import recall_memory
    from agent.plugin.plugin_impl.cyrene_memory import retire_short_term_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "user incorrectly prefers verbose replies",
            "type": "preference",
            "first_seen": "2026-05-18",
            "last_mentioned": "2026-05-20",
            "mention_count": 1,
            "emotional_valence": 0,
        },
        {
            "content": "user uses macOS",
            "type": "fact",
            "first_seen": "2026-05-16",
            "last_mentioned": "2026-05-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])
    memory_id = short_term.entry_id(short_term.load_entries()[0])

    result = await tools._tool_retire_short_term_memory(
        {"memory_id": memory_id, "reason": "user corrected this"},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["status"] == "success"
    assert payload["changed"] is True
    entries = short_term.load_entries()
    assert entries[0]["id"] == memory_id
    assert entries[0]["stale"] is True
    assert entries[0]["retire_reason"] == "user corrected this"
    assert "user incorrectly prefers verbose replies" not in short_term.get_context()

    recall_result = await recall_memory._tool_recall_memory(
        {"query": "verbose", "limit": 10},
        PluginContext(),
    )
    recall_payload = json.loads(recall_result)
    assert recall_payload["available_matches"] == 0


async def test_recall_memory_tool_uses_or_for_multiple_terms(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": "用户本人照片可用于身份识别",
            "type": "fact",
            "first_seen": "2026-06-20",
            "last_mentioned": "2026-06-21",
            "mention_count": 1,
            "emotional_valence": 0,
        },
    ])

    result = await tools._tool_recall_memory(
        {"query": "照片 人物 头像 识别", "limit": 10},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["available_matches"] == 1
    assert payload["memories"][0]["content"] == "用户本人照片可用于身份识别"


async def test_recall_memory_tool_bounds_large_results(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import short_term
    from agent.plugin.plugin_impl.cyrene_memory import recall_memory as tools

    short_term.init_short_term(tmp_path)
    short_term.save_entries([
        {
            "content": f"memory-{index}-" + ("x" * 10_000),
            "type": "fact",
            "first_seen": "2026-05-18",
            "last_mentioned": f"2026-05-{20 - index:02d}",
            "mention_count": 1,
            "emotional_valence": 0,
        }
        for index in range(20)
    ])

    result = await tools._tool_recall_memory(
        {"limit": 20},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["truncated"] is True
    assert len(result) < 10_000
    assert all(len(item["content"]) <= 801 for item in payload["memories"])
    assert all(item["content_truncated"] is True for item in payload["memories"])


async def test_recall_conversation_tool_returns_archived_matches(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_memory import archive as conversations
    from agent.plugin.plugin_impl.cyrene_memory import recall_conversation as tools

    conversations_dir = tmp_path / "conversations"
    conversations_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(conversations, "CONVERSATIONS_DIR", conversations_dir)

    (conversations_dir / "2026-05-19.md").write_text(
        "# Conversations - 2026-05-19\n\n"
        "<!-- session_title: 第一场 -->\n\n"
        "## 09:00:00 UTC\n\n"
        "<!-- archive_session_id: session_alpha -->\n"
        "<!-- session_title: 第一场 -->\n"
        "<!-- round_id: round_1 -->\n"
        "<!-- round_title: 设计角色 -->\n\n"
        "**User**: 先聊角色设定\n\n"
        "**Ape**: 角色偏冷静理性。\n\n"
        "---\n\n"
        "## 10:00:00 UTC\n\n"
        "<!-- archive_session_id: session_beta -->\n"
        "<!-- session_title: 第二场 -->\n"
        "<!-- round_id: round_2 -->\n"
        "<!-- round_title: 偏好总结 -->\n\n"
        "**User**: 记住我偏好简洁回答\n\n"
        "**Ape**: 已记录你偏好简洁回答。\n\n"
        "---\n",
        encoding="utf-8",
    )

    result = await tools._tool_recall_conversation(
        {"session_id": "archive_2026-05-19_session_beta", "limit": 2},
        PluginContext(),
    )
    payload = json.loads(result)

    assert payload["matches"][0]["archive_session_id"] == "session_beta"
    assert payload["matches"][0]["session_title"] == "第二场"
    assert payload["matches"][0]["assistant"] == "已记录你偏好简洁回答。"
    assert "memories" not in payload


async def test_recall_conversation_tool_searches_active_workbench_workspace(tmp_path):
    from agent.plugin.plugin_impl.cyrene_memory import archive as conversations
    from agent.plugin.plugin_impl.cyrene_memory import recall_conversation as tools

    workspace = tmp_path / "project"
    other_workspace = tmp_path / "other"
    conversations.archive_session_exchange(
        "wbchat_alpha",
        "我们讨论 photo identification skill 的安装",
        "已安装全局 skill。",
        workspace_dir=workspace,
        session_title="技能安装",
    )
    conversations.archive_session_exchange(
        "wbchat_beta",
        "photo identification skill 后续清理",
        "需要检查实体和项目记忆。",
        workspace_dir=workspace,
        session_title="清理讨论",
    )
    conversations.archive_session_exchange(
        "wbchat_other",
        "photo identification skill 在另一个 workspace",
        "不应被当前 workspace 搜到。",
        workspace_dir=other_workspace,
        session_title="其他项目",
    )

    result = await tools._tool_recall_conversation(
        {"query": "photo identification", "limit": 10},
        PluginContext(workspace=workspace),
    )

    payload = json.loads(result)
    assert payload["scope"] == "workbench_workspace"
    assert {item["session_id"] for item in payload["matches"]} == {
        "wbchat_alpha",
        "wbchat_beta",
    }
    assert all(item["source"] == "workbench_workspace" for item in payload["matches"])
    assert all(str(workspace) in item["source_file"] for item in payload["matches"])






async def test_heartbeat_proactive_check_uses_main_agent_loop(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    seen = {}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_run_plugin_proactive_turn(
        prompt, *, bot, owner_id, db_path, **_kwargs
    ):
        seen["prompt"] = prompt
        seen["chat_id"] = owner_id
        seen["db_path"] = db_path
        return SimpleNamespace(
            text="user-facing proactive message",
            model="test-model",
            pending_question=None,
        )

    monkeypatch.setattr(
        scheduler,
        "_run_plugin_proactive_turn",
        fake_run_plugin_proactive_turn,
    )
    monkeypatch.setattr(scheduler, "_deliver_proactive_message", AsyncMock())

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    assert seen["chat_id"] == 7
    assert seen["db_path"] == "db.sqlite3"
    assert "scheduler-initiated proactive check-in" in seen["prompt"]
    assert "Recent memories about the user" not in seen["prompt"]
    assert "autonomous work cycle, not a social check-in" in seen["prompt"]
    assert "use tools and complete the work now" in seen["prompt"]
    assert "Never claim or imply that the user just woke up" in seen["prompt"]
    assert "Trigger: system scheduler; no new user activity" in seen["prompt"]
    assert "Do not send a greeting, check-in, small talk" in seen["prompt"]
    # A delivered message advances the unanswered streak by exactly one.
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    assert scheduler._LOTTERY_STATE["last_proactive_time"] > 0


async def test_heartbeat_proactive_check_stays_silent_when_agent_skips(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    seen = {"notified": False}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_run_plugin_proactive_turn(prompt, **_kwargs):
        seen["prompt"] = prompt
        return SimpleNamespace(text="", model="", pending_question=None)

    async def fake_notify(*args, **kwargs):
        seen["notified"] = True

    monkeypatch.setattr(
        scheduler,
        "_run_plugin_proactive_turn",
        fake_run_plugin_proactive_turn,
    )
    monkeypatch.setattr(scheduler, "notify", fake_notify)

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    # Agent returned no text -> nothing is delivered and the unanswered streak
    # does not advance.
    assert seen["notified"] is False
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0
    assert "scheduler-initiated proactive check-in" in seen["prompt"]
    # A work cycle with nothing material to do must bow out silently instead
    # of manufacturing a social check-in.
    assert "If there is no useful safe action or no material result" in seen["prompt"]


async def test_proactive_single_ignored_message_does_not_snowball_into_cooldown(monkeypatch):
    """Regression: the unanswered streak must track delivered messages, not
    heartbeat ticks. One ignored message followed by silent ticks must NOT
    accumulate into the cooldown threshold."""
    import time

    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=0, cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    # Deliver exactly one message on the first tick; stay silent ever after.
    calls = {"n": 0}

    async def fake_run_plugin_proactive_turn(prompt, **_kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            text="hey, how did the launch go?" if calls["n"] == 1 else "",
            model="test-model",
            pending_question=None,
        )

    monkeypatch.setattr(
        scheduler,
        "_run_plugin_proactive_turn",
        fake_run_plugin_proactive_turn,
    )
    monkeypatch.setattr(scheduler, "_deliver_proactive_message", AsyncMock())

    # The user never replies (reset_lottery is never called) across many ticks.
    for _ in range(6):
        await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    # Only the single delivery counts; no multi-day cooldown is armed.
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 1
    assert scheduler._LOTTERY_STATE["cooldown_until"] == 0.0
    assert scheduler._LOTTERY_STATE["cooldown_until"] <= time.time()


async def test_proactive_cooldown_arms_when_streak_reaches_threshold(monkeypatch):
    """Once ``_PROACTIVE_COOLDOWN_THRESHOLD`` delivered messages go unanswered,
    the next check arms the cooldown instead of sending again."""
    import time

    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    sent = {"count": 0}

    monkeypatch.setattr(scheduler, "OWNER_ID", 7)
    monkeypatch.setattr(scheduler, "_load_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_save_lottery_state", lambda: None)
    monkeypatch.setattr(scheduler, "_is_daytime", lambda: True)
    monkeypatch.setattr(scheduler, "_silence_hours", lambda: 96.0)
    monkeypatch.setattr(scheduler, "_latest_workbench_user_activity", lambda: None)
    monkeypatch.setattr(scheduler, "notify", AsyncMock())
    scheduler._LOTTERY_STATE.update(
        consecutive_unanswered=scheduler._PROACTIVE_COOLDOWN_THRESHOLD,
        cooldown_until=0.0, last_proactive_time=0.0, probability=0.0,
    )

    async def fake_run_plugin_proactive_turn(prompt, **_kwargs):
        sent["count"] += 1
        return SimpleNamespace(text="hi", model="test-model", pending_question=None)

    monkeypatch.setattr(
        scheduler,
        "_run_plugin_proactive_turn",
        fake_run_plugin_proactive_turn,
    )

    await scheduler._heartbeat_proactive_check(bot=None, db_path="db.sqlite3")

    assert sent["count"] == 0
    assert scheduler._LOTTERY_STATE["cooldown_until"] > time.time()
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0

    # A user message clears the cooldown so the agent can speak again.
    scheduler.reset_lottery()
    assert scheduler._LOTTERY_STATE["cooldown_until"] == 0.0
    assert scheduler._LOTTERY_STATE["consecutive_unanswered"] == 0


def test_assistant_text_ignores_reasoning_when_tool_calls_present():
    """Regression: a turn that emits tool_calls (e.g. ``quit``) with empty
    content must NOT surface ``reasoning_content`` as user-facing text — that
    leaked the model's chain-of-thought into proactive messages. Pure-text
    turns (no tool_calls) still fall back to reasoning for Qwen-style models."""
    from cyrene.model_runtime.messages import _assistant_text

    quit_turn = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "The user hasn't replied yet... Let me just quit.",
        "tool_calls": [{"id": "c1", "function": {"name": "quit", "arguments": "{}"}}],
    }
    assert _assistant_text(quit_turn) == ""

    # No tool_calls: the reasoning fallback is still honored (Qwen-style models).
    plain_turn = {"role": "assistant", "content": "", "reasoning_content": "final answer"}
    assert _assistant_text(plain_turn) == "final answer"

    # Real content always wins, even alongside tool_calls.
    spoke_turn = {
        "role": "assistant",
        "content": "scheduled task completed",
        "reasoning_content": "scratch",
        "tool_calls": [{"id": "c2", "function": {"name": "quit", "arguments": "{}"}}],
    }
    assert _assistant_text(spoke_turn) == "scheduled task completed"


def test_last_user_time_prefers_archive_over_state_mtime(tmp_path, monkeypatch):
    """Silence detection must read the real user-turn timestamp from the
    conversation archive, not state.json's mtime. The agent rewrites state.json
    on its own (proactive replies, steward, ...), so a fresh mtime would
    otherwise mask genuine user silence and suppress the >72h reach-out."""
    from datetime import datetime, timezone

    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    # The user actually last spoke on 2026-06-02 at 09:00 UTC (recorded once,
    # per turn, in the archive).
    (conv_dir / "2026-06-02.md").write_text(
        "# 2026-06-02\n\n## 09:00:00 UTC\n\n**User**: morning!\n\n**Cyrene**: hi\n",
        encoding="utf-8",
    )
    # state.json was just rewritten by the agent — its last message is a
    # proactive reply and its mtime is "now". That must NOT count as user activity.
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "user", "content": "morning!"},
            {"role": "assistant", "content": "checking in", "proactive": True},
        ]}),
        encoding="utf-8",
    )

    memory_service = SimpleNamespace(
        latest_archived_user_message_time=lambda: datetime(
            2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc
        )
    )
    monkeypatch.setattr(scheduler, "_memory_service", lambda: memory_service)
    monkeypatch.setattr(scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)

    result = scheduler._last_user_message_time()

    assert result == datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc)


def test_last_user_time_mtime_fallback_requires_user_spoke_last(tmp_path, monkeypatch):
    """Before anything is archived, fall back to state.json mtime only when the
    most recent message is the user's; otherwise report unknown (None) so we
    never treat one of the agent's own writes as user activity."""
    import os
    from datetime import datetime, timezone

    from agent.plugin.plugin_impl.cyrene_proactive import service as scheduler

    state_file = tmp_path / "state.json"
    memory_service = SimpleNamespace(latest_archived_user_message_time=lambda: None)
    monkeypatch.setattr(scheduler, "_memory_service", lambda: memory_service)
    monkeypatch.setattr(scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)

    # (a) User spoke last → mtime is a valid proxy.
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "you there?"},
        ]}),
        encoding="utf-8",
    )
    pinned = datetime(2026, 6, 4, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(state_file, (pinned, pinned))
    result = scheduler._last_user_message_time()
    assert result is not None
    assert abs(result.timestamp() - pinned) < 1.0

    # (b) Agent spoke last (proactive) → mtime is the agent's write, not the
    #     user's, so it must be ignored.
    state_file.write_text(
        json.dumps({"messages": [
            {"role": "user", "content": "you there?"},
            {"role": "assistant", "content": "yes!", "proactive": True},
        ]}),
        encoding="utf-8",
    )
    assert scheduler._last_user_message_time() is None

def test_pending_permission_public_shape_keeps_only_localizable_meta():
    from cyrene.workbench.session_view import build_pending_question

    result = build_pending_question({
        "id": "question_ui",
        "text": "legacy text",
        "meta": {
            "kind": "self_configuration_confirmation",
            "tool_name": "cyrene.ui.click",
            "operation": "cyrene.ui.click.r2",
            "path_hint": "cyrene-setting:argument-hash",
            "reason": "提交搜索请求",
            "secret_internal_plan": "must not leak",
        },
        "options": ["允许这一次", "拒绝"],
    })

    assert result is not None
    assert result["meta"] == {
        "kind": "self_configuration_confirmation",
        "tool_name": "cyrene.ui.click",
        "operation": "cyrene.ui.click.r2",
        "path_hint": "cyrene-setting:argument-hash",
        "reason": "提交搜索请求",
    }


def test_inbox_send_message_is_serialized():
    from cyrene.runtime import inbox
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        inbox.INBOX_DIR = Path(tmp) / "inbox"

        async def send_and_read():
            await asyncio.gather(*[
                inbox.send_message(f"sender_{i}", "receiver", "chat", f"payload_{i}")
                for i in range(20)
            ])
            return await inbox.read_messages("receiver", mark_read=False)

        messages = asyncio.run(send_and_read())
        ids = [m["message_id"] for m in messages]
        assert len(messages) == 20
        assert len(set(ids)) == 20
        assert inbox.get_unread_count("receiver") == 20


# ---------------------------------------------------------------------------
# Issue #38 — Credential isolation between model providers
# ---------------------------------------------------------------------------


def test_recent_main_agent_activity_ignores_completed_accounting_events_and_terminal_phases():
    from datetime import datetime, timedelta, timezone

    from cyrene.workbench.session_view import has_recent_main_agent_activity

    now = datetime.now(timezone.utc)

    def stamp(offset):
        return (now + timedelta(seconds=offset)).isoformat()

    assert has_recent_main_agent_activity([
        {"type": "session_update", "status": "running", "timestamp": stamp(-4)},
        {"type": "llm_call", "caller": "main_agent", "status": "completed", "timestamp": stamp(-3)},
        {"type": "phase_transition", "to": "done", "timestamp": stamp(-2)},
    ], now) is False

    assert has_recent_main_agent_activity([
        {"type": "tool_call_started", "caller": "main_agent", "tool_call_id": "a", "timestamp": stamp(-4)},
        {"type": "tool_call_started", "caller": "main_agent", "tool_call_id": "b", "timestamp": stamp(-3)},
        {"type": "tool_call_finished", "caller": "main_agent", "tool_call_id": "a", "timestamp": stamp(-2)},
    ], now) is True
