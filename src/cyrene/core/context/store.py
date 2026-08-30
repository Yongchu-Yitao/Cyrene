"""A SQLite-backed store that owns exactly one context tree."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..hook import CONTEXT_CHANGE, CONTEXT_USED, ContextUsed, HookEvent
from ..observability import log_operation
from .hook_store import TreeHookStore
from .errors import (
    ContextError,
    NodeHasChildrenError,
    NodeNotFoundError,
    RootDeletionError,
    TreeNotFoundError,
)
from .tree import ContextChange, ContextNode, ContextTree
from .schema import connect, ensure_tree_schema, transaction
from .serialization import Clock, decode_value, encode_value, normalize_time, utc_now

logger = logging.getLogger(__name__)

_MISSING = object()
TokenCounter = Callable[[Any], int]


def default_token_counter(value: Any) -> int:
    """Return a stable approximation until a model tokenizer is injected."""

    encoded = encode_value(value).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


class ContextTreeStore:
    """Own one tree database, one connection, and one write lock."""

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Clock = utc_now,
        token_counter: TokenCounter = default_token_counter,
        token_limit: int = 0,
        _tree_id: str | None = None,
        _root_id: str | None = None,
        _root_value: Any = _MISSING,
    ) -> None:
        self.database = Path(database).expanduser().resolve()
        self._clock = clock
        self._token_counter = token_counter
        self._token_limit = int(token_limit)
        self._lock = threading.RLock()
        self._closed = False
        self._deleted = False
        self._connection = connect(self.database)
        ensure_tree_schema(self._connection)
        self._backfill_token_counts()
        self.hooks = TreeHookStore(self._connection, self._lock)

        try:
            if _root_value is _MISSING:
                self._tree = self._load_tree()
            else:
                if not _tree_id or not _root_id:
                    raise ValueError("tree_id and root_id are required when creating a tree")
                self._tree = self._initialize_tree(_tree_id, _root_id, _root_value)
        except Exception as exc:
            log_operation(
                logger,
                "context.store",
                "open",
                phase="failed",
                level=logging.ERROR,
                exc_info=True,
                database=self.database,
                requested_tree_id=_tree_id,
                requested_root_id=_root_id,
                error=exc,
            )
            self.close()
            raise
        log_operation(
            logger,
            "context.store",
            "open",
            phase="completed",
            level=logging.DEBUG,
            database=self.database,
            tree_id=self._tree.id,
            root_id=self._tree.root_id,
            created=_root_value is not _MISSING,
            token_limit=self._token_limit,
        )

    @classmethod
    def create(
        cls,
        database: str | Path,
        *,
        tree_id: str,
        root_id: str,
        root_value: Any = None,
        clock: Clock = utc_now,
        token_counter: TokenCounter = default_token_counter,
        token_limit: int = 0,
    ) -> ContextTreeStore:
        return cls(
            database,
            clock=clock,
            token_counter=token_counter,
            token_limit=token_limit,
            _tree_id=str(tree_id),
            _root_id=str(root_id),
            _root_value=root_value,
        )

    def __enter__(self) -> ContextTreeStore:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    @property
    def tree(self) -> ContextTree:
        return self._tree

    @staticmethod
    def _new_node_id() -> str:
        return f"node_{uuid4().hex}"

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
        log_operation(
            logger,
            "context.store",
            "close",
            phase="completed",
            level=logging.DEBUG,
            database=self.database,
            tree_id=getattr(getattr(self, "_tree", None), "id", None),
        )

    def _ensure_available(self) -> None:
        if self._closed:
            log_operation(
                logger,
                "context.store",
                "availability_check",
                phase="failed",
                level=logging.ERROR,
                database=self.database,
                tree_id=getattr(getattr(self, "_tree", None), "id", None),
                reason="closed",
            )
            raise ContextError("context tree store is closed")
        if self._deleted:
            log_operation(
                logger,
                "context.store",
                "availability_check",
                phase="failed",
                level=logging.ERROR,
                database=self.database,
                tree_id=self._tree.id,
                reason="deleted",
            )
            raise TreeNotFoundError(f"context tree has been deleted: {self._tree.id}")

    def _now(self) -> datetime:
        return normalize_time(self._clock())

    def _count_tokens(self, value: Any) -> int:
        count = self._token_counter(value)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("token_counter must return a non-negative integer")
        return count

    def _backfill_token_counts(self) -> None:
        """Calculate counters once for databases created before token caching."""

        missing = self._connection.execute(
            "SELECT 1 FROM context_nodes "
            "WHERE self_token_count IS NULL OR path_token_count IS NULL LIMIT 1"
        ).fetchone()
        if missing is None:
            return
        rows = self._connection.execute(
            """
            WITH RECURSIVE ordered(node_id, parent_id, value_json, depth) AS (
                SELECT node_id, parent_id, value_json, 0
                FROM context_nodes WHERE parent_id IS NULL
                UNION ALL
                SELECT child.node_id, child.parent_id, child.value_json,
                       ordered.depth + 1
                FROM context_nodes AS child
                JOIN ordered ON child.parent_id = ordered.node_id
            )
            SELECT node_id, parent_id, value_json
            FROM ordered ORDER BY depth, node_id
            """
        ).fetchall()
        path_tokens: dict[str, int] = {}
        updates: list[tuple[int, int, str]] = []
        for row in rows:
            node_id = str(row["node_id"])
            parent_id = (
                str(row["parent_id"]) if row["parent_id"] is not None else None
            )
            self_tokens = self._count_tokens(decode_value(str(row["value_json"])))
            path_total = self_tokens + (path_tokens.get(parent_id, 0) if parent_id else 0)
            path_tokens[node_id] = path_total
            updates.append((self_tokens, path_total, node_id))
        if updates:
            with transaction(self._connection):
                self._connection.executemany(
                    "UPDATE context_nodes SET self_token_count = ?, "
                    "path_token_count = ? WHERE node_id = ?",
                    updates,
                )

    def _initialize_tree(self, tree_id: str, root_id: str, root_value: Any) -> ContextTree:
        encoded = encode_value(root_value)
        self_tokens = self._count_tokens(root_value)
        created_at = self._now()
        timestamp = created_at.isoformat()
        with transaction(self._connection):
            existing = self._connection.execute(
                "SELECT 1 FROM context_tree_metadata WHERE singleton = 1"
            ).fetchone()
            if existing is not None:
                raise ContextError(f"context tree database is already initialized: {self.database}")
            self._connection.execute(
                """
                INSERT INTO context_tree_metadata(singleton, tree_id, root_id, created_at)
                VALUES (1, ?, ?, ?)
                """,
                (tree_id, root_id, timestamp),
            )
            self._connection.execute(
                """
                INSERT INTO context_nodes(
                    node_id, parent_id, value_json, self_token_count,
                    path_token_count, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?)
                """,
                (root_id, encoded, self_tokens, self_tokens, timestamp, timestamp),
            )
        return ContextTree(tree_id, root_id, created_at)

    def _load_tree(self) -> ContextTree:
        row = self._connection.execute(
            """
            SELECT tree_id, root_id, created_at
            FROM context_tree_metadata
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            self.close()
            raise TreeNotFoundError(f"context tree database is not initialized: {self.database}")
        return ContextTree(
            id=str(row["tree_id"]),
            root_id=str(row["root_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def _node_from_row(self, row: sqlite3.Row) -> ContextNode:
        return ContextNode(
            id=str(row["node_id"]),
            tree_id=self._tree.id,
            parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
            value=decode_value(str(row["value_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _context_usage(
        self,
        node_id: str,
        time: datetime,
        *,
        include_node_tokens: bool,
    ) -> ContextUsed:
        row = self._connection.execute(
            "SELECT path_token_count FROM context_nodes WHERE node_id = ?",
            (str(node_id),),
        ).fetchone()
        if row is None:
            raise NodeNotFoundError(
                f"context node not found in tree {self._tree.id}: {node_id}"
            )
        tokens = int(row["path_token_count"] or 0)
        node_tokens: dict[str, int] = {}
        if include_node_tokens:
            rows = self._connection.execute(
            """
            WITH RECURSIVE ancestors(
                node_id, parent_id, self_token_count, depth
            ) AS (
                SELECT node_id, parent_id, self_token_count, 0
                FROM context_nodes
                WHERE node_id = ?
                UNION ALL
                SELECT parent.node_id, parent.parent_id, parent.self_token_count,
                       ancestors.depth + 1
                FROM context_nodes AS parent
                JOIN ancestors ON parent.node_id = ancestors.parent_id
            )
            SELECT node_id, self_token_count
            FROM ancestors
            ORDER BY depth DESC
            """,
            (str(node_id),),
            ).fetchall()
            node_tokens = {
                str(ancestor["node_id"]): int(ancestor["self_token_count"] or 0)
                for ancestor in rows
            }
        usage = ContextUsed(
            tree_id=self._tree.id,
            node_id=str(node_id),
            tokens=tokens,
            token_limit=self._token_limit,
            usage_ratio=(tokens / self._token_limit) if self._token_limit else 0.0,
            node_tokens=node_tokens,
            time=time,
        )
        log_operation(
            logger,
            "context.store",
            "calculate_usage",
            phase="completed",
            tree_id=self._tree.id,
            node_id=node_id,
            tokens=tokens,
            token_limit=self._token_limit,
            usage_ratio=usage.usage_ratio,
            node_tokens=node_tokens,
        )
        return usage

    def _enqueue_change(self, change: ContextChange, *, report_usage: bool = True) -> None:
        change_deliveries = self.hooks.enqueue(
            HookEvent(
                CONTEXT_CHANGE,
                change.tree_id,
                change.time,
                payload=change,
                node_id=change.node_id,
                is_root=change.node_id == self._tree.root_id,
            )
        )
        if report_usage:
            if self.hooks.has_enabled_hook(CONTEXT_USED):
                usage = self._context_usage(
                    change.node_id,
                    change.time,
                    include_node_tokens=(
                        self.hooks.context_used_node_tokens_required()
                    ),
                )
                usage_deliveries = self.hooks.enqueue(
                    HookEvent(
                        CONTEXT_USED,
                        usage.tree_id,
                        usage.time,
                        payload=usage,
                        node_id=usage.node_id,
                        is_root=usage.node_id == self._tree.root_id,
                    )
                )
            else:
                usage_deliveries = 0
        else:
            usage_deliveries = 0
        log_operation(
            logger,
            "context.store",
            "enqueue_change",
            phase="completed",
            tree_id=change.tree_id,
            node_id=change.node_id,
            context_action=change.action,
            change=change,
            context_change_deliveries=change_deliveries,
            context_used_deliveries=usage_deliveries,
        )

    def enqueue_initial_root(self) -> None:
        """Queue root notifications after initial Hooks have been installed."""

        with self._lock:
            self._ensure_available()
            change = ContextChange(
                self._tree.id,
                self._tree.root_id,
                "mount",
                self._tree.created_at,
            )
            with transaction(self._connection):
                self._enqueue_change(change)
        log_operation(
            logger,
            "context.store",
            "enqueue_initial_root",
            phase="completed",
            tree_id=self._tree.id,
            root_id=self._tree.root_id,
        )

    def enqueue_context_used(self, usage: ContextUsed) -> None:
        with self._lock, transaction(self._connection):
            deliveries = self.hooks.enqueue(
                HookEvent(
                    CONTEXT_USED,
                    usage.tree_id,
                    usage.time,
                    payload=usage,
                    node_id=usage.node_id,
                    is_root=usage.node_id == self._tree.root_id,
                )
            )
        log_operation(
            logger,
            "context.store",
            "enqueue_context_used",
            phase="completed",
            tree_id=usage.tree_id,
            node_id=usage.node_id,
            tokens=usage.tokens,
            token_limit=usage.token_limit,
            node_tokens=usage.node_tokens,
            deliveries=deliveries,
        )

    def _require_node_row(self, node_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT node_id, parent_id, value_json, self_token_count,
                   path_token_count, created_at, updated_at
            FROM context_nodes
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        if row is None:
            log_operation(
                logger,
                "context.store",
                "require_node",
                phase="failed",
                level=logging.DEBUG,
                tree_id=self._tree.id,
                node_id=node_id,
                reason="not_found",
            )
            raise NodeNotFoundError(
                f"context node not found in tree {self._tree.id}: {node_id}"
            )
        return row

    def mount(
        self,
        parent_id: str,
        value: Any,
        *,
        node_id: str | None = None,
    ) -> ContextNode:
        parent_id = str(parent_id)
        node_id = str(node_id or self._new_node_id())
        log_operation(
            logger,
            "context.store",
            "mount",
            phase="requested",
            tree_id=self._tree.id,
            parent_id=parent_id,
            node_id=node_id,
            value=value,
        )
        encoded = encode_value(value)
        self_tokens = self._count_tokens(value)
        created_at = self._now()
        timestamp = created_at.isoformat()
        with self._lock:
            self._ensure_available()
            try:
                with transaction(self._connection):
                    parent = self._require_node_row(parent_id)
                    parent_path_tokens = int(parent["path_token_count"] or 0)
                    self._connection.execute(
                        """
                        INSERT INTO context_nodes(
                            node_id, parent_id, value_json, self_token_count,
                            path_token_count, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            node_id,
                            parent_id,
                            encoded,
                            self_tokens,
                            parent_path_tokens + self_tokens,
                            timestamp,
                            timestamp,
                        ),
                    )
                    self._enqueue_change(
                        ContextChange(
                            self._tree.id,
                            node_id,
                            "mount",
                            created_at,
                            parent_id=parent_id,
                        )
                    )
            except sqlite3.IntegrityError as exc:
                raise ContextError(f"context node id already exists: {node_id}") from exc
        node = ContextNode(node_id, self._tree.id, parent_id, decode_value(encoded), created_at, created_at)
        log_operation(
            logger,
            "context.store",
            "mount",
            phase="completed",
            tree_id=self._tree.id,
            parent_id=parent_id,
            node_id=node_id,
            node=node,
        )
        return node

    def save_effect_result(
        self,
        assistant_node_id: str,
        call_id: str,
        result: Any,
    ) -> None:
        """Persist one in-flight tool result without rewriting its Assistant."""

        assistant_node_id = str(assistant_node_id)
        call_id = str(call_id)
        encoded = encode_value(result)
        timestamp = self._now().isoformat()
        with self._lock:
            self._ensure_available()
            with transaction(self._connection):
                self._require_node_row(assistant_node_id)
                self._connection.execute(
                    """
                    INSERT INTO context_effect_results(
                        assistant_node_id, call_id, result_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(assistant_node_id, call_id) DO UPDATE SET
                        result_json = excluded.result_json,
                        updated_at = excluded.updated_at
                    """,
                    (assistant_node_id, call_id, encoded, timestamp, timestamp),
                )

    def effect_results(self, assistant_node_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_available()
            rows = self._connection.execute(
                "SELECT call_id, result_json FROM context_effect_results "
                "WHERE assistant_node_id = ? ORDER BY created_at, call_id",
                (str(assistant_node_id),),
            ).fetchall()
        return {
            str(row["call_id"]): decode_value(str(row["result_json"]))
            for row in rows
        }

    def clear_effect_results(self, assistant_node_id: str) -> int:
        with self._lock:
            self._ensure_available()
            with transaction(self._connection):
                cursor = self._connection.execute(
                    "DELETE FROM context_effect_results WHERE assistant_node_id = ?",
                    (str(assistant_node_id),),
                )
        return max(0, cursor.rowcount)

    def update_node(self, node_id: str, value: Any) -> ContextNode:
        node_id = str(node_id)
        log_operation(
            logger,
            "context.store",
            "update_node",
            phase="requested",
            tree_id=self._tree.id,
            node_id=node_id,
            value=value,
        )
        encoded = encode_value(value)
        self_tokens = self._count_tokens(value)
        updated_at = self._now()
        timestamp = updated_at.isoformat()
        with self._lock:
            self._ensure_available()
            with transaction(self._connection):
                existing = self._require_node_row(node_id)
                token_delta = self_tokens - int(existing["self_token_count"] or 0)
                self._connection.execute(
                    "UPDATE context_nodes SET value_json = ?, self_token_count = ?, "
                    "updated_at = ? WHERE node_id = ?",
                    (encoded, self_tokens, timestamp, node_id),
                )
                if token_delta:
                    self._connection.execute(
                        """
                        WITH RECURSIVE descendants(node_id) AS (
                            SELECT node_id FROM context_nodes WHERE node_id = ?
                            UNION ALL
                            SELECT child.node_id
                            FROM context_nodes AS child
                            JOIN descendants ON child.parent_id = descendants.node_id
                        )
                        UPDATE context_nodes
                        SET path_token_count = path_token_count + ?
                        WHERE node_id IN (SELECT node_id FROM descendants)
                        """,
                        (node_id, token_delta),
                    )
                self._enqueue_change(
                    ContextChange(
                        self._tree.id,
                        node_id,
                        "update",
                        updated_at,
                        parent_id=(
                            str(existing["parent_id"])
                            if existing["parent_id"] is not None
                            else None
                        ),
                    )
                )
        node = ContextNode(
            node_id,
            self._tree.id,
            str(existing["parent_id"]) if existing["parent_id"] is not None else None,
            decode_value(encoded),
            datetime.fromisoformat(str(existing["created_at"])),
            updated_at,
        )
        log_operation(
            logger,
            "context.store",
            "update_node",
            phase="completed",
            tree_id=self._tree.id,
            node_id=node_id,
            node=node,
        )
        return node

    def replace_subtree(
        self,
        node_id: str,
        value: Any,
        *,
        expected_node_ids: tuple[str, ...] | None = None,
    ) -> tuple[ContextNode, tuple[str, ...]]:
        """Atomically replace one node and remove all of its descendants.

        ``expected_node_ids`` is an optimistic-concurrency guard.  Reflection
        captures a complete source subtree before doing asynchronous model
        work; the commit is rejected when that subtree changed in the
        meantime, so no newly-arrived context can be discarded accidentally.
        """

        node_id = str(node_id)
        encoded = encode_value(value)
        self_tokens = self._count_tokens(value)
        updated_at = self._now()
        timestamp = updated_at.isoformat()
        with self._lock:
            self._ensure_available()
            with transaction(self._connection):
                existing = self._require_node_row(node_id)
                token_delta = self_tokens - int(existing["self_token_count"] or 0)
                subtree = self.get_subtree(node_id)
                actual_ids = tuple(node.id for node in subtree)
                if expected_node_ids is not None and actual_ids != tuple(
                    str(item) for item in expected_node_ids
                ):
                    raise ContextError(
                        f"context subtree changed before replacement: {node_id}"
                    )
                deleted_ids = actual_ids[1:]
                if deleted_ids:
                    delete_change = ContextChange(
                        self._tree.id,
                        deleted_ids[0],
                        "delete",
                        updated_at,
                        deleted_node_ids=deleted_ids,
                        parent_id=node_id,
                    )
                    self._enqueue_change(delete_change, report_usage=False)
                    self._connection.execute(
                        "DELETE FROM context_nodes WHERE parent_id = ?",
                        (node_id,),
                    )
                self._connection.execute(
                    "UPDATE context_nodes SET value_json = ?, self_token_count = ?, "
                    "path_token_count = path_token_count + ?, updated_at = ? "
                    "WHERE node_id = ?",
                    (encoded, self_tokens, token_delta, timestamp, node_id),
                )
                update_change = ContextChange(
                    self._tree.id,
                    node_id,
                    "update",
                    updated_at,
                    parent_id=(
                        str(existing["parent_id"])
                        if existing["parent_id"] is not None
                        else None
                    ),
                )
                self._enqueue_change(update_change)
        node = ContextNode(
            node_id,
            self._tree.id,
            str(existing["parent_id"]) if existing["parent_id"] is not None else None,
            decode_value(encoded),
            datetime.fromisoformat(str(existing["created_at"])),
            updated_at,
        )
        log_operation(
            logger,
            "context.store",
            "replace_subtree",
            phase="completed",
            tree_id=self._tree.id,
            node_id=node_id,
            deleted_node_ids=deleted_ids,
        )
        return node, deleted_ids

    def get_node(self, node_id: str) -> ContextNode:
        normalized_id = str(node_id)
        with self._lock:
            self._ensure_available()
            row = self._require_node_row(normalized_id)
        node = self._node_from_row(row)
        log_operation(
            logger,
            "context.store",
            "get_node",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=normalized_id,
            node=node,
        )
        return node

    def is_root(self, node_id: str) -> bool:
        self.get_node(str(node_id))
        result = self._tree.root_id == str(node_id)
        log_operation(
            logger,
            "context.store",
            "is_root",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            result=result,
        )
        return result

    def has_child(self, node_id: str) -> bool:
        node_id = str(node_id)
        with self._lock:
            self._ensure_available()
            self._require_node_row(node_id)
            row = self._connection.execute(
                "SELECT 1 FROM context_nodes WHERE parent_id = ? LIMIT 1",
                (node_id,),
            ).fetchone()
        result = row is not None
        log_operation(
            logger,
            "context.store",
            "has_child",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            result=result,
        )
        return result

    def get_parent(self, node_id: str) -> ContextNode | None:
        with self._lock:
            self._ensure_available()
            row = self._require_node_row(str(node_id))
            if row["parent_id"] is None:
                log_operation(
                    logger,
                    "context.store",
                    "get_parent",
                    phase="completed",
                    level=logging.DEBUG,
                    tree_id=self._tree.id,
                    node_id=node_id,
                    parent=None,
                )
                return None
            parent = self._require_node_row(str(row["parent_id"]))
        result = self._node_from_row(parent)
        log_operation(
            logger,
            "context.store",
            "get_parent",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            parent=result,
        )
        return result

    def get_children(self, node_id: str) -> list[ContextNode]:
        node_id = str(node_id)
        with self._lock:
            self._ensure_available()
            self._require_node_row(node_id)
            rows = self._connection.execute(
                """
                SELECT node_id, parent_id, value_json, created_at, updated_at
                FROM context_nodes
                WHERE parent_id = ?
                ORDER BY created_at, node_id
                """,
                (node_id,),
            ).fetchall()
        result = [self._node_from_row(row) for row in rows]
        log_operation(
            logger,
            "context.store",
            "get_children",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            count=len(result),
            children=result,
        )
        return result

    def get_path(self, node_id: str) -> list[ContextNode]:
        node_id = str(node_id)
        with self._lock:
            self._ensure_available()
            self._require_node_row(node_id)
            rows = self._connection.execute(
                """
                WITH RECURSIVE ancestors(
                    node_id, parent_id, value_json, created_at, updated_at, depth
                ) AS (
                    SELECT node_id, parent_id, value_json, created_at, updated_at, 0
                    FROM context_nodes
                    WHERE node_id = ?
                    UNION ALL
                    SELECT parent.node_id, parent.parent_id, parent.value_json,
                           parent.created_at, parent.updated_at, ancestors.depth + 1
                    FROM context_nodes AS parent
                    JOIN ancestors ON parent.node_id = ancestors.parent_id
                )
                SELECT node_id, parent_id, value_json, created_at, updated_at
                FROM ancestors
                ORDER BY depth DESC
                """,
                (node_id,),
            ).fetchall()
        result = [self._node_from_row(row) for row in rows]
        log_operation(
            logger,
            "context.store",
            "get_path",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            count=len(result),
            path=result,
        )
        return result

    def get_subtree(self, node_id: str) -> list[ContextNode]:
        node_id = str(node_id)
        with self._lock:
            self._ensure_available()
            self._require_node_row(node_id)
            rows = self._connection.execute(
                """
                WITH RECURSIVE descendants(
                    node_id, parent_id, value_json, created_at, updated_at, sort_path
                ) AS (
                    SELECT node_id, parent_id, value_json, created_at, updated_at,
                           created_at || ':' || node_id
                    FROM context_nodes
                    WHERE node_id = ?
                    UNION ALL
                    SELECT child.node_id, child.parent_id, child.value_json,
                           child.created_at, child.updated_at,
                           descendants.sort_path || '/' || child.created_at || ':' || child.node_id
                    FROM context_nodes AS child
                    JOIN descendants ON child.parent_id = descendants.node_id
                )
                SELECT node_id, parent_id, value_json, created_at, updated_at
                FROM descendants
                ORDER BY sort_path
                """,
                (node_id,),
            ).fetchall()
        result = [self._node_from_row(row) for row in rows]
        log_operation(
            logger,
            "context.store",
            "get_subtree",
            phase="completed",
            level=logging.DEBUG,
            tree_id=self._tree.id,
            node_id=node_id,
            count=len(result),
            subtree=result,
        )
        return result

    def delete_node(self, node_id: str, *, recursive: bool = False) -> ContextChange:
        node_id = str(node_id)
        log_operation(
            logger,
            "context.store",
            "delete_node",
            phase="requested",
            tree_id=self._tree.id,
            node_id=node_id,
            recursive=recursive,
        )
        deleted_at = self._now()
        with self._lock:
            self._ensure_available()
            with transaction(self._connection):
                row = self._require_node_row(node_id)
                if row["parent_id"] is None:
                    raise RootDeletionError("a root node can only be removed by delete_tree")
                subtree = self.get_subtree(node_id)
                if len(subtree) > 1 and not recursive:
                    raise NodeHasChildrenError(f"context node has children: {node_id}")
                deleted_ids = tuple(node.id for node in subtree)
                change = ContextChange(
                    self._tree.id,
                    node_id,
                    "delete",
                    deleted_at,
                    deleted_node_ids=deleted_ids,
                    parent_id=str(row["parent_id"]),
                )
                self._enqueue_change(change, report_usage=False)
                self._connection.execute("DELETE FROM context_nodes WHERE node_id = ?", (node_id,))
        log_operation(
            logger,
            "context.store",
            "delete_node",
            phase="completed",
            tree_id=self._tree.id,
            node_id=node_id,
            recursive=recursive,
            change=change,
        )
        return change

    def mark_deleted(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_available()
            rows = self._connection.execute(
                "SELECT node_id FROM context_nodes ORDER BY created_at, node_id"
            ).fetchall()
            self._deleted = True
            node_ids = tuple(str(row["node_id"]) for row in rows)
        log_operation(
            logger,
            "context.store",
            "mark_deleted",
            phase="completed",
            tree_id=self._tree.id,
            node_ids=node_ids,
        )
        return node_ids

    def unmark_deleted(self) -> None:
        with self._lock:
            if not self._closed:
                self._deleted = False
        log_operation(
            logger,
            "context.store",
            "unmark_deleted",
            phase="completed",
            tree_id=self._tree.id,
            closed=self._closed,
        )
