"""Consistent graph reads over leased context stores.

The graph is a read model of the context tree. Keep its snapshot transaction
here rather than expanding the mutation-oriented store and router interfaces.
"""
from __future__ import annotations


def _version(store) -> tuple[str, int]:
    row = store._connection.execute(
        "SELECT incarnation, revision FROM context_graph_revision WHERE singleton = 1"
    ).fetchone()
    return str(row[0]), int(row[1])


def graph_version(router, tree_id: str) -> tuple[str, int]:
    with router._lease(tree_id) as store, store._lock:
        store._ensure_available()
        return _version(store)


def graph_snapshot(router, tree_id: str):
    """Read nodes, commit pointer and version from one SQLite snapshot."""
    with router._lease(tree_id) as store, store._lock:
        store._ensure_available()
        store._connection.execute("BEGIN")
        try:
            version = _version(store)
            nodes = store.get_subtree(store.tree.root_id)
            leaf, _run = store.committed_state()
            return nodes, leaf, version
        finally:
            store._connection.execute("ROLLBACK")
