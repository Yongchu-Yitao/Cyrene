from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from agent.context import (
    ContextStoreRouter,
    ContextTreeStore,
    ContextValueError,
    NodeHasChildrenError,
    NodeNotFoundError,
    RootDeletionError,
    TreeNotFoundError,
)


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(microseconds=1)
        return value


def test_tree_structure_queries_and_time_ordering(tmp_path):
    store = ContextStoreRouter(tmp_path / "context", clock=AdvancingClock())
    tree = store.create_tree({"scope": "shared"}, tree_id="tree", root_id="root")
    second = store.mount(tree.id, tree.root_id, "second", node_id="node-b")
    first = store.mount(tree.id, tree.root_id, "first", node_id="node-a")
    leaf = store.mount(tree.id, first.id, {"leaf": True}, node_id="leaf")

    assert store.is_root(tree.id, tree.root_id) is True
    assert store.is_root(tree.id, first.id) is False
    assert store.has_child(tree.id, tree.root_id) is True
    assert store.has_child(tree.id, second.id) is False
    assert store.get_parent(tree.id, tree.root_id) is None
    assert store.get_parent(tree.id, leaf.id) == first
    assert [node.id for node in store.get_children(tree.id, tree.root_id)] == [second.id, first.id]
    assert [node.id for node in store.get_path(tree.id, leaf.id)] == [tree.root_id, first.id, leaf.id]
    assert [node.id for node in store.get_subtree(tree.id, tree.root_id)] == [
        tree.root_id,
        second.id,
        first.id,
        leaf.id,
    ]

    store.close()


def test_values_are_materialized_and_update_preserves_creation_time(tmp_path):
    clock = AdvancingClock()
    store = ContextStoreRouter(tmp_path / "context", clock=clock)
    changes = []
    unsubscribe = store.subscribe(changes.append)
    source = {"items": ["original"]}
    tree = store.create_tree(source, tree_id="tree", root_id="root")
    source["items"].append("mutated outside store")

    stored = store.get_node(tree.id, tree.root_id)
    assert stored.value == {"items": ["original"]}

    updated = store.update_node(tree.id, tree.root_id, {"items": ["updated"]})
    assert updated.created_at == stored.created_at
    assert updated.updated_at > stored.updated_at
    assert store.get_node(tree.id, tree.root_id).value == {"items": ["updated"]}
    assert [(change.action, change.node_id) for change in changes] == [
        ("mount", "root"),
        ("update", "root"),
    ]

    unsubscribe()
    store.update_node(tree.id, tree.root_id, "not delivered")
    assert len(changes) == 2
    store.close()


def test_listener_filter_and_failures_do_not_rollback_commits(tmp_path, caplog):
    store = ContextStoreRouter(tmp_path / "context")
    first = store.create_tree(tree_id="first", root_id="first-root")
    second = store.create_tree(tree_id="second", root_id="second-root")
    observed = []
    store.subscribe(observed.append, tree_id=first.id)

    def failing_listener(_change):
        raise RuntimeError("listener failed")

    store.subscribe(failing_listener, tree_id=first.id)
    node = store.mount(first.id, first.root_id, "visible", node_id="first-child")
    store.mount(second.id, second.root_id, "hidden", node_id="second-child")

    assert [(change.tree_id, change.node_id) for change in observed] == [(first.id, node.id)]
    assert store.get_node(first.id, node.id).value == "visible"
    assert "Context change listener failed" in caplog.text
    store.close()


def test_listener_can_delete_the_changed_tree_after_commit(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="tree", root_id="root")

    def delete_after_update(change):
        if change.action == "update":
            store.delete_tree(change.tree_id)

    store.subscribe(delete_after_update, tree_id=tree.id)
    store.update_node(tree.id, tree.root_id, "updated")

    with pytest.raises(TreeNotFoundError):
        store.get_tree(tree.id)
    store.close()


