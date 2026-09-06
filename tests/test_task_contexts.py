"""Task switching across real model/tool transitions, persistence and isolation."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from cyrene.core.session import AgentSession
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.core.context.tasks import TOOLS, clip_summary
from cyrene.core.context.projection import project_model_messages


def run(coro):
    return asyncio.run(coro)


def make_session(tmp_path, model=None, **kwargs):
    async def default(arguments, context):
        return {"content": "answer", "tool_calls": []}
    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, model or default, kind="model"),
    )), source="test")
    (tmp_path / "plugins").mkdir(exist_ok=True)
    session = AgentSession(tmp_path / "data", tmp_path, tmp_path / "plugins", registry=registry, **kwargs)
    session._configured_compaction_limit = lambda: 0
    return session


def command(s, name, args, receipt):
    return run(s.task_contexts.execute(name, args, receipt))


def test_catalog_core_tools_and_lazy_creation(tmp_path):
    s = make_session(tmp_path)
    try:
        assert s.task_contexts.read()["active"] is None
        assert TOOLS <= {t["function"]["name"] for t in s._direct_model_tool_definitions()}
        assert all(s.registry.registered(name).pack_id == "core" for name in TOOLS)
        with pytest.raises(ValueError):
            command(s, "load_context", {"context_id": "missing"}, "load")
        assert not s.task_contexts.read()["documents"]
        s.submit("new task", run_id="r")
        run(s.drain())
        state = s.task_contexts.read()
        assert state["active"] in state["documents"]
        assert "task_context_catalog" in str(s._messages(s.snapshot()["leaf_id"]))
    finally:
        s.close()


def test_unload_clip_edit_any_context_and_idempotency(tmp_path):
    s = make_session(tmp_path)
    replacement_body = "TASK_A_REPLACEMENT_EVIDENCE_82f1"
    try:
        a = s.task_contexts.ensure("a")
        summary = "首" * 220 + "尾" * 220
        command(s, "unload_context", {"summary": summary}, "unload-a")
        assert s.task_contexts.read()["active"] is None
        assert len(s.task_contexts.read()["documents"][a]["summary"]) == 200
        assert clip_summary(summary).startswith("首") and clip_summary(summary).endswith("尾")
        b = s.task_contexts.ensure("b")
        command(s, "append_context", {"context_id": a, "content": "first"}, "append")
        command(s, "append_context", {"context_id": a, "content": "first"}, "append")
        assert s.task_contexts.read()["documents"][a]["body"] == "first"
        command(s, "replace_context", {"context_id": a, "content": replacement_body}, "replace")
        assert s.task_contexts.read()["active"] == b
        assert replacement_body not in str(s._messages(s.tree.root_id))
        with pytest.raises(ValueError):
            command(s, "load_context", {"context_id": a}, "blocked")
        command(s, "unload_context", {"summary": "B paused"}, "unload-b")
        command(s, "load_context", {"context_id": a}, "load-a")
        assert replacement_body in str(s._messages(s.tree.root_id))
        assert "revision" not in str(s.task_contexts.read())
    finally:
        s.close()
    reopened = make_session(tmp_path)
    try:
        assert reopened.task_contexts.read()["active"] == a
        assert reopened.task_contexts.read()["documents"][a]["body"] == replacement_body
    finally:
        reopened.close()


def test_real_switch_preserves_prose_isolates_tools_and_snapshots(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("secret evidence " * 1000)
    inputs = []
    async def model(arguments, context):
        inputs.append(deepcopy(arguments["messages"]))
        index = len(inputs)
        calls = {
            1: ("Read", {"path": str(source)}),
            2: ("unload_context", {"summary": "Read evidence; pending analysis"}),
        }
        if index in calls:
            name, args = calls[index]
            return {"content": "shared prose", "tool_calls": [{"id": f"c{index}", "name": name, "arguments": args}]}
        return {"content": "new task answer", "tool_calls": []}
    s = make_session(tmp_path, model)
    try:
        s.submit("read then switch", run_id="r")
        run(s.drain())
        assert len(inputs) == 3
        assert "secret evidence " * 100 in str(inputs[1])
        assert "secret evidence" not in str(inputs[2])
        assert "shared prose" in str(inputs[2])
        state = s.task_contexts.read()
        assert len(state["documents"]) == 2
        a = next(key for key in state["documents"] if key != state["active"])
        command(s, "unload_context", {"summary": "pause new task"}, "pause")
        command(s, "load_context", {"context_id": a}, "restore")
        messages = s._messages(s.snapshot()["leaf_id"])
        assert "snapshot_path" in str(messages)
        assert "secret evidence " * 100 not in str(messages)
        path = s.store.get_path(s.tree.id, s.snapshot()["leaf_id"])
        assert project_model_messages(path) == messages
        snapshots = list(s.store.artifact_directory(s.tree.id).glob("*.json"))
        assert snapshots and json.loads(snapshots[0].read_text()) == source.read_text()
        directory = s.store.artifact_directory(s.tree.id)
    finally:
        s.close()
    from cyrene.core.context import ContextStoreRouter
    with ContextStoreRouter(tmp_path / "data" / "context") as store:
        store.delete_tree("agent-session")
    assert not directory.exists()


def test_missing_artifact_load_keeps_inactive_state(tmp_path):
    s = make_session(tmp_path)
    try:
        a = s.task_contexts.ensure("a")
        s.store.mount(s.tree.id, s.tree.root_id, {"role": "tool_results", "task_context_id": a,
            "results": [{"task_reference": {"snapshot_path": str(tmp_path / "missing")}}]})
        command(s, "unload_context", {"summary": "pause"}, "pause")
        with pytest.raises(OSError):
            command(s, "load_context", {"context_id": a}, "restore")
        assert s.task_contexts.read()["active"] is None
    finally:
        s.close()


def test_mixed_control_batch_does_not_execute_other_tool(tmp_path):
    calls = 0
    async def model(arguments, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"content": "", "tool_calls": [
                {"id": "u", "name": "unload_context", "arguments": {"summary": "pause"}},
                {"id": "w", "name": "Write", "arguments": {"path": str(tmp_path / "forbidden"), "content": "bad"}},
            ]}
        assert "must be called alone" in str(arguments["messages"])
        return {"content": "corrected", "tool_calls": []}
    s = make_session(tmp_path, model)
    try:
        s.submit("task", run_id="r")
        run(s.drain())
        assert calls == 2
        assert not (tmp_path / "forbidden").exists()
    finally:
        s.close()


def test_compaction_only_changes_active_document(tmp_path):
    s = make_session(tmp_path)
    try:
        command(s, "replace_context", {"context_id": "shared", "content": "Shared contract; source: spec.md; scope: all"}, "shared")
        shared_before = deepcopy(s.task_contexts.read()["shared"])
        a = s.task_contexts.ensure("a")
        command(s, "replace_context", {"context_id": a, "content": "A untouched"}, "a")
        command(s, "unload_context", {"summary": "A pause"}, "ua")
        b = s.task_contexts.ensure("b")
        command(s, "replace_context", {"context_id": b, "content": "evidence " * 5000}, "b")
        s.submit("shared exact request", run_id="r")
        run(s.drain())
        # Provide a short exact tool episode so the earlier body can be folded.
        leaf = s.snapshot()["leaf_id"]
        call = s.store.mount(s.tree.id, leaf, {"role": "assistant", "content": "shared answer", "task_context_id": b,
            "tool_calls": [{"id": "t", "name": "Read", "arguments": {"path": "x"}}], "run_id": "r"})
        result = s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_context_id": b, "run_id": "r",
            "results": [{"call_id": "t", "name": "Read", "success": True, "value": "short"}]})
        class Gateway:
            async def complete(self, messages, **kwargs):
                assert "A untouched" not in str(messages)
                assert "Shared contract" not in str(messages)
                assert "shared exact request" not in str(messages)
                return {"content": "task evidence summary"}
        s._plugin_service_values["model"] = Gateway()
        node, outcome = run(s._compact_at_node(result, context_limit=0, force=True, reason="manual", resume_model=False))
        assert outcome["compacted"]
        assert s.task_contexts.read()["shared"] == shared_before
        assert "Shared contract" in str(s._messages(node.id))
        assert s.task_contexts.read()["documents"][a]["body"] == "A untouched"
        effective = s._messages(node.id)
        assert "shared exact request" in str(effective)
        assert "shared answer" in str(effective)
        assert "task evidence summary" in str(effective)
        assert "messages" not in node.value
    finally:
        s.close()


def test_serial_gate_covers_read_through_commit(tmp_path):
    s = make_session(tmp_path)
    try:
        a = s.task_contexts.ensure("a")
        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()
            async def rewrite():
                async with s.task_contexts.serial():
                    state = s.task_contexts.read()
                    started.set()
                    await release.wait()
                    state["documents"][a]["body"] = "rewritten"
                    s.task_contexts.write(state)
            job = asyncio.create_task(rewrite())
            await started.wait()
            append = asyncio.create_task(s.task_contexts.execute("append_context", {"context_id": a, "content": "later"}, "later"))
            await asyncio.sleep(0)
            assert not append.done()
            release.set()
            await asyncio.gather(job, append)
        run(scenario())
        assert s.task_contexts.read()["documents"][a]["body"] == "rewritten\n\nlater"
    finally:
        s.close()


def test_shared_budget_changes_only_on_unload(tmp_path):
    s = make_session(tmp_path)
    try:
        s.submit("old request " * 1000, run_id="old")
        run(s.drain())
        s.submit("current request", run_id="new")
        run(s.drain())
        state = s.task_contexts.read()
        state["shared_token_budget"] = 50
        s.task_contexts.write(state)
        assert "old request " * 100 in str(s._messages(s._leaf_id))
        command(s, "unload_context", {"summary": "pause"}, "pause")
        messages = s._messages(s._leaf_id)
        assert "old request " * 100 not in str(messages)
        assert "current request" in str(messages)
        archive = Path(s.task_contexts.read()["shared_snapshot"])
        assert "old request " * 100 in archive.read_text()
    finally:
        s.close()


def test_legacy_history_migrates_without_rewriting_prose(tmp_path):
    from cyrene.core.context import ContextStoreRouter
    with ContextStoreRouter(tmp_path / "data" / "context") as store:
        tree = store.create_tree({"role": "system", "content": "original rules"}, tree_id="agent-session", root_id="root")
        user = store.mount(tree.id, tree.root_id, {"role": "user", "content": "legacy question", "run_id": "old"})
        call = store.mount(tree.id, user.id, {"role": "assistant", "content": "legacy answer", "run_id": "old",
            "tool_calls": [{"id": "c", "name": "Read", "arguments": {"path": "x"}}]})
        result = store.mount(tree.id, call.id, {"role": "tool_results", "run_id": "old",
            "results": [{"call_id": "c", "name": "Read", "success": True, "value": "legacy tool"}]})
        end = store.mount(tree.id, result.id, {"role": "assistant", "content": "done", "run_id": "old", "session_end_complete": True})
        store.commit_state(tree.id, end.id, "old")
    s = make_session(tmp_path)
    try:
        assert len(s.task_contexts.read()["documents"]) == 1
        messages = s._messages(end.id)
        assert "legacy question" in str(messages) and "legacy tool" in str(messages)
        command(s, "unload_context", {"summary": "old paused"}, "unload")
        messages = s._messages(end.id)
        assert "legacy question" in str(messages) and "legacy answer" in str(messages)
        assert "legacy tool" not in str(messages)
    finally:
        s.close()


def test_unchanged_context_preserves_exact_prefix_and_full_results(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("cache-sensitive evidence " * 1000)
    inputs = []
    async def model(arguments, context):
        messages = deepcopy(arguments["messages"])
        if inputs:
            assert messages[:len(inputs[-1])] == inputs[-1]
        inputs.append(messages)
        if len(inputs) <= 2:
            return {"content": f"reading {len(inputs)}", "tool_calls": [
                {"id": f"read-{len(inputs)}", "name": "Read", "arguments": {"path": str(source)}}]}
        return {"content": "done", "tool_calls": []}
    s = make_session(tmp_path, model)
    try:
        command(s, "replace_context", {"context_id": "shared", "content": "Stable shared agreement; source: user; scope: all"}, "shared")
        s.submit("read twice", run_id="r")
        run(s.drain())
        assert len(inputs) == 3
        assert all(str(messages).count("Stable shared agreement") == 1 for messages in inputs)
        tools = [m for m in inputs[-1] if m.get("role") == "tool"]
        assert len(tools) == 2
        assert all("cache-sensitive evidence " * 100 in m["content"] for m in tools)
        assert [m["role"] for m in inputs[-1]][-4:] == ["assistant", "tool", "assistant", "tool"]
    finally:
        s.close()


def test_shared_is_always_loaded_editable_and_never_active(tmp_path):
    s = make_session(tmp_path)
    try:
        assert s.task_contexts.read()["shared"] == {"body": ""}
        assert s.task_contexts.read()["active"] is None
        assert not s.task_contexts.read()["documents"]
        assert {"id": "shared", "active": False, "always_loaded": True} in s.snapshot()["taskContexts"]
        command(s, "append_context", {"context_id": "shared", "content": "Common API; source: spec.md; scope: all"}, "shared-append")
        assert not s.task_contexts.read()["documents"]
        initial = s._messages(s.tree.root_id)
        assert sum("Common API" in str(m.get("content", "")) for m in initial) == 1
        assert next(m for m in initial if "Common API" in m.get("content", ""))["role"] == "user"
        a = s.task_contexts.ensure("a")
        command(s, "unload_context", {"summary": "A paused"}, "ua")
        assert "Common API" in str(s._messages(s.tree.root_id))
        with pytest.raises(ValueError, match="always loaded"):
            command(s, "load_context", {"context_id": "shared"}, "bad-load")
        assert s.task_contexts.read()["active"] is None
        command(s, "load_context", {"context_id": a}, "la")
        command(s, "replace_context", {"context_id": "shared", "content": "Updated API; source: decision.md; scope: all"}, "shared-replace")
        assert s.task_contexts.read()["active"] == a
        assert "Updated API" in str(s._messages(s.tree.root_id))
        assert "Common API" not in str(s._messages(s.tree.root_id))
    finally:
        s.close()
    reopened = make_session(tmp_path)
    try:
        assert reopened.task_contexts.read()["shared"]["body"].startswith("Updated API")
        assert reopened.task_contexts.read()["active"] == a
    finally:
        reopened.close()


def test_shared_migration_preserves_existing_task_state(tmp_path):
    s = make_session(tmp_path)
    a = s.task_contexts.ensure("existing")
    command(s, "replace_context", {"context_id": a, "content": "Existing body"}, "existing")
    old_state = s.task_contexts.read()
    old_state.pop("shared")
    s.task_contexts.write(old_state)
    s.close()
    reopened = make_session(tmp_path)
    try:
        state = reopened.task_contexts.read()
        assert state.pop("shared") == {"body": ""}
        assert state == old_state
    finally:
        reopened.close()


def test_core_tools_edit_shared_through_real_model_transition(tmp_path):
    inputs = []
    async def model(arguments, context):
        inputs.append(deepcopy(arguments["messages"]))
        if len(inputs) == 1:
            return {"content": "", "tool_calls": [{"id": "shared-write", "name": "append_context",
                "arguments": {"context_id": "shared", "content": "Cross-task agreement; source: user; scope: all"}}]}
        assert "Cross-task agreement" in str(arguments["messages"])
        return {"content": "saved", "tool_calls": []}
    s = make_session(tmp_path, model)
    try:
        s.submit("Save our common agreement", run_id="r")
        run(s.drain())
        assert len(inputs) == 2
        assert s.task_contexts.read()["shared"]["body"].startswith("Cross-task agreement")
        assert s.task_contexts.read()["active"] != "shared"
    finally:
        s.close()


def test_repeated_tool_call_ids_do_not_import_other_task_results(tmp_path):
    s = make_session(tmp_path)
    try:
        a = s.task_contexts.ensure("a")
        def episode(parent, owner, result):
            call = s.store.mount(s.tree.id, parent, {"role": "assistant", "task_context_id": owner,
                "content": "", "tool_calls": [{"id": "repeated", "name": "Read", "arguments": {"path": "x"}}]})
            return s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_context_id": owner,
                "results": [{"call_id": "repeated", "name": "Read", "success": True, "value": result}]})
        first = episode(s.tree.root_id, a, "A evidence")
        command(s, "unload_context", {"summary": "pause"}, "pause")
        b = s.task_contexts.ensure("b")
        second = episode(first.id, b, "B secret evidence")
        command(s, "unload_context", {"summary": "pause"}, "pause-b")
        command(s, "load_context", {"context_id": a}, "load-a")
        messages = s._messages(second.id)
        assert "A evidence" in str(messages)
        assert "B secret evidence" not in str(messages)
        assert len([m for m in messages if m.get("role") == "tool"]) == 1
    finally:
        s.close()


def test_compaction_budget_counts_full_observation_as_task_data(tmp_path):
    s = make_session(tmp_path)
    try:
        owner = s.task_contexts.ensure("a")
        call = s.store.mount(s.tree.id, s.tree.root_id, {"role": "assistant", "task_context_id": owner,
            "content": "", "tool_calls": [{"id": "c", "name": "Read", "arguments": {"path": "x"}}]})
        result = s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_context_id": owner,
            "results": [{"call_id": "c", "name": "Read", "success": True,
                         "value": "large result " * 2000, "task_reference": {"snapshot_path": "small.json"}}]})
        selected, reserved, before = s._compaction_input(result, None)
        assert "large result " * 1000 in str(selected)
        assert reserved < before / 2
    finally:
        s.close()


def test_pending_compaction_marker_recovers_after_reopen(tmp_path):
    s = make_session(tmp_path)
    s.submit("task", run_id="r")
    run(s.drain())
    state = s.task_contexts.read()
    owner = state["active"]
    state["documents"][owner]["body"] = "Committed compacted body"
    state["pending_compaction"] = {"node_id": "pending-marker", "parent_id": s._leaf_id, "value": {
        "role": "context_compaction", "run_id": "r", "task_context_id": owner,
        "resume_model": False, "trigger_model": False}}
    s.task_contexts.write(state)
    s.close()
    reopened = make_session(tmp_path)
    try:
        assert "pending_compaction" not in reopened.task_contexts.read()
        assert reopened.snapshot()["leaf_id"] == "pending-marker"
        assert "Committed compacted body" in str(reopened._messages("pending-marker"))
    finally:
        reopened.close()


def test_fork_replays_prefix_and_owns_artifacts_after_source_deletion(tmp_path):
    from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime
    s = make_session(tmp_path)
    owner = s.task_contexts.ensure("a")
    user = s.store.mount(s.tree.id, s.tree.root_id, {"role": "user", "run_id": "r1", "content": "first"})
    def shared_edit(parent, text, key):
        call = s.store.mount(s.tree.id, parent, {"role": "assistant", "task_control": True, "task_context_id": owner,
            "tool_calls": [{"id": key, "name": "replace_context", "arguments": {"context_id": "shared", "content": text}}]})
        return s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_control": True, "task_context_id": owner,
            "results": [{"call_id": key, "success": True, "name": "replace_context", "value": {}}]})
    edit = shared_edit(user.id, "original common contract", "first-edit")
    call = s.store.mount(s.tree.id, edit.id, {"role": "assistant", "task_context_id": owner,
        "tool_calls": [{"id": "read", "name": "Read", "arguments": {"path": "x"}}]})
    stored = {"call_id": "read", "name": "Read", "success": True, "value": "snapshot evidence"}
    s.task_contexts.reference(stored, {})
    result = s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_context_id": owner, "results": [stored]})
    cutoff = s.store.mount(s.tree.id, result.id, {"role": "user", "run_id": "r2", "content": "later change"})
    shared_edit(cutoff.id, "future contract must not leak", "second-edit")
    command(s, "replace_context", {"context_id": "shared", "content": "future contract must not leak"}, "future")
    s.close()
    runtime = ConversationRuntime(str(tmp_path / "workbench.sqlite3"))
    runtime._state_root = lambda: tmp_path / "data"
    fork = runtime.fork_context("agent-session", "fork", user_ordinal=2)
    assert runtime.delete_context("agent-session")
    reopened = make_session(tmp_path, tree_id="fork")
    try:
        assert reopened.task_contexts.read()["shared"]["body"] == "original common contract"
        messages = reopened._messages(fork["leaf_id"])
        assert "future contract" not in str(messages)
        node = reopened.store.get_node("fork", result.id)
        artifact = Path(node.value["results"][0]["task_reference"]["snapshot_path"])
        assert artifact.is_file()
        assert reopened.store.artifact_directory("fork") in artifact.parents
        command(reopened, "unload_context", {"summary": "pause"}, "pause")
        command(reopened, "load_context", {"context_id": owner}, "load")
    finally:
        reopened.close()


def test_body_only_compaction_and_snapshot_reference_survive(tmp_path):
    s = make_session(tmp_path)
    try:
        owner = s.task_contexts.ensure("a")
        command(s, "replace_context", {"context_id": owner, "content": "repeated task evidence " * 2000}, "body")
        s.submit("keep this exact request", run_id="r")
        run(s.drain())
        class Gateway:
            async def complete(self, messages, **kwargs):
                return {"content": "verified task summary"}
        s._plugin_service_values["model"] = Gateway()
        outcome = run(s.compact_context(context_limit=0))
        assert outcome["compacted"]
        assert "keep this exact request" in str(s._messages(s._leaf_id))
        assert "verified task summary" in str(s._messages(s._leaf_id))
    finally:
        s.close()


def test_snapshot_reference_is_visible_before_and_after_reload(tmp_path):
    s = make_session(tmp_path)
    try:
        owner = s.task_contexts.ensure("a")
        call = s.store.mount(s.tree.id, s.tree.root_id, {"role": "assistant", "task_context_id": owner,
            "tool_calls": [{"id": "read", "name": "Read", "arguments": {"path": "source"}}]})
        result = {"call_id": "read", "name": "Read", "success": True, "value": "reading evidence"}
        s.task_contexts.reference(result, {})
        node = s.store.mount(s.tree.id, call.id, {"role": "tool_results", "task_context_id": owner, "results": [result]})
        tool = next(m for m in s._messages(node.id) if m.get("role") == "tool")
        payload = json.loads(tool["content"])
        assert payload["value"] == "reading evidence"
        assert Path(payload["reference"]["snapshot_path"]).is_file()
        command(s, "unload_context", {"summary": "paused"}, "pause")
        command(s, "load_context", {"context_id": owner}, "load")
        tool = next(m for m in s._messages(node.id) if m.get("role") == "tool")
        assert json.loads(tool["content"])["value"] == payload["reference"]
    finally:
        s.close()


def test_sequential_context_edits_keep_success_receipts_until_switch(tmp_path):
    inputs = []
    async def model(arguments, context):
        inputs.append(deepcopy(arguments['messages']))
        step = len(inputs)
        if step <= 2:
            return {'tool_calls': [{'id': f'edit{step}', 'name': 'append_context',
                'arguments': {'context_id': 'shared', 'content': f'constraint {step}'}}]}
        receipts = [m.get('tool_call_id') for m in arguments['messages'] if m.get('role') == 'tool']
        assert 'edit1' in receipts and 'edit2' in receipts
        return {'content': 'Both edits completed.'}
    s = make_session(tmp_path, model)
    try:
        s.submit('Record two constraints once each', run_id='r')
        run(s.drain())
        assert len(inputs) == 3
        assert s.task_contexts.read()['shared']['body'].count('constraint 1') == 1
    finally:
        s.close()


def test_unknown_context_error_is_visible_to_the_model(tmp_path):
    calls = 0
    async def model(arguments, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'tool_calls': [{'id': 'missing', 'name': 'load_context',
                'arguments': {'context_id': 'does-not-exist'}}]}
        assert 'Unknown context_id' in str(arguments['messages'])
        if calls == 2:
            return {'tool_calls': [{'id': 'corrected', 'name': 'load_context',
                'arguments': {'context_id': s.task_contexts.read()['active']}}]}
        receipt = next(m for m in arguments['messages'] if m.get('tool_call_id') == 'corrected')
        assert '"saved": true' in receipt['content']
        return {'content': 'The existing context remains active.'}
    s = make_session(tmp_path, model)
    try:
        s.submit('Try an invalid context', run_id='r')
        run(s.drain())
        state = s.task_contexts.read()
        assert len(state['documents']) == 1
        assert state['active'] in state['documents']
    finally:
        s.close()


def test_switch_retains_current_request_control_receipts(tmp_path):
    inputs = []
    async def model(arguments, context):
        inputs.append(deepcopy(arguments['messages']))
        step = len(inputs)
        plan = {
            1: ('load_context', {'context_id': 'missing'}),
            2: ('unload_context', {'summary': 'B paused'}),
            3: ('load_context', {'context_id': a}),
        }
        if step in plan:
            name, args = plan[step]
            return {'tool_calls': [{'id': f'switch{step}', 'name': name, 'arguments': args}]}
        receipts = {m.get('tool_call_id') for m in arguments['messages'] if m.get('role') == 'tool'}
        assert {'switch1', 'switch2', 'switch3'} <= receipts
        return {'content': 'Recovered A after the invalid ID.'}
    s = make_session(tmp_path, model)
    try:
        a = s.task_contexts.ensure('a')
        command(s, 'unload_context', {'summary': 'A paused'}, 'pause-a')
        s.task_contexts.ensure('b')
        s.submit('Try missing then return to A', run_id='r')
        run(s.drain())
        assert len(inputs) == 4
        assert s.task_contexts.read()['active'] == a
    finally:
        s.close()
