import sqlite3

import pytest

from cyrene.workbench.core_adapter.hook_listing import runtime_hook_listing_state


def test_missing_hook_store_is_uninitialized_without_creating_files(tmp_path):
    assert runtime_hook_listing_state(str(tmp_path / "db.sqlite3")) == {
        "system_hooks": [], "system_hooks_status": "uninitialized",
    }
    assert list(tmp_path.iterdir()) == []


def test_existing_empty_hook_store(tmp_path):
    root = tmp_path / "agent-state" / "context"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "index.sqlite3") as db:
        db.execute("CREATE TABLE context_tree_index (tree_id TEXT, database_path TEXT, created_at TEXT)")
    assert runtime_hook_listing_state(str(tmp_path / "db.sqlite3"))["system_hooks_status"] == "empty"


@pytest.mark.parametrize("broken_tree", [False, True])
def test_unreadable_hook_store_is_error_not_empty(tmp_path, broken_tree):
    root = tmp_path / "agent-state" / "context"
    root.mkdir(parents=True)
    index = root / "index.sqlite3"
    if broken_tree:
        with sqlite3.connect(index) as db:
            db.execute("CREATE TABLE context_tree_index (tree_id TEXT, database_path TEXT, created_at TEXT)")
            db.execute("INSERT INTO context_tree_index VALUES ('tree', 'tree.sqlite3', 'now')")
        (root / "tree.sqlite3").write_bytes(b"invalid database")
    else:
        index.write_bytes(b"invalid database")
    assert runtime_hook_listing_state(str(tmp_path / "db.sqlite3")) == {
        "system_hooks": [], "system_hooks_status": "error",
    }
