"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from agent.context import ContextStoreRouter
from agent.hook import (
    CONTEXT_CHANGE,
    CONTEXT_USED,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    TURN_START,
    HookBlocked,
    HookRegistration,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_every_tree_owns_an_independent_hook_set(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        first = store.create_tree(tree_id="first", root_id="first-root")
        second = store.create_tree(tree_id="second", root_id="second-root")
        first_hooks = store.hooks_for(first.id)
        second_hooks = store.hooks_for(second.id)
        observed = []
        first_hooks.register(CONTEXT_CHANGE, lambda event: observed.append(event.tree_id))

        store.mount(first.id, first.root_id, "first")
        store.mount(second.id, second.root_id, "second")
        await first_hooks.drain()

        assert first_hooks is store.get_hooks(first.id)
        assert first_hooks is not second_hooks
        assert observed == [first.id]
        store.close()

    run(scenario())


def test_root_only_is_a_hook_filter_and_plugins_control_mounting(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)
        root_changes = []

        def plugin(event):
            root_changes.append(event.payload.action)
            return {"not": "automatically mounted"}

        hooks.register(CONTEXT_CHANGE, plugin, root_only=True)
        child = store.mount(tree.id, tree.root_id, "child", node_id="child")
        store.update_node(tree.id, child.id, "updated child")
        store.update_node(tree.id, tree.root_id, "updated root")
        await hooks.drain()

        assert root_changes == ["update"]
        assert [node.id for node in store.get_children(tree.id, tree.root_id)] == ["child"]
        store.close()

    run(scenario())


def test_hook_plugin_can_mutate_the_same_tree_without_lock_reentry(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)

        def plugin(event):
            if event.payload.action == "update":
                store.mount(tree.id, event.payload.node_id, "plugin result", node_id="result")

        hooks.register(CONTEXT_CHANGE, plugin)
        store.update_node(tree.id, tree.root_id, "trigger")
        await hooks.drain()

        assert store.get_node(tree.id, "result").value == "plugin result"
        store.close()

    run(scenario())


def test_context_used_reports_tree_tokens_and_normal_reads_do_not_emit(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        leaf = store.mount(tree.id, tree.root_id, "leaf", node_id="leaf")
        hooks = store.hooks_for(tree.id)
        observed = []
        roots = []
        hooks.register(CONTEXT_USED, lambda event: observed.append(event.payload))
        hooks.register(
            CONTEXT_USED,
            lambda event: roots.append(event.payload.node_id),
            root_only=True,
        )

        store.get_path(tree.id, leaf.id)
        assert observed == []

        usage = store.report_context_used(
            tree.id,
            leaf.id,
            300,
            token_limit=1200,
            node_tokens={tree.root_id: 100, leaf.id: 200},
        )
        store.report_context_used(tree.id, tree.root_id, 100, token_limit=1200)
        await hooks.drain()

        assert usage.tokens == 300
        assert usage.usage_ratio == 0.25
        assert usage.node_tokens == {tree.root_id: 100, leaf.id: 200}
        assert [item.node_id for item in observed] == [leaf.id, tree.root_id]
        assert roots == [tree.root_id]
        store.close()

    run(scenario())


def test_pre_tool_use_preserves_modify_block_and_failure_policies(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)
        seen = []

        def modify(event):
            arguments = dict(event.payload["tool"]["arguments"])
            arguments["checked"] = True
            return {"decision": "modify", "arguments": arguments}

        def observe_modified(event):
            seen.append(dict(event.payload["tool"]["arguments"]))
            return {"decision": "allow"}

        hooks.register(PRE_TOOL_USE, modify, matcher="Bash")
        hooks.register(PRE_TOOL_USE, observe_modified, matcher="B*")
        assert await hooks.pre_tool_use("Bash", {"command": "pwd"}) == {
            "command": "pwd",
            "checked": True,
        }
        assert seen == [{"command": "pwd", "checked": True}]

        hooks.register(
            PRE_TOOL_USE,
            lambda _event: (_ for _ in ()).throw(RuntimeError("unavailable")),
            hook_id="fail-open",
        )
        assert (await hooks.pre_tool_use("Read", {"path": "file"}))["path"] == "file"

        hooks.register(
            PRE_TOOL_USE,
            lambda _event: {"decision": "block", "reason": "denied"},
            hook_id="block",
            matcher="Delete",
        )
        with pytest.raises(HookBlocked, match="denied"):
            await hooks.pre_tool_use("Delete", {"path": "file"})

        hooks.register(
            PRE_TOOL_USE,
            lambda _event: (_ for _ in ()).throw(RuntimeError("guard failed")),
            hook_id="fail-closed",
            matcher="Upload",
            failure_policy="block",
        )
        with pytest.raises(HookBlocked, match="guard failed"):
            await hooks.pre_tool_use("Upload", {"path": "file"})
        store.close()

    run(scenario())


def test_post_tool_and_lifecycle_compatibility_hooks(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)
        events = []

        hooks.register(SESSION_START, lambda _event: {
            "context": "first",
            "context_kind": "memory",
            "context_source": "test.memory",
        })
        hooks.register(SESSION_START, lambda _event: "second")
        hooks.register(
            SESSION_START,
            lambda _event: {"context": "soul", "context_position": "top"},
        )
        hooks.register(
            SESSION_START,
            lambda _event: {"context": "base", "context_position": "system"},
        )
        hooks.register(POST_TOOL_USE, lambda event: events.append(event))
        hooks.register(SESSION_END, lambda event: events.append(event))
        hooks.register(STOP, lambda event: events.append(event))
        hooks.register(TURN_START, lambda event: {"context": event.payload["turn"]})

        assert await hooks.session_start() == "base\n\nsoul\n\nfirst\n\nsecond"
        session_mounts = await hooks.session_start_mounts()
        memory_mount = next(
            mount for mount in session_mounts if mount["context"] == "first"
        )
        assert memory_mount["context_kind"] == "memory"
        assert memory_mount["context_source"] == "test.memory"
        assert await hooks.turn_start({"turn": "dynamic"}) == "dynamic"
        await hooks.post_tool_use(
            "Read",
            {"path": "file"},
            "contents",
            success=True,
        )
        await hooks.session_end({"status": "completed"})
        await hooks.stop("cancelled")

        assert [event.name for event in events] == [POST_TOOL_USE, SESSION_END, STOP]
        assert events[0].payload["result"]["value"] == "contents"
        assert events[2].payload["reason"] == "cancelled"
        store.close()

    run(scenario())


def test_hook_failure_does_not_rollback_context_commit(tmp_path, caplog):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)

        def fail(_event):
            raise RuntimeError("plugin failed")

        hooks.register(CONTEXT_CHANGE, fail)
        node = store.mount(tree.id, tree.root_id, "committed", node_id="child")
        await hooks.drain()

        assert store.get_node(tree.id, node.id).value == "committed"
        assert "Hook Plugin failed" in caplog.text
        store.close()

    run(scenario())


def test_delete_hook_contains_surviving_parent_id(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        child = store.mount(tree.id, tree.root_id, "child", node_id="child")
        hooks = store.hooks_for(tree.id)
        changes = []
        hooks.register(CONTEXT_CHANGE, lambda event: changes.append(event.payload))

        store.delete_node(tree.id, child.id)
        await hooks.drain()

        assert changes[-1].action == "delete"
        assert changes[-1].parent_id == tree.root_id
        store.close()

    run(scenario())


def test_blocked_hook_on_one_tree_does_not_hold_another_tree_lock(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    first = store.create_tree(tree_id="first", root_id="first-root")
    second = store.create_tree(tree_id="second", root_id="second-root")
    entered = threading.Event()
    release = threading.Event()
    second_finished = threading.Event()
    errors = []

    def wait_after_commit(_event):
        entered.set()
        if not release.wait(2):
            raise TimeoutError("hook was not released")

    store.hooks_for(first.id).register(CONTEXT_CHANGE, wait_after_commit)

    def write_first():
        try:
            store.mount(first.id, first.root_id, "first")
        except Exception as exc:
            errors.append(exc)

    def write_second():
        try:
            store.mount(second.id, second.root_id, "second")
        except Exception as exc:
            errors.append(exc)
        finally:
            second_finished.set()

    first_thread = threading.Thread(target=write_first)
    second_thread = threading.Thread(target=write_second)
    first_thread.start()
    try:
        assert entered.wait(1)
        second_thread.start()
        assert second_finished.wait(1)
    finally:
        release.set()
        first_thread.join(2)
        if second_thread.ident is not None:
            second_thread.join(2)

    assert not errors
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    store.close()


def test_context_hooks_run_in_tree_queue_order(tmp_path):
    async def scenario():
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(tree_id="tree", root_id="root")
        hooks = store.hooks_for(tree.id)
        observed = []

        async def plugin(event):
            if event.payload.action != "update":
                return
            observed.append(("start", event.payload.node_id))
            if event.payload.node_id == "first":
                await asyncio.sleep(0.02)
            observed.append(("end", event.payload.node_id))

        hooks.register(CONTEXT_CHANGE, plugin, hook_id="ordered", plugin_id="ordered")
        first = store.mount(tree.id, tree.root_id, "first", node_id="first")
        second = store.mount(tree.id, tree.root_id, "second", node_id="second")
        store.update_node(tree.id, first.id, "first updated")
        store.update_node(tree.id, second.id, "second updated")
        await hooks.drain()

        assert observed == [
            ("start", first.id),
            ("end", first.id),
            ("start", second.id),
            ("end", second.id),
        ]
        store.close()

    run(scenario())


def test_hook_bindings_share_tree_database_and_restore(tmp_path):
    directory = tmp_path / "context"
    observed = []

    def plugin(event):
        observed.append(event.payload.node_id)

    with ContextStoreRouter(directory) as store:
        tree = store.create_tree(tree_id="tree", root_id="root")
        store.hooks_for(tree.id).register(
            CONTEXT_CHANGE,
            plugin,
            hook_id="persistent-hook",
            plugin_id="persistent-plugin",
        )
        database = store.tree_database_path(tree.id)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT hook_id, plugin_id FROM hook_bindings"
        ).fetchall() == [("persistent-hook", "persistent-plugin")]

    async def reopened_scenario():
        reopened = ContextStoreRouter(
            directory,
            plugins={"persistent-plugin": plugin},
        )
        hooks = reopened.hooks_for("tree")
        assert hooks.list()[0].plugin_id == "persistent-plugin"
        reopened.update_node("tree", "root", "updated")
        await hooks.drain()
        assert observed == ["root"]
        reopened.close()

    run(reopened_scenario())


def test_blocked_delivery_resumes_when_plugin_is_restored(tmp_path):
    directory = tmp_path / "context"

    def original(_event):
        return None

    with ContextStoreRouter(directory) as store:
        tree = store.create_tree(tree_id="tree", root_id="root")
        store.hooks_for(tree.id).register(
            CONTEXT_CHANGE,
            original,
            hook_id="persistent-hook",
            plugin_id="persistent-plugin",
        )

    async def block_without_implementation():
        reopened = ContextStoreRouter(directory)
        hooks = reopened.hooks_for("tree")
        reopened.update_node("tree", "root", "waiting")
        await hooks.drain()
        with sqlite3.connect(reopened.tree_database_path("tree")) as connection:
            assert connection.execute(
                "SELECT status FROM hook_queue"
            ).fetchall() == [("blocked",)]
        reopened.close()

    run(block_without_implementation())

    observed = []

    def restored(event):
        observed.append(event.payload.node_id)

    async def resume_with_implementation():
        reopened = ContextStoreRouter(
            directory,
            plugins={"persistent-plugin": restored},
        )
        hooks = reopened.hooks_for("tree")
        await hooks.drain()
        assert observed == ["root"]
        reopened.close()

    run(resume_with_implementation())


def test_context_used_is_automatically_queued_after_node_updates(tmp_path):
    async def scenario():
        store = ContextStoreRouter(
            tmp_path / "context",
            token_counter=lambda value: len(str(value)),
            token_limit=100,
        )
        tree = store.create_tree("root", tree_id="tree", root_id="root")
        child = store.mount(tree.id, tree.root_id, "x", node_id="child")
        hooks = store.hooks_for(tree.id)
        observed = []
        hooks.register(CONTEXT_USED, lambda event: observed.append(event.payload))

        store.update_node(tree.id, child.id, "updated")
        await hooks.drain()

        assert len(observed) == 1
        assert observed[0].node_id == child.id
        assert observed[0].node_tokens == {tree.root_id: 4, child.id: 7}
        assert observed[0].tokens == 11
        assert observed[0].usage_ratio == 0.11
        store.close()

    run(scenario())


def test_initial_hook_observes_root_mount(tmp_path):
    async def scenario():
        observed = []

        def plugin(event):
            observed.append((event.payload.action, event.node_id, event.is_root))

        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(
            tree_id="tree",
            root_id="root",
            initial_hooks=(
                HookRegistration(
                    CONTEXT_CHANGE,
                    "root-plugin",
                    plugin,
                    hook_id="root-hook",
                    root_only=True,
                ),
            ),
        )
        await store.hooks_for(tree.id).drain()

        assert observed == [("mount", tree.root_id, True)]
        store.close()

    run(scenario())
