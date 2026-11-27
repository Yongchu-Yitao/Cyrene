"""Consistent graph reads over leased context stores.

The graph is a read model of the context tree. Keep its snapshot transaction
here rather than expanding the mutation-oriented store and router interfaces.
"""
from __future__ import annotations

from contextlib import contextmanager


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


def path_nodes(store, leaf, *, cached_root=None):
    """Materialize a linear ancestry in depth order, sorting no payloads in SQLite."""
    from .errors import NodeNotFoundError
    with store._lock:
        store._ensure_available()
        rows = store._connection.execute("""
            WITH RECURSIVE ancestors(node_id, depth) AS (
                SELECT node_id, 0 FROM context_nodes WHERE node_id = ?
                UNION ALL
                SELECT node.parent_id, ancestors.depth + 1
                FROM ancestors JOIN context_nodes AS node USING (node_id)
                WHERE node.parent_id IS NOT NULL AND node.parent_id IS NOT ?
            )
            SELECT node.node_id, node.parent_id, node.value_json, node.created_at, node.updated_at, ancestors.depth
            FROM ancestors JOIN context_nodes AS node USING (node_id)
        """, (leaf, cached_root.id if cached_root is not None else None)).fetchall()
    if not rows:
        raise NodeNotFoundError(f"context node not found in tree {store.tree.id}: {leaf}")
    nodes = [None] * len(rows)
    for row in rows:
        nodes[-1 - row["depth"]] = store._node_from_row(row)
    if cached_root is not None and nodes[0].parent_id == cached_root.id:
        nodes.insert(0, cached_root)
    return nodes


class ModelPath:
    """Share materialized nodes only during an active group of related reads."""

    def __init__(self):
        self.users = 0
        self.version = None
        self.nodes = ()
        self.positions = {}
        self.changed = {}
        self.append_base = None

    def release_nodes(self):
        self.nodes = ()
        self.positions.clear()
        self.changed.clear()

    def record(self, version, node_id, parent_id, *, appended=False):
        if self.version != (version[0], version[1] - 1):
            self.release_nodes()
            self.append_base = version[1]
        elif not appended:
            self.append_base = version[1]
        self.version = version
        if len(self.changed) >= 256:
            self.release_nodes()
        if self.nodes:
            self.changed[node_id] = parent_id

    def read(self, store, leaf, version):
        if self.version != version:
            self.release_nodes()
            self.append_base = version[1]
            self.version = version
        if not self.users:
            return tuple(store.get_path(leaf))
        if self.nodes and not self.changed and self.nodes[-1].id == leaf:
            return self.nodes
        parent = self.changed.get(leaf)
        if leaf in self.positions:
            nodes = list(self.nodes[:self.positions[leaf] + 1])
        elif parent in self.positions:
            nodes = [*self.nodes[:self.positions[parent] + 1], store.get_node(leaf)]
        elif self.nodes and self.nodes[0].id not in self.changed:
            nodes = path_nodes(store, leaf, cached_root=self.nodes[0])
            self.changed.clear()
        else:
            nodes = store.get_path(leaf)
            self.changed.clear()
        for node_id in self.changed.keys() & self.positions.keys():
            index = self.positions[node_id]
            if index < len(nodes) and nodes[index].id == node_id:
                nodes[index] = store.get_node(node_id)
        self.nodes = tuple(nodes)
        self.positions = {node.id: index for index, node in enumerate(nodes)}
        self.changed.clear()
        return self.nodes


@contextmanager
def model_read_scope(router, tree_id):
    """Pin a path for related readers. The last reader releases all raw payloads."""
    with router._lease(tree_id) as store, store._lock:
        if store._model_path is None:
            store._model_path = ModelPath()
        cache = store._model_path
        cache.users += 1
    # Hooks may close or evict a tree. Hold neither its lease nor its lock
    # across caller code. A reopened store has its own revision and cache.
    try:
        yield cache
    finally:
        with store._lock:
            cache.users -= 1
            if not cache.users:
                cache.release_nodes()


def model_path_snapshot(router, tree_id, leaf):
    """Read ancestry and its append-only revision range in one database snapshot."""
    with router._lease(tree_id) as store, store._lock:
        store._ensure_available()
        if store._model_path is None:
            store._model_path = ModelPath()
        store._connection.execute("BEGIN")
        try:
            version = _version(store)
            cache = store._model_path
            return cache.read(store, leaf, version), version, cache.append_base
        finally:
            store._connection.execute("ROLLBACK")


def model_path(router, tree_id, leaf):
    from .router import ContextStoreRouter
    if not isinstance(router, ContextStoreRouter):
        return router.get_path(tree_id, leaf)
    nodes, _, _ = model_path_snapshot(router, tree_id, leaf)
    return nodes


def model_root_value(router, tree_id):
    """Read task metadata without walking a branch or shortening the hot path."""
    with router._lease(tree_id) as store, store._lock:
        root_id = store.tree.root_id
        cache = store._model_path
        if cache and cache.nodes and root_id not in cache.changed and cache.version == _version(store):
            return cache.nodes[0].value
        return store.get_node(root_id).value


def latest_path_value(router, tree_id, leaf, **fields):
    """Return an isolated matching mount to a hook without exporting a cached node."""
    from copy import deepcopy
    if leaf:
        path = model_path(router, tree_id, leaf)
        for node in reversed(path):
            if all(node.value.get(key) == value for key, value in fields.items()):
                return deepcopy(node.value)
    return None
