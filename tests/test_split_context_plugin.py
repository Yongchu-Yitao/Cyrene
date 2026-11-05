from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from cyrene.core.context import ContextStoreRouter
from cyrene.core.plugin import PluginSetupContext


def _setup(tmp_path, *, session_id="chat-current", ui_instance_id="surface-1"):
    from cyrene.plugins.builtin.cyrene_split_context import plugin_pack

    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id=session_id, root_id="root")
    hooks = store.hooks_for(tree.id)
    assert plugin_pack.setup is not None
    plugin_pack.setup(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={
            "run_context": {
                "session_id": session_id,
                "ui_instance_id": ui_instance_id,
            },
        },
        services={},
    ))
    return store, hooks


def _snapshot(*, visible_session_id="chat-current", file_name="guide.md"):
    return {
        "ok": True,
        "surface": {
            "visible_session_id": visible_session_id,
            "visible_session_kind": "chat",
        },
        "root": {
            "node_id": "pane_workspace",
            "role": "group",
            "name": "Split view",
            "state": {"card_count": 2},
            "children": [
                {
                    "node_id": "pane-chat",
                    "name": "Current conversation",
                    "value_summary": "chat",
                    "state": {
                        "content_kind": "chat",
                        "content_id": "chat-current",
                        "side": "left",
                        "position": "top",
                    },
                },
                {
                    "node_id": "pane-file",
                    "name": file_name,
                    "value_summary": "file",
                    "state": {
                        "content_kind": "file",
                        "side": "right",
                        "position": "top",
                    },
                },
            ],
        },
    }


def test_split_context_mounts_latest_same_conversation_layout(tmp_path, monkeypatch):
    from cyrene.workbench.ui import ui_surface

    responses = [
        _snapshot(file_name="first.md"),
        _snapshot(file_name="later.md"),
    ]
    calls = []

    async def fake_request(ui_instance_id, method, args, *, timeout):
        calls.append((ui_instance_id, method, dict(args), timeout))
        return responses.pop(0)

    monkeypatch.setattr(ui_surface, "request", fake_request)
    store, hooks = _setup(tmp_path)
    try:
        first = asyncio.run(hooks.turn_start_mounts())
        second = asyncio.run(hooks.turn_start_mounts())
    finally:
        store.close()

    assert len(first) == len(second) == 1
    assert first[0]["context_kind"] == "current_workbench_split"
    assert first[0]["context_source"] == "cyrene_split_context"
    assert 'name="first.md" kind="file" side="right"' in first[0]["context"]
    assert "later.md" not in first[0]["context"]
    assert 'name="later.md" kind="file" side="right"' in second[0]["context"]
    assert calls == [
        (
            "surface-1",
            "snapshot",
            {
                "parent_node_id": "pane_workspace",
                "include": ["interactive", "text"],
                "max_depth": 2,
                "page_size": 12,
            },
            1.5,
        ),
        (
            "surface-1",
            "snapshot",
            {
                "parent_node_id": "pane_workspace",
                "include": ["interactive", "text"],
                "max_depth": 2,
                "page_size": 12,
            },
            1.5,
        ),
    ]


def test_split_context_ignores_a_different_visible_conversation(tmp_path, monkeypatch):
    from cyrene.workbench.ui import ui_surface

    async def fake_request(*_args, **_kwargs):
        return _snapshot(visible_session_id="chat-other")

    monkeypatch.setattr(ui_surface, "request", fake_request)
    store, hooks = _setup(tmp_path)
    try:
        assert asyncio.run(hooks.turn_start_mounts()) == ()
    finally:
        store.close()


def test_split_context_ignores_a_single_pane_layout(tmp_path, monkeypatch):
    from cyrene.workbench.ui import ui_surface

    async def fake_request(*_args, **_kwargs):
        snapshot = _snapshot()
        snapshot["root"]["state"]["card_count"] = 1
        snapshot["root"]["children"] = snapshot["root"]["children"][:1]
        return snapshot

    monkeypatch.setattr(ui_surface, "request", fake_request)
    store, hooks = _setup(tmp_path)
    try:
        assert asyncio.run(hooks.turn_start_mounts()) == ()
    finally:
        store.close()


def test_split_context_is_inert_without_a_current_ui_surface(tmp_path, monkeypatch):
    from cyrene.workbench.ui import ui_surface

    called = False

    async def fake_request(*_args, **_kwargs):
        nonlocal called
        called = True
        return _snapshot()

    monkeypatch.setattr(ui_surface, "request", fake_request)
    store, hooks = _setup(tmp_path, ui_instance_id="")
    try:
        assert asyncio.run(hooks.turn_start_mounts()) == ()
    finally:
        store.close()
    assert called is False