def test_delete_node_requires_explicit_recursive_delete(tmp_path):
    store = ContextStoreRouter(tmp_path / "context", clock=AdvancingClock())
    changes = []
    tree = store.create_tree(tree_id="tree", root_id="root")
    parent = store.mount(tree.id, tree.root_id, "parent", node_id="parent")
    child = store.mount(tree.id, parent.id, "child", node_id="child")
    store.subscribe(changes.append)

    with pytest.raises(RootDeletionError):
        store.delete_node(tree.id, tree.root_id)
    with pytest.raises(NodeHasChildrenError):
        store.delete_node(tree.id, parent.id)

    store.delete_node(tree.id, parent.id, recursive=True)

    with pytest.raises(NodeNotFoundError):
        store.get_node(tree.id, parent.id)
    with pytest.raises(NodeNotFoundError):
        store.get_node(tree.id, child.id)
    assert store.has_child(tree.id, tree.root_id) is False
    assert changes[-1].action == "delete"
    assert changes[-1].deleted_node_ids == (parent.id, child.id)
    store.close()


def test_delete_tree_and_persistence_across_reopen(tmp_path):
    directory = tmp_path / "context"
    with ContextStoreRouter(directory) as store:
        tree = store.create_tree({"root": True}, tree_id="tree", root_id="root")
        store.mount(tree.id, tree.root_id, "child", node_id="child")
        tree_database = store.tree_database_path(tree.id)

    with ContextStoreRouter(directory) as reopened:
        assert reopened.get_tree("tree") == tree
        assert [node.id for node in reopened.get_subtree("tree", "root")] == ["root", "child"]
        reopened.delete_tree("tree")
        assert not tree_database.exists()
        with pytest.raises(TreeNotFoundError):
            reopened.get_tree("tree")


def test_rejects_non_json_values(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    with pytest.raises(ContextValueError):
        store.create_tree({"not-json": object()})
    assert not list((tmp_path / "context" / "trees").rglob("*.sqlite3"))
    store.close()


def test_each_tree_has_an_isolated_database(tmp_path):
    store = ContextStoreRouter(tmp_path / "context")
    first = store.create_tree(tree_id="first", root_id="first-root")
    second = store.create_tree(tree_id="second", root_id="second-root")

    first_database = store.tree_database_path(first.id)
    second_database = store.tree_database_path(second.id)

    assert first_database != second_database
    assert first_database.exists()
    assert second_database.exists()
    with sqlite3.connect(first_database) as connection:
        assert connection.execute(
            "SELECT tree_id FROM context_tree_metadata WHERE singleton = 1"
        ).fetchone() == (first.id,)
    with sqlite3.connect(second_database) as connection:
        assert connection.execute(
            "SELECT tree_id FROM context_tree_metadata WHERE singleton = 1"
        ).fetchone() == (second.id,)
    store.close()


def test_lru_reopens_evicted_tree_connections(tmp_path):
    store = ContextStoreRouter(tmp_path / "context", max_open_trees=1)
    first = store.create_tree(tree_id="first", root_id="first-root")
    second = store.create_tree(tree_id="second", root_id="second-root")

    first_child = store.mount(first.id, first.root_id, "first")
    second_child = store.mount(second.id, second.root_id, "second")

    assert store.get_node(first.id, first_child.id).value == "first"
    assert store.get_node(second.id, second_child.id).value == "second"
    store.close()


def test_different_tree_writes_do_not_share_a_router_lock(tmp_path, monkeypatch):
    store = ContextStoreRouter(tmp_path / "context")
    first = store.create_tree(tree_id="first", root_id="first-root")
    second = store.create_tree(tree_id="second", root_id="second-root")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    errors = []
    original_mount = ContextTreeStore.mount

    def blocking_mount(tree_store, parent_id, value, *, node_id=None):
        if tree_store.tree.id == first.id:
            first_entered.set()
            if not release_first.wait(2):
                raise TimeoutError("first tree was not released")
        return original_mount(tree_store, parent_id, value, node_id=node_id)

    monkeypatch.setattr(ContextTreeStore, "mount", blocking_mount)

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
        assert first_entered.wait(1)
        second_thread.start()
        assert second_finished.wait(1)
    finally:
        release_first.set()
        first_thread.join(2)
        if second_thread.ident is not None:
            second_thread.join(2)

    assert not errors
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    store.close()
