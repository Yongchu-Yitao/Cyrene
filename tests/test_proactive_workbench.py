from pathlib import Path

from cyrene.plugins.builtin.cyrene_proactive import plugin_pack


def test_proactive_pack_registers_private_assistant_revisit():
    from types import SimpleNamespace
    services = {}
    modules = []
    plugin_pack.application_setup(SimpleNamespace(provide=services.__setitem__, expose_frontend=modules.append))
    assert {tool.name for tool in plugin_pack.plugins} == {"assistant.revisit_draw"}
    assert modules == ["proactive"]
    assert set(services) == {"proactive_settings"}
    assert [spec.key for spec in services["proactive_settings"].specs] == ["agent_proactive", "agent_proactive_daily_frequency"]


def test_workbench_frontend_handles_proactive_sse():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cyrene/workbench/webui/frontend/features/chat/live-event-controller.jsx"
    ).read_text(encoding="utf-8")

    assert 'event.type === "workbench_proactive_message"' in source
    assert "wbcApplyProactiveMessage(context, event)" in source
    assert "messages.some(function (item) { return item.id === message.id; })" in source
    assert "messages: messages.concat([message])" in source


async def test_legacy_editable_pack_cannot_schedule_or_start_work(tmp_path):
    import pytest
    from types import SimpleNamespace
    from cyrene.plugins.background import background_job_spec
    from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime

    legacy = SimpleNamespace(name="proactive.heartbeat", metadata={"background_job": {"id": "proactive_heartbeat"}})
    assert background_job_spec(legacy) is None
    config = ConversationConfig(session_id="work", workspace_dir=str(tmp_path), db_path="", command="proactive-heartbeat")
    with pytest.raises(ValueError, match="Legacy proactive work sessions"):
        await ConversationRuntime("").send(config, "resume", run_id="legacy")