@pytest.mark.parametrize("failure", ["timeout", "exception", "other_session", "missing_surface", "malformed"])
def test_split_context_invalidates_only_current_branch_on_read_failure(tmp_path, monkeypatch, failure):
    from cyrene.workbench.ui import ui_surface

    async def fake_request(*_args, **_kwargs):
        if failure == "exception":
            raise RuntimeError("surface disconnected")
        if failure == "other_session":
            return _snapshot(visible_session_id="chat-other", file_name="PRIVATE_OTHER_FILE")
        if failure == "malformed":
            return {"ok": True, "surface": {"visible_session_id": "chat-current"}, "root": {}}
        return {"ok": False, "error": "surface_timeout"}

    monkeypatch.setattr(ui_surface, "request", fake_request)
    store, hooks = _setup(tmp_path, ui_instance_id="" if failure == "missing_surface" else "surface-1")
    try:
        previous = store.mount("chat-current", "root", {
            "role": "context", "context_kind": "current_workbench_split",
            "content": "Previously opened guide.md", "context_lifecycle": "turn", "run_id": "old"})
        user = store.mount("chat-current", previous.id, {"role": "user", "content": "continue"})
        result = asyncio.run(hooks.turn_start_mounts({"user_node_id": user.id}))
        assert len(result) == 1
        content = result[0]["context"]
        assert "could not be read for this turn" in content
        assert "does not mean those panes have been closed" in content
        assert "PRIVATE_OTHER_FILE" not in content
        assert store.get_node("chat-current", previous.id) == previous

        other_branch = store.mount("chat-current", "root", {"role": "user", "content": "fresh branch"})
        assert asyncio.run(hooks.turn_start_mounts({"user_node_id": other_branch.id})) == ()
    finally:
        store.close()


def test_split_context_lifecycle_preserves_history_and_model_prefix_after_reopen(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_split_context.context import setup_split_context
    from cyrene.workbench.ui import ui_surface
    from test_task_contexts import make_session, run

    captures = []
    layout = ["first.md"]

    async def model(arguments, _context):
        captures.append(deepcopy(arguments["messages"]))
        return {"content": "done"}

    async def fake_request(*_args, **_kwargs):
        if layout[0] == "unavailable":
            return {"ok": False, "error": "surface_timeout"}
        snapshot = _snapshot(visible_session_id="agent-session", file_name=layout[0])
        if layout[0] == "closed":
            snapshot["root"]["children"] = snapshot["root"]["children"][:1]
        return snapshot

    def open_session():
        session = make_session(tmp_path, model)
        setup_split_context(PluginSetupContext(
            data_directory=tmp_path / "data", plugin_directory=tmp_path / "plugins",
            workspace=tmp_path, tree=session.store, tree_id=session.tree.id,
            root_id=session.tree.root_id, hooks=session.hooks,
            data={"run_context": {"ui_instance_id": "surface-1"}}, services={},
        ))
        return session

    monkeypatch.setattr(ui_surface, "request", fake_request)
    session = open_session()
    saved_mounts = {}
    try:
        layouts = ["first.md", "later.md", "closed", "closed", "reopened.md",
                   "unavailable", "unavailable", "recovered.md"]
        for index, value in enumerate(layouts):
            if index == 2:
                session.close()
                session = open_session()
            layout[0] = value
            session.submit(f"Request {index}", run_id=f"run-{index}")
            run(session.drain())
            assert len(captures) == index + 1
            current = captures[-1]
            if index:
                assert current[:len(captures[-2])] == captures[-2]
            latest = current[-1]["content"]
            assert current[-1]["role"] == "user"
            assert latest.startswith(f"Request {index}")
            if index in {3, 6}:
                assert "<current_workbench_split>" not in latest
            elif value == "closed":
                assert "has no split view" in latest
                assert "do not treat their panes as currently open" in latest
            elif value == "unavailable":
                assert "could not be read for this turn" in latest
                assert "has no split view" not in latest
            else:
                assert value in latest
                assert "supersedes earlier split-layout snapshots" in latest

            path = session.store.get_path(session.tree.id, session._leaf_id)
            for node in path:
                if node.value.get("context_kind") == "current_workbench_split":
                    if node.id in saved_mounts:
                        assert node == saved_mounts[node.id]
                    saved_mounts[node.id] = node
        assert len(saved_mounts) == 6
    finally:
        session.close()
