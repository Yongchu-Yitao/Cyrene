"""Small, transactionally maintained inputs to durable run-state selection.

Keep ancestry and branch semantics intact without reading prompts, tool output
or saved task documents just to display a task's status.
"""
from __future__ import annotations


_FIELDS = (
    "role", "run_id", "runtime_guidance", "trigger_model", "pending_question",
    "cancelled", "error", "session_end_complete", "answer_complete",
    "resume_model", "failure_kind", "retry_at", "cancel_reason", "metadata",
)


def _projection(source):
    keys = ",".join("'" + key + "'" for key in _FIELDS)
    # json_each parses the source once. Preserve JSON booleans (not integers),
    # explicit nulls and missing keys: the selector uses strict True/False.
    return f"""(SELECT json_group_object(key, CASE
        WHEN key = 'metadata' AND type = 'object'
            THEN json_object('retry', json(value -> '$.retry'))
        WHEN type IN ('object', 'array') THEN json(value)
        WHEN type = 'true' THEN json('true')
        WHEN type = 'false' THEN json('false')
        ELSE value END)
        FROM json_each({source}) WHERE key IN ({keys}))"""


def ensure_checkpoint_projection(connection):
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='context_checkpoint_nodes'"
    ).fetchone():
        return
    # Schema and backfill are atomic, including concurrent first readers and
    # older writers which know only about context_nodes.
    from .schema import transaction
    with transaction(connection):
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='context_checkpoint_nodes'"
        ).fetchone():
            return
        connection.execute("""CREATE TABLE context_checkpoint_nodes (
            node_id TEXT PRIMARY KEY REFERENCES context_nodes(node_id) ON DELETE CASCADE,
            parent_id TEXT,
            value_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        for event, name in (("INSERT", "insert"),
                            ("UPDATE OF value_json, parent_id, created_at, updated_at", "update")):
            connection.execute(f"""CREATE TRIGGER checkpoint_node_{name}
                AFTER {event} ON context_nodes BEGIN
                INSERT INTO context_checkpoint_nodes VALUES (
                    NEW.node_id, NEW.parent_id, {_projection('NEW.value_json')}, NEW.created_at, NEW.updated_at)
                ON CONFLICT(node_id) DO UPDATE SET parent_id=excluded.parent_id,
                    value_json=excluded.value_json, created_at=excluded.created_at, updated_at=excluded.updated_at;
                END""")
        connection.execute("""CREATE TRIGGER checkpoint_node_delete
            AFTER DELETE ON context_nodes BEGIN
            DELETE FROM context_checkpoint_nodes WHERE node_id=OLD.node_id;
            END""")
        connection.execute(f"""INSERT INTO context_checkpoint_nodes
            SELECT node_id, parent_id, {_projection('context_nodes.value_json')}, created_at, updated_at
            FROM context_nodes""")


def checkpoint_snapshot(router, tree_id):
    """Read projected values and commit pointer from the same SQLite snapshot."""
    with router._lease(tree_id) as store, store._lock:
        store._ensure_available()
        rows = store._connection.execute("""
            SELECT p.node_id, p.parent_id, p.value_json, p.created_at, p.updated_at,
                   m.committed_leaf_id
            FROM context_checkpoint_nodes p
            CROSS JOIN context_tree_metadata m WHERE m.singleton=1
        """).fetchall()
        return [store._node_from_row(row) for row in rows], str(rows[0]['committed_leaf_id'] or '') if rows else ''
