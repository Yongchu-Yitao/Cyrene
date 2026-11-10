"""Grouped context search, bounded excerpts and the real core tool handshake."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from cyrene.core.context.search import search_context
from test_task_contexts import command, make_session, run


def node(id, **value):
    return SimpleNamespace(id=id, value=value)


def fixture_state():
    return {"active": "b", "shared": {"body": "Shared 邮箱 constraint"}, "documents": {
        "a": {"title": "邮箱方案", "summary": "邮箱验证", "body": "改用邮箱，保留邮箱提示。",
              "archived": True, "user_nodes": ["u"], "covered": ["u", "a1"],
              "messages": [{"role": "assistant", "content": "使用邮箱验证。"}]},
        "b": {"title": "Current", "body": "Other work", "messages": []},
    }}


def fixture_path():
    return [node("root", role="system", content="邮箱 system secret"),
            node("u", role="user", content="邮箱能替代短信吗？"),
            node("a1", role="assistant", task_context_id="a", content="使用邮箱验证。"),
            node("t", role="tool_results", task_context_id="a", content="邮箱 tool secret"),
            node("control", role="assistant", task_context_id="a", task_control=True, content="邮箱 control secret"),
            node("current", role="user", content="找邮箱")]


def test_grouped_sources_archive_shared_and_raw_compacted_history():
    state, path = fixture_state(), fixture_path()
    before = deepcopy(state)
    result = search_context(state, path, {"query": "邮箱"})
    assert state == before
    assert [group["context_id"] for group in result["contexts"]] == ["shared", "a"]
    group = result["contexts"][1]
    assert group["archived"] is True
    assert [m["source"] for m in group["matches"]] == ["title", "summary", "body", "message", "message"]
    assert [m.get("role") for m in group["matches"][-2:]] == ["user", "assistant"]
    assert "secret" not in str(result) and "找邮箱" not in str(result)
    assert result["next_cursor"] is None
    assert search_context(state, path, {"query": "邮箱", "context_id": "b"}) == {"contexts": [], "next_cursor": None}


def test_pagination_counts_excerpts_and_survives_search_calls():
    state, path = fixture_state(), fixture_path()
    args = {"query": "邮箱", "limit": 2}
    all_hits = []
    while True:
        result = search_context(state, path, args)
        page = [(g["context_id"], m) for g in result["contexts"] for m in g["matches"]]
        assert len(page) == 2
        all_hits.extend(page)
        if result["next_cursor"] is None:
            break
        args["cursor"] = result["next_cursor"]
        path.append(node("search", role="assistant", task_control=True, task_context_id="b", content="邮箱"))
        path.append(node("result", role="tool_results", task_context_id="b", content=str(result)))
    assert len(all_hits) == 6
    expected = search_context(state, fixture_path(), {"query": "邮箱"})
    assert all_hits == [(g["context_id"], m) for g in expected["contexts"] for m in g["matches"]]


def test_cursor_rejects_changed_query_limit_target_or_content():
    state, path = fixture_state(), fixture_path()
    args = {"query": "邮箱", "limit": 1}
    cursor = search_context(state, path, args)["next_cursor"]
    for changes in ({"query": "方案"}, {"limit": 2}, {"context_id": "a"}, {"cursor": "bad"}):
        with pytest.raises(ValueError, match="cursor"):
            search_context(state, path, {**args, "cursor": cursor, **changes})
    state["documents"]["a"]["body"] += " changed"
    with pytest.raises(ValueError, match="cursor"):
        search_context(state, path, {**args, "cursor": cursor})


@pytest.mark.parametrize("args", [{}, {"query": " "}, {"query": "a" * 201},
    {"query": "x", "context_id": "missing"}, {"query": "x", "limit": 0},
    {"query": "x", "limit": 31}, {"query": "x", "limit": True},
    {"query": "x", "limit": 1.5}, {"query": "x", "cursor": None},
    {"query": "x", "extra": True}])
def test_invalid_search_arguments(args):
    with pytest.raises(ValueError):
        search_context(fixture_state(), fixture_path(), args)


def test_snippets_are_bounded_original_text_and_merge_nearby_occurrences():
    state = fixture_state()
    state["documents"]["b"]["body"] = "x" * 200 + "Straße STRASSE" + "x" * 400 + "Straße" + "x" * 200
    result = search_context(state, [], {"query": "strasse", "context_id": "b"})
    matches = result["contexts"][0]["matches"]
    assert len(matches) == 2
    assert "Straße STRASSE" in matches[0]["snippet"]
    assert all(len(m["snippet"]) < 180 for m in matches)
    state["documents"]["b"]["body"] = "ß"
    result = search_context(state, [], {"query": "s", "context_id": "b"})
    assert result["contexts"][0]["matches"] == [{"source": "body", "snippet": "ß"}]


def test_real_core_search_keeps_context_state_and_handles_pagination(tmp_path):
    seen = []

    async def model(arguments, context):
        seen.append(deepcopy(arguments["messages"]))
        state = session.task_contexts.read()
        assert state == before
        if len(seen) == 1:
            return {"tool_calls": [{"id": "search1", "name": "search_context",
                                    "arguments": {"query": "邮箱", "limit": 1}}]}
        receipt = next(m for m in arguments["messages"] if m.get("tool_call_id") == "search1")
        result = json.loads(receipt["content"])["value"]
        if len(seen) == 2:
            return {"tool_calls": [{"id": "search2", "name": "search_context",
                                    "arguments": {"query": "邮箱", "limit": 1, "cursor": result["next_cursor"]}}]}
        second = next(m for m in arguments["messages"] if m.get("tool_call_id") == "search2")
        assert json.loads(second["content"])["success"] is True
        return {"content": "Found the earlier context."}

    session = make_session(tmp_path, model)
    try:
        target = session.task_contexts.ensure("old")
        command(session, "append_context", {"context_id": target, "content": "邮箱方案"}, "body")
        command(session, "unload_context", {"summary": "邮箱决定"}, "pause")
        command(session, "edit_context_catalog", {"action": "archive", "context_id": target}, "archive")
        before = session.task_contexts.read()
        session.submit("Find the earlier decision", run_id="search")
        run(session.drain())
        assert len(seen) == 3
        # The final answer may create its own task, but the search calls did not.
        assert session.task_contexts.read()["documents"][target] == before["documents"][target]
    finally:
        session.close()


def test_search_uses_current_branch_and_survives_reopen(tmp_path):
    session = make_session(tmp_path)
    try:
        target = session.task_contexts.ensure("old")
        root = session.tree.root_id
        current = session.store.mount(session.tree.id, root, {
            "role": "assistant", "task_context_id": target, "content": "邮箱选择：当前分支"})
        session.store.mount(session.tree.id, root, {
            "role": "assistant", "task_context_id": target, "content": "邮箱选择：其他分支"})
        session._leaf_id = current.id
        before = session.task_contexts.read()
        result = command(session, "search_context", {"query": "邮箱"}, "search")
        assert "当前分支" in str(result) and "其他分支" not in str(result)
        assert session.task_contexts.read() == before
    finally:
        session.close()
    session = make_session(tmp_path)
    try:
        session._leaf_id = current.id
        assert command(session, "search_context", {"query": "邮箱"}, "again") == result
    finally:
        session.close()
