"""Execution limits must not infer task progress from tool output novelty."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyrene.core import AgentSession
from cyrene.core.context import ContextStoreRouter
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry, plugin_session_state, with_plugin_session_state
from cyrene.platform import inbox
from cyrene.plugins.builtin.cyrene_subagent import application_setup
from cyrene.plugins.builtin.cyrene_subagent.contracts import ExecutionLimits, SubagentRecord
from cyrene.plugins.builtin.cyrene_subagent.manager import SubagentManager


@pytest.fixture
def default_limits(monkeypatch):
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_subagent.contracts.settings_store.get",
        lambda key, default=None: default,
    )


@pytest.mark.parametrize("observations", ["discovery", "repeated_results"])
def test_execution_continues_through_discovery_and_repeated_results(tmp_path, monkeypatch, default_limits, observations):
    async def scenario():
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        source = Path(__file__).parents[1] / "src/cyrene/plugins/builtin/cyrene_subagent"
        shutil.copytree(source, plugins / "cyrene_subagent")
        discovery = [
            {"operation": "list"},
            {"operation": "describe", "name": "ReadProbe"},
            {"operation": "describe", "name": "StatusProbe"},
            {"operation": "describe", "name": "ReadProbe"},
        ]
        repeats = 1 if observations == "discovery" else 6
        operations = discovery + [
            {"operation": "invoke", "name": "ReadProbe", "arguments": {}}
            for _ in range(repeats)
        ]
        executed = []
        child_turns = []

        async def model(arguments, context):
            if context.data.get("model_call_kind") == "permission":
                return {"content": "", "tool_calls": [{
                    "id": "allow", "name": "decide",
                    "arguments": {"approve": True, "rationale": "isolated test"},
                }]}
            if context.data.get("agent_id", "main") == "main":
                return {"content": "parent ready", "tool_calls": []}
            index = len(child_turns)
            child_turns.append(index)
            if index < len(operations):
                return {"content": "", "tool_calls": [{
                    "id": f"call-{index}", "name": "toolbox", "arguments": operations[index],
                }]}
            if index == len(operations):
                return {"content": "Read the requested evidence.", "tool_calls": [{
                    "id": "quit", "name": "quit", "arguments": {
                        "completion_status": "completed",
                        "criteria_evidence": [{"criterion": "read evidence", "evidence": "Probe returned the evidence."}],
                    },
                }]}
            return {"content": "Evidence collected.", "tool_calls": []}

        def read_probe(arguments, context):
            executed.append(context.data.get("agent_id"))
            return "identical evidence"

        registry = PluginRegistry()
        registry.register_pack(PluginPack("model", "model", (
            Plugin("MiniMax", "model", {"type": "object"}, model, kind="model"),
        )), source="test")
        registry.register_pack(PluginPack("probe", "probe", tuple(
            Plugin(name, name, {"type": "object", "properties": {}}, read_probe)
            for name in ("ReadProbe", "StatusProbe")
        )), source="test")
        session = AgentSession(
            tmp_path / "data", tmp_path / "workspace", plugins,
            registry=registry, plugin_context_data={"session_id": "limit-regression"},
        )
        try:
            session.submit("seed", run_id="seed")
            await session.drain()
            manager = session.plugin_services["subagents"]
            await manager.spawn("main", "worker", "Read evidence", success_criteria=["read evidence"])
            await asyncio.wait_for(session.drain(), 20)
            record = manager._records["worker"]
            assert executed == ["worker"] * repeats
            assert record.status == "done"
            assert record.outcome == "completed"
            assert record.finalization_reason == ""
            assert record.metrics.tool_calls == repeats
            assert "no_progress_turns" not in record.metrics.as_dict()
            assert "seen_result_fingerprints" not in record.as_dict()
        finally:
            session.close()

    asyncio.run(scenario())


@pytest.fixture
def manager(tmp_path, default_limits):
    store = ContextStoreRouter(tmp_path / "store")
    tree = store.create_tree({"role": "system", "content": "test"})
    manager = SubagentManager(SimpleNamespace(store=store, tree=tree, plugin_context_data={}))
    manager._records["worker"] = SubagentRecord("worker", "child", "task", "main", "run")
    yield manager
    store.close()


@pytest.mark.parametrize("kind,reason", [
    ("tools", "execution_tool_budget_exhausted"),
    ("time", "execution_wall_time_exhausted"),
    ("cost", "execution_cost_budget_exhausted"),
    ("context", "execution_context_budget_exhausted"),
])
def test_resource_limits_still_block_work_but_allow_finish(manager, kind, reason):
    record = manager._records["worker"]
    limits = ExecutionLimits.current()
    if kind == "tools":
        record.metrics.lease_tool_calls = limits.max_tool_calls - 1
        manager.record_tool_execution("worker", "Read")
        assert record.metrics.lease_tool_calls == limits.max_tool_calls
    elif kind == "time":
        record.lease_started_at = (datetime.now(timezone.utc) - timedelta(seconds=limits.max_wall_seconds + 1)).isoformat()
    elif kind == "cost":
        record.metrics.estimated_cost_usd = limits.max_cost_usd
    else:
        manager.observe_context("worker", 100, 100)
    assert manager.review_tool("worker", "Read", {})["decision"] == "block"
    assert record.finalization_reason == reason
    assert manager.review_tool("worker", "quit", {})["decision"] == "allow"


@pytest.mark.parametrize("status", ["running", "incomplete", "done", "cancelled", "failed"])
def test_old_checkpoints_drop_removed_fence_without_reviving_terminal_work(manager, status):
    old = manager._records["worker"].as_dict()
    old.update({
        "status": status,
        "finalization_reason": "execution_no_progress",
        "stop_reason": "execution_no_progress",
        "result": "original evidence",
        "reported_node_id": "already-reported" if status != "running" else "",
        "seen_tool_signatures": ["old-signature"],
        "seen_result_fingerprints": ["old-result"],
    })
    old["metrics"]["no_progress_turns"] = 3
    old["metrics"]["tool_calls"] = 17
    owner = manager.owner
    owner.store.update_node(owner.tree.id, owner.tree.root_id, with_plugin_session_state(
        {"role": "system"}, "cyrene_subagent", {"records": {"worker": old}},
    ))
    restored = SubagentManager(owner)
    record = restored._records["worker"]
    assert record.status == status
    assert record.result == "original evidence"
    assert record.reported_node_id == old["reported_node_id"]
    assert record.stop_reason == "execution_no_progress"  # Historical reason, not an execution fence.
    assert record.finalization_reason == ""
    assert record.metrics.tool_calls == 17
    assert restored.has_active is (status == "running")
    if status == "running":
        assert restored.review_tool("worker", "Read", {})["decision"] == "allow"
        assert "Finalization Required" not in restored.mode_context("worker")
    restored._persist()
    saved = plugin_session_state(owner.store.get_node(owner.tree.id, owner.tree.root_id).value, "cyrene_subagent")["records"]["worker"]
    assert "seen_tool_signatures" not in saved
    assert "seen_result_fingerprints" not in saved
    assert "no_progress_turns" not in saved["metrics"]


def test_checkpoint_keeps_other_resource_fences(manager):
    old = manager._records["worker"].as_dict()
    old["finalization_reason"] = "execution_cost_budget_exhausted"
    restored = SubagentRecord.from_value("worker", old)
    assert restored.finalization_reason == "execution_cost_budget_exhausted"


def test_removed_setting_is_not_advertised_locally_or_remotely():
    from cyrene.plugins.builtin.cyrene_remote.commands import _REMOTE_SETTING_FIELDS

    services = {}
    context = SimpleNamespace(expose_frontend=lambda name: None, provide=services.__setitem__)
    application_setup(context)
    keys = {spec.key for spec in services["subagent_settings"].setting_specs()}
    assert "subagent_execution_no_progress_turns" not in keys
    assert "subagent_execution_max_tool_calls" in keys
    assert "subagent_discussion_no_new_info_rounds" in keys
    assert all(field["key"] != "subagent_execution_no_progress_turns" for field in _REMOTE_SETTING_FIELDS)