async def test_private_wakes_survive_restart_replace_and_deliver_without_user_text(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from cyrene.plugins.builtin.session_management import followup
    from cyrene.plugins.builtin.session_management.store import Store
    from cyrene.workbench.http.workbench.chat_routes.send_request import SendOrigin
    from cyrene.workbench.http.workbench.chat_routes.send_input import append_user_message, SendInput

    owner = followup.ASSISTANT_ID
    host = SimpleNamespace(chat=lambda _: {"kind": "personal-assistant"}, version=lambda _: "v1",
                           status=lambda _: "idle")
    captured = []
    async def dispatch(target, message, **kwargs):
        private = followup.private_context(service.store, kwargs["client_request_id"], kwargs["expected_version"])
        captured.append(private)
        assert target == owner and "hidden second" not in message
        origin = SendOrigin.parse({"clientRequestId": kwargs["client_request_id"], "agentOriginated": True,
                                   "sourceSessionId": owner, "agentMessageKind": kwargs["agent_message_kind"]})
        messages = []
        append_user_message({}, messages, SendInput(message, message, "", [], []), origin, "", lambda _: "id")
        assert not messages
    host.port = lambda: SimpleNamespace(dispatch_agent_message=dispatch)
    service = SimpleNamespace(store=Store(tmp_path / "managed.db"), host=host)
    at = datetime.fromtimestamp(1010, timezone.utc)
    monkeypatch.setattr(followup.time, "time", lambda: 1000)
    monkeypatch.setattr(followup, "long_enabled", lambda: False)
    followup.schedule(service, owner, "ctx", at, "hidden first", "one")
    followup.schedule(service, owner, "ctx", at, "hidden second", "two")
    followup.schedule(service, owner, "ctx", at, "hidden first", "one")
    service.store = Store(tmp_path / "managed.db")
    await followup.deliver_due(service)
    assert not captured and followup.deadline(service.store) == 10
    monkeypatch.setattr(followup.time, "time", lambda: 1011)
    await followup.deliver_due(service)
    await followup.deliver_due(service)
    assert len(captured) == 1 and "hidden second" in captured[0]


def test_private_timer_can_be_cancelled_without_replaying_old_request(tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from cyrene.plugins.builtin.session_management import followup
    from cyrene.plugins.builtin.session_management.store import Store
    host = SimpleNamespace(chat=lambda _: {"kind": "personal-assistant"}, version=lambda _: "v1")
    service = SimpleNamespace(store=Store(tmp_path / "db"), host=host)
    at = datetime.now(timezone.utc) + timedelta(minutes=1)
    followup.schedule(service, followup.ASSISTANT_ID, "ctx", at, "check", "1")
    followup.schedule(service, followup.ASSISTANT_ID, "ctx", None, "", "2")
    followup.schedule(service, followup.ASSISTANT_ID, "ctx", at, "check", "1")
    with service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM assistant_wakes WHERE state='pending'").fetchone()[0] == 0


def test_revisit_decision_requires_corresponding_work_evidence():
    import json
    from types import SimpleNamespace
    from cyrene.plugins.builtin.cyrene_proactive.revisit import report_from
    report = {"decision": "report", "source": "https://example.org/new", "previous": "absent", "change": "published", "relevance": "usable"}
    search = {"name": "WebSearch", "success": True, "value": "source: https://example.org/new"}
    work = SimpleNamespace(run_id="work", snapshot={"nodes": [
        {"value": {"role": "tool_results", "run_id": "work", "results": [search]}}]})
    def check(body, status="completed"):
        return report_from(work, SimpleNamespace(text=json.dumps(body), status=status),
                           {"context_id": "ctx1", "current_task_results": {"task:result": "done"}})
    assert check(report) == report
    for source in ("context:ctx1", "task:result"):
        assert check({**report, "source": source})["source"] == source
    assert check({**report, "source": "context:other"}) is None
    assert check({"decision": "silent"}) is None
    assert check({**report, "change": ""}) is None
    assert check(report, "awaiting_user") is None
    work.run_id = "other"
    assert check(report) is None


async def test_child_completion_triggers_one_private_decision(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from cyrene.plugins.builtin.cyrene_cards import home_preparation
    from cyrene.plugins.builtin.cyrene_proactive import revisit
    from cyrene.workbench import core_adapter

    runtime = SimpleNamespace(send=AsyncMock(), shutdown=AsyncMock())
    monkeypatch.setattr(core_adapter, "ConversationRuntime", lambda _: runtime)
    monkeypatch.setattr(home_preparation, "home_model_identity", lambda _: {})
    service = Mock()
    service.store.path = tmp_path / "db"
    for status in ("awaiting_user", "completed"):
        runtime.send.reset_mock()
        runtime.send.side_effect = [SimpleNamespace(status=status, run_id="work"),
                                    SimpleNamespace(status="completed", text='{"decision":"silent"}')]
        assert await revisit.run_child(service, {}, {"context_id": "ctx"}) is None
        calls = runtime.send.await_args_list
        assert len(calls) == (2 if status == "completed" else 1)
        if status == "completed":
            work, decision = calls
            assert work.args[0].session_id == decision.args[0].session_id
            assert decision.args[1] == revisit.DECISION_PROMPT
            assert decision.kwargs["run_id"] == "work_decision"
            assert decision.kwargs["metadata"]["system_initiated"]
            assert not decision.args[0].tool_scope.tool_names


async def test_lottery_only_wakes_parent_for_report(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from datetime import datetime, timezone
    from cyrene.plugins.builtin.cyrene_proactive import revisit
    from cyrene.plugins.builtin.session_management.store import Store
    chat = {"kind": "personal-assistant", "id": "assistant_global"}
    service = SimpleNamespace(store=Store(tmp_path / "db"), host=SimpleNamespace(
        chat=lambda _: chat, status=lambda _: "idle", version=lambda _: "v1"))
    context = SimpleNamespace(services={"session_management": service}, data={"background_job_id": "assistant_revisit_draw"})
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 20, 12, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(revisit, "datetime", Clock)
    monkeypatch.setattr(revisit, "get", lambda key, default: "UTC" if key == "timezone" else default)
    monkeypatch.setattr(revisit, "long_enabled", lambda: True)
    monkeypatch.setattr(revisit.random, "random", lambda: 0)
    monkeypatch.setattr(revisit, "select_block", lambda *_: ("research", {}))
    child = AsyncMock(side_effect=[None, {"decision": "report", "source": "https://example.org/result"}])
    monkeypatch.setattr(revisit, "run_child", child)
    parent = Mock()
    monkeypatch.setattr(revisit, "schedule", parent)
    await revisit.draw({}, context)
    for _ in range(2):
        with service.store.connect() as db:
            db.execute("UPDATE revisit_lottery SET last_draw=0,p=0.5")
        await revisit.draw({}, context)
    assert child.await_count == 2
    parent.assert_called_once()
    monkeypatch.setattr(revisit, "long_enabled", lambda: False)
    assert await revisit.draw({}, context) == {"status": "disabled"}

def test_revisit_mean_sets_existing_job_cadence(monkeypatch):
    from cyrene.plugins.background import background_job_spec
    from cyrene.plugins.builtin.cyrene_proactive import revisit

    for frequency in (0.09, 1, 5, 64):
        monkeypatch.setattr(revisit, "get", lambda key, default: frequency)
        interval = background_job_spec(revisit.draw_tool).interval_seconds
        assert 0 < interval <= 3600
        admission = 3600 / revisit.draw_period() if revisit.draw_period() > 3600 else 1
        mean_seconds = interval * revisit.DRAWS_PER_WIN / admission
        assert abs(mean_seconds - 16 * 3600 / frequency) < 2


def test_daily_frequency_defaults_and_existing_interval(monkeypatch):
    from cyrene.plugins.builtin.cyrene_proactive import revisit

    settings = {}
    monkeypatch.setattr(revisit, "get", settings.get)
    assert revisit.default_daily_frequency() == 1
    settings["agent_revisit_mean_minutes"] = 120
    assert revisit.default_daily_frequency() == 8
    settings["agent_proactive_daily_frequency"] = 5
    assert revisit.draw_period() == 16 * 3600 / (5 * revisit.DRAWS_PER_WIN)
