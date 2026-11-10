"""Catalog lifecycle through core tools, projection, persistence and forks."""
from copy import deepcopy

import pytest

from cyrene.core.context.capacity import ContextCapacityError
from cyrene.core.context.tasks import context_catalog, fork_task_state
from test_task_contexts import command, make_session, run


def edit(session, action, receipt, **args):
    return command(session, "edit_context_catalog", {"action": action, **args}, receipt)


def test_archive_search_restore_preserves_task_and_survives_reopen(tmp_path):
    s = make_session(tmp_path)
    try:
        a = s.task_contexts.ensure("a")
        command(s, "append_context", {"context_id": a, "content": "original evidence"}, "body")
        command(s, "switch_context", {"context_id": None, "title": "Current task", "summary": "A paused"}, "b")
        before = deepcopy(s.task_contexts.read())
        edit(s, "update", "metadata", context_id=a, title="旧研究", summary="已交付，来源 report.md")
        receipt = edit(s, "archive", "archive", context_id=a)
        assert edit(s, "archive", "archive", context_id=a) == receipt
        state = s.task_contexts.read()
        assert state["active"] == before["active"]
        assert state["documents"][a]["body"] == "original evidence"
        assert state["documents"][a]["last_active_at"] == before["documents"][a]["last_active_at"]
        assert a not in str(s._messages(s._leaf_id))
        assert "1 archived task contexts" in str(s._messages(s._leaf_id))
        assert a in {entry["id"] for entry in context_catalog(state)}
        result = edit(s, "search", "search", query="研究", status="archived")
        assert [entry["id"] for entry in result["entries"]] == [a]
        assert "body" not in result["entries"][0]
        assert s.task_contexts.read() == state
    finally:
        s.close()
    s = make_session(tmp_path)
    try:
        assert s.task_contexts.read()["documents"][a]["archived"]
        edit(s, "restore", "restore", context_id=a)
        assert a in str(s._messages(s._leaf_id))
        assert s.task_contexts.read()["active"] == before["active"]
        assert "original evidence" not in str(s._messages(s._leaf_id))
    finally:
        s.close()


def test_catalog_invalid_requests_and_failed_activation_are_atomic(tmp_path):
    s = make_session(tmp_path)
    try:
        a = s.task_contexts.ensure("a")
        for args in (
            {"action": "archive", "context_id": a},
            {"action": "restore", "context_id": "shared"},
            {"action": "update", "context_id": a},
            {"action": "update", "context_id": a, "title": "valid", "summary": " "},
            {"action": "update", "context_id": a, "summary": "x" * 201},
            {"action": "archive", "context_id": "missing"},
            {"action": "search", "context_id": a},
        ):
            state = s.task_contexts.read()
            with pytest.raises(ValueError):
                command(s, "edit_context_catalog", args, "invalid")
            assert s.task_contexts.read() == state
        command(s, "append_context", {"context_id": a, "content": "evidence " * 10000}, "large")
        command(s, "unload_context", {"summary": "saved progress"}, "pause")
        edit(s, "archive", "archive", context_id=a)
        state = s.task_contexts.read()
        s._configured_compaction_limit = lambda: 10000
        for name in ("load_context", "switch_context"):
            with pytest.raises(ContextCapacityError):
                command(s, name, {"context_id": a}, name)
            assert s.task_contexts.read() == state
        s._configured_compaction_limit = lambda: 1000000
        command(s, "load_context", {"context_id": a}, "fits")
        assert s.task_contexts.read()["documents"][a]["archived"] is False
        assert s.task_contexts.read()["active"] == a
    finally:
        s.close()


def test_search_pagination_filters_and_empty_legacy_metadata(tmp_path):
    s = make_session(tmp_path)
    try:
        ids = []
        for index in range(12):
            target = s.task_contexts.ensure(str(index))
            ids.append(target)
            edit(s, "update", f"name-{index}", context_id=target, title=f"Research {index}")
            command(s, "unload_context", {"summary": "analysis paused"}, f"pause-{index}")
            edit(s, "archive", f"archive-{index}", context_id=target)
        first = edit(s, "search", "search", query="RESEARCH", status="archived")
        assert len(first["entries"]) == 10
        second = edit(s, "search", "search", query="RESEARCH", status="archived", cursor=first["next_cursor"])
        assert [entry["id"] for entry in first["entries"] + second["entries"]] == ids
        assert second["next_cursor"] is None
        assert edit(s, "search", "search", status="unarchived")["entries"] == []
        with pytest.raises(ValueError, match="cursor"):
            edit(s, "search", "search", query="analysis", cursor=first["next_cursor"])
        legacy = s.task_contexts.ensure("untitled-legacy")
        assert edit(s, "search", "search", query=legacy)["entries"][0]["id"] == legacy
    finally:
        s.close()


def test_real_catalog_tool_handshake_and_fork_replay(tmp_path):
    seen = []

    async def model(arguments, context):
        seen.append(deepcopy(arguments["messages"]))
        step = len(seen)
        plan = {
            1: ("switch_context", {"context_id": None, "title": "Next", "summary": "Original paused"}),
            2: ("edit_context_catalog", {"action": "update", "context_id": original, "title": "Prior research", "summary": "Ready"}),
            3: ("edit_context_catalog", {"action": "archive", "context_id": original}),
            4: ("edit_context_catalog", {"action": "search", "query": "research", "status": "archived"}),
            5: ("switch_context", {"context_id": original, "summary": "Next paused"}),
        }
        if step in plan:
            name, args = plan[step]
            return {"tool_calls": [{"id": f"c{step}", "name": name, "arguments": args}]}
        receipts = {message.get("tool_call_id") for message in arguments["messages"] if message.get("role") == "tool"}
        assert "c5" in receipts
        return {"content": "Resumed original task"}

    s = make_session(tmp_path, model)
    try:
        original = s.task_contexts.ensure("original")
        command(s, "append_context", {"context_id": original, "content": "Original body"}, "body")
        s.submit("Organize and resume", run_id="organize")
        run(s.drain())
        assert len(seen) == 6
        path = s.store.get_path(s.tree.id, s._leaf_id)
        state = s.task_contexts.read()
        replayed = fork_task_state(path)
        assert context_catalog(replayed) == context_catalog(state)
        assert any(message.get("tool_call_id") == "c4" and original in message.get("content", "")
                   for message in seen[4])
        archive_index = next(index for index, node in enumerate(path)
                             if node.value.get("role") == "tool_results"
                             and any(result.get("call_id") == "c3" for result in node.value.get("results", [])))
        archived = fork_task_state(path[:archive_index + 1])
        assert archived["documents"][original]["archived"] is True
        assert state["active"] == original
        assert state["documents"][original]["archived"] is False
        assert state["documents"][original]["body"] == "Original body"
    finally:
        s.close()
