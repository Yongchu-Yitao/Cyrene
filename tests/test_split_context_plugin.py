from __future__ import annotations

import asyncio

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
