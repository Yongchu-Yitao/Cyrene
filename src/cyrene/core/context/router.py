"""Route context operations to isolated single-tree SQLite stores."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..hook import ContextUsed, Hook, HookRegistration, HookSet, PluginRegistry
from ..hook.hook import HookPlugin
from ..hook.storage import QueuedHookEvent
from ..observability import log_operation
from .errors import ContextError, TreeNotFoundError
from .tree import ContextChange, ContextNode, ContextTree
from .publisher import ChangeListener, ChangePublisher
from .schema import connect, ensure_index_schema, transaction
from .serialization import Clock, normalize_time, utc_now
from .store import ContextTreeStore, TokenCounter, default_token_counter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _CachedTree:
    store: ContextTreeStore
    users: int = 0


class _TreeHookPersistence:
    """Lease one tree store for every short Hook persistence operation."""

    def __init__(self, router: ContextStoreRouter, tree_id: str) -> None:
        self._router = router
        self._tree_id = str(tree_id)

    def list_hooks(self) -> tuple[Hook, ...]:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.list_hooks()

    def recover(self) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.recover()

    def save_hook(self, hook: Hook) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.save_hook(hook)

    def delete_hook(self, hook_id: str) -> bool:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.delete_hook(hook_id)

    def claim_next(self) -> QueuedHookEvent | None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.claim_next()

    def complete(self, sequence: int) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.complete(sequence)

    def fail(self, sequence: int, error: str) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.fail(sequence, error)

    def block(self, sequence: int, error: str) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.block(sequence, error)

    def release(self, sequence: int) -> None:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            store.hooks.release(sequence)

    def requeue_blocked(self, plugin_id: str) -> int:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.requeue_blocked(plugin_id)

    def retry_failed(self) -> int:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.retry_failed()

    def has_work(self) -> bool:
        with self._router._lease(self._tree_id, allow_deleting=True) as store:
            return store.hooks.has_work()


class ContextStoreRouter:
    """Map every tree to an isolated database, connection, and write lock."""

    def __init__(
        self,
        directory: str | Path,
        *,
        clock: Clock = utc_now,
        max_open_trees: int = 32,
        plugins: Mapping[str, HookPlugin] | None = None,
        token_counter: TokenCounter = default_token_counter,
        token_limit: int = 0,
    ) -> None:
        if max_open_trees < 1:
            raise ValueError("max_open_trees must be at least 1")
        if isinstance(token_limit, bool) or not isinstance(token_limit, int) or token_limit < 0:
            raise ValueError("token_limit must be a non-negative integer")
        self.directory = Path(directory).expanduser().resolve()
        self.trees_directory = self.directory / "trees"
        self.trees_directory.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._max_open_trees = int(max_open_trees)
        self._plugins = PluginRegistry(plugins)
        self._token_counter = token_counter
        self._token_limit = token_limit
        self._condition = threading.Condition(threading.RLock())
        self._cache: OrderedDict[str, _CachedTree] = OrderedDict()
        self._deleting: set[str] = set()
        self._hook_sets: dict[str, HookSet] = {}
        self._publisher = ChangePublisher()
        self._closed = False
        self._index = connect(self.directory / "index.sqlite3")
        ensure_index_schema(self._index)
        log_operation(
            logger,
            "context.router",
            "initialize",
            phase="completed",
            level=logging.DEBUG,
            directory=self.directory,
            max_open_trees=self._max_open_trees,
            token_limit=self._token_limit,
        )

    def __enter__(self) -> ContextStoreRouter:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    @staticmethod
    def _new_tree_id() -> str:
        return f"tree_{uuid4().hex}"

    @staticmethod
    def _new_node_id() -> str:
        return f"node_{uuid4().hex}"

    def _ensure_open(self) -> None:
        if self._closed:
            log_operation(
                logger,
                "context.router",
                "availability_check",
                phase="failed",
                level=logging.ERROR,
                directory=self.directory,
                reason="closed",
            )
            raise ContextError("context store router is closed")

    def _now(self) -> datetime:
        return normalize_time(self._clock())

    def _relative_database_path(self, tree_id: str) -> Path:
        digest = hashlib.sha256(tree_id.encode("utf-8")).hexdigest()
        return Path("trees") / digest[:2] / f"{digest}.sqlite3"

    def _index_row_locked(self, tree_id: str) -> sqlite3.Row:
        row = self._index.execute(
            """
            SELECT tree_id, root_id, database_path, created_at
            FROM context_tree_index
            WHERE tree_id = ?
            """,
            (tree_id,),
        ).fetchone()
        if row is None:
            log_operation(
                logger,
                "context.router",
                "lookup_tree",
                phase="failed",
                level=logging.DEBUG,
                tree_id=tree_id,
                reason="not_found",
            )
            raise TreeNotFoundError(f"context tree not found: {tree_id}")
        return row

    def existing_tree_ids(self, tree_ids: Iterable[str]) -> frozenset[str]:
        """Resolve candidate tree ids with batched index-only queries."""

        candidates = tuple(
            dict.fromkeys(
                normalized
                for tree_id in tree_ids
                if (normalized := str(tree_id or "").strip())
            )
        )
        if not candidates:
            return frozenset()
        found: set[str] = set()
        with self._condition:
            self._ensure_open()
            for start in range(0, len(candidates), 500):
                batch = candidates[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = self._index.execute(
                    f"SELECT tree_id FROM context_tree_index "
                    f"WHERE tree_id IN ({placeholders})",
                    batch,
                ).fetchall()
                found.update(str(row["tree_id"]) for row in rows)
        log_operation(
            logger,
            "context.router",
            "existing_tree_ids",
            phase="completed",
            level=logging.DEBUG,
            candidate_count=len(candidates),
            existing_count=len(found),
        )
        return frozenset(found)

    def tree_database_path(self, tree_id: str) -> Path:
        with self._condition:
            self._ensure_open()
            row = self._index_row_locked(str(tree_id))
            result = self.directory / str(row["database_path"])
        log_operation(
            logger,
            "context.router",
            "tree_database_path",
            phase="completed",
            level=logging.DEBUG,
            tree_id=tree_id,
            database=result,
        )
        return result

    def subscribe(
        self,
        listener: ChangeListener,
        *,
        tree_id: str | None = None,
    ) -> Callable[[], None]:
        with self._condition:
            self._ensure_open()
            return self._publisher.subscribe(listener, tree_id=tree_id)

    def _hooks_for_row_locked(self, row: sqlite3.Row) -> HookSet:
        tree_id = str(row["tree_id"])
        hooks = self._hook_sets.get(tree_id)
        if hooks is None:
            hooks = HookSet(
                tree_id,
                str(row["root_id"]),
                _TreeHookPersistence(self, tree_id),
                self._plugins,
            )
            self._hook_sets[tree_id] = hooks
            hooks.wake()
        return hooks

    def hooks_for(self, tree_id: str) -> HookSet:
        """Return the independent HookSet owned by one tree."""

        with self._condition:
            self._ensure_open()
            row = self._index_row_locked(str(tree_id))
            hooks = self._hooks_for_row_locked(row)
        log_operation(
            logger,
            "context.router",
            "hooks_for",
            phase="completed",
            level=logging.DEBUG,
            tree_id=tree_id,
            root_id=hooks.root_id,
        )
        return hooks

    get_hooks = hooks_for

    def _publish_change(self, change: ContextChange, hooks: HookSet) -> None:
        """Notify subscribers and Hooks after every database/router lock is free."""

        self._publisher.publish(change)
        hooks.wake()
        log_operation(
            logger,
            "context.router",
            "publish_change",
            phase="completed",
            tree_id=change.tree_id,
            node_id=change.node_id,
            context_action=change.action,
            change=change,
        )

    def _open_store_locked(self, tree_id: str) -> _CachedTree:
        cached = self._cache.get(tree_id)
        if cached is not None:
            self._cache.move_to_end(tree_id)
            log_operation(
                logger,
                "context.router",
                "open_store",
                phase="cache_hit",
                level=logging.DEBUG,
                tree_id=tree_id,
                users=cached.users,
            )
            return cached
        row = self._index_row_locked(tree_id)
        store = ContextTreeStore(
            self.directory / str(row["database_path"]),
            clock=self._clock,
            token_counter=self._token_counter,
            token_limit=self._token_limit,
        )
        if store.tree.id != tree_id:
            store.close()
            raise ContextError(f"context tree index mismatch: {tree_id}")
        cached = _CachedTree(store)
        self._cache[tree_id] = cached
        self._cache.move_to_end(tree_id)
        log_operation(
            logger,
            "context.router",
            "open_store",
            phase="completed",
            level=logging.DEBUG,
            tree_id=tree_id,
            database=store.database,
            cache_size=len(self._cache),
        )
        return cached

    def _evict_locked(self) -> None:
        while len(self._cache) > self._max_open_trees:
            victim_id = next(
                (
                    tree_id
                    for tree_id, entry in self._cache.items()
                    if entry.users == 0 and tree_id not in self._deleting
                ),
                None,
            )
            if victim_id is None:
                return
            self._cache.pop(victim_id).store.close()
            log_operation(
                logger,
                "context.router",
                "evict_store",
                phase="completed",
                tree_id=victim_id,
                cache_size=len(self._cache),
            )

    @contextmanager
    def _lease(
        self,
        tree_id: str,
        *,
        allow_deleting: bool = False,
    ) -> Iterator[ContextTreeStore]:
        tree_id = str(tree_id)
        with self._condition:
            self._ensure_open()
            if tree_id in self._deleting and not allow_deleting:
                raise TreeNotFoundError(f"context tree is being deleted: {tree_id}")
            cached = self._open_store_locked(tree_id)
            cached.users += 1
            self._cache.move_to_end(tree_id)
            self._evict_locked()
        try:
            yield cached.store
        finally:
            with self._condition:
                cached.users -= 1
                if self._closed and cached.users == 0:
                    current = self._cache.get(tree_id)
                    if current is cached:
                        self._cache.pop(tree_id, None)
                        cached.store.close()
                else:
                    self._evict_locked()
                self._condition.notify_all()

    def create_tree(
        self,
        root_value: Any = None,
        *,
        tree_id: str | None = None,
        root_id: str | None = None,
        initial_hooks: Iterable[HookRegistration] = (),
    ) -> ContextTree:
        tree_id = str(tree_id or self._new_tree_id())
        root_id = str(root_id or self._new_node_id())
        initial_hooks = tuple(initial_hooks)
        relative_path = self._relative_database_path(tree_id)
        database = self.directory / relative_path
        log_operation(
            logger,
            "context.router",
            "create_tree",
            phase="requested",
            tree_id=tree_id,
            root_id=root_id,
            root_value=root_value,
            database=database,
        )
        with self._condition:
            self._ensure_open()
            store: ContextTreeStore | None = None
            attempted_create = False
            try:
                with transaction(self._index):
                    duplicate = self._index.execute(
                        "SELECT 1 FROM context_tree_index WHERE tree_id = ?",
                        (tree_id,),
                    ).fetchone()
                    if duplicate is not None:
                        raise ContextError(f"context tree already exists: {tree_id}")
                    if database.exists():
                        raise ContextError(f"context tree database path already exists: {database}")
                    attempted_create = True
                    store = ContextTreeStore.create(
                        database,
                        tree_id=tree_id,
                        root_id=root_id,
                        root_value=root_value,
                        clock=self._clock,
                        token_counter=self._token_counter,
                        token_limit=self._token_limit,
                    )
                    self._index.execute(
                        """
                        INSERT INTO context_tree_index(tree_id, root_id, database_path, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (tree_id, root_id, str(relative_path), store.tree.created_at.isoformat()),
                    )
            except Exception:
                if store is not None:
                    store.close()
                if attempted_create:
                    self._remove_database_files(database)
                raise
            assert store is not None
            self._cache[tree_id] = _CachedTree(store)
            self._cache.move_to_end(tree_id)
            hooks = HookSet(
                tree_id,
                root_id,
                _TreeHookPersistence(self, tree_id),
                self._plugins,
            )
            self._hook_sets[tree_id] = hooks
            try:
                for registration in initial_hooks:
                    hooks.register(
                        registration.event,
                        registration.plugin,
                        plugin_id=registration.plugin_id,
                        hook_id=registration.hook_id,
                        root_only=registration.root_only,
                        matcher=registration.matcher,
                        failure_policy=registration.failure_policy,
                        config=registration.config,
                        enabled=registration.enabled,
                    )
                store.enqueue_initial_root()
            except Exception:
                self._hook_sets.pop(tree_id, None)
                self._cache.pop(tree_id, None)
                hooks.close(cancel_pending=True)
                store.close()
                with transaction(self._index):
                    self._index.execute(
                        "DELETE FROM context_tree_index WHERE tree_id = ?",
                        (tree_id,),
                    )
                self._remove_database_files(database)
                raise
            self._evict_locked()
            tree = store.tree
        self._publish_change(
            ContextChange(tree.id, tree.root_id, "mount", tree.created_at),
            hooks,
        )
        log_operation(
            logger,
            "context.router",
            "create_tree",
            phase="completed",
            tree_id=tree.id,
            root_id=tree.root_id,
            database=database,
            initial_hook_count=len(initial_hooks),
        )
        return tree

    def get_tree(self, tree_id: str) -> ContextTree:
        with self._lease(tree_id) as store:
            tree = store.tree
        log_operation(
            logger,
            "context.router",
            "get_tree",
            phase="completed",
            level=logging.DEBUG,
            tree_id=tree.id,
            root_id=tree.root_id,
        )
        return tree

    def delete_tree(self, tree_id: str) -> None:
        tree_id = str(tree_id)
        deleted_at = self._now()
        log_operation(
            logger,
            "context.router",
            "delete_tree",
            phase="requested",
            tree_id=tree_id,
        )
        with self._condition:
            self._ensure_open()
            if tree_id in self._deleting:
                raise TreeNotFoundError(f"context tree is being deleted: {tree_id}")
            self._deleting.add(tree_id)
            try:
                cached = self._open_store_locked(tree_id)
                row = self._index_row_locked(tree_id)
                hooks = self._hooks_for_row_locked(row)
            except Exception:
                self._deleting.discard(tree_id)
                raise
            cached.users += 1
            database = cached.store.database
            root_id = cached.store.tree.root_id

        hooks.close(wait=True)
        try:
            deleted_ids = cached.store.mark_deleted()
        except Exception:
            with self._condition:
                cached.users -= 1
                self._deleting.discard(tree_id)
                self._hook_sets.pop(tree_id, None)
                self._condition.notify_all()
            raise

        with self._condition:
            cached.users -= 1
            while cached.users > 0:
                self._condition.wait()
            try:
                with transaction(self._index):
                    self._index.execute(
                        "DELETE FROM context_tree_index WHERE tree_id = ?",
                        (tree_id,),
                    )
            except Exception:
                cached.store.unmark_deleted()
                self._deleting.discard(tree_id)
                self._hook_sets.pop(tree_id, None)
                self._condition.notify_all()
                raise
            self._cache.pop(tree_id, None)
            self._hook_sets.pop(tree_id, None)
            cached.store.close()
            self._deleting.discard(tree_id)
            self._condition.notify_all()

        self._remove_database_files(database)
        change = ContextChange(
            tree_id,
            root_id,
            "delete",
            deleted_at,
            deleted_node_ids=deleted_ids,
        )
        self._publisher.publish(change)
        log_operation(
            logger,
            "context.router",
            "delete_tree",
            phase="completed",
            tree_id=tree_id,
            root_id=root_id,
            deleted_node_ids=deleted_ids,
            database=database,
        )

    @staticmethod
    def _remove_database_files(database: Path) -> None:
        for candidate in (
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
        ):
            candidate.unlink(missing_ok=True)

    def mount(
        self,
        tree_id: str,
        parent_id: str,
        value: Any,
        *,
        node_id: str | None = None,
    ) -> ContextNode:
        hooks = self.hooks_for(tree_id)
        with self._lease(tree_id) as store:
            node = store.mount(parent_id, value, node_id=node_id)
        self._publish_change(
            ContextChange(
                node.tree_id,
                node.id,
                "mount",
                node.created_at,
                parent_id=node.parent_id,
            ),
            hooks,
        )
        log_operation(
            logger,
            "context.router",
            "mount",
            phase="completed",
            tree_id=tree_id,
            parent_id=parent_id,
            node_id=node.id,
            value=value,
        )
        return node

    def update_node(self, tree_id: str, node_id: str, value: Any) -> ContextNode:
        hooks = self.hooks_for(tree_id)
        with self._lease(tree_id) as store:
            node = store.update_node(node_id, value)
        self._publish_change(
            ContextChange(
                node.tree_id,
                node.id,
                "update",
                node.updated_at,
                parent_id=node.parent_id,
            ),
            hooks,
        )
        log_operation(
            logger,
            "context.router",
            "update_node",
            phase="completed",
            tree_id=tree_id,
            node_id=node_id,
            value=value,
        )
        return node

    def get_node(self, tree_id: str, node_id: str) -> ContextNode:
        with self._lease(tree_id) as store:
            return store.get_node(node_id)

    def is_root(self, tree_id: str, node_id: str) -> bool:
        with self._lease(tree_id) as store:
            return store.is_root(node_id)

    def has_child(self, tree_id: str, node_id: str) -> bool:
        with self._lease(tree_id) as store:
            return store.has_child(node_id)

    def get_parent(self, tree_id: str, node_id: str) -> ContextNode | None:
        with self._lease(tree_id) as store:
            return store.get_parent(node_id)

    def get_children(self, tree_id: str, node_id: str) -> list[ContextNode]:
        with self._lease(tree_id) as store:
            return store.get_children(node_id)

    def get_path(self, tree_id: str, node_id: str) -> list[ContextNode]:
        with self._lease(tree_id) as store:
            return store.get_path(node_id)

    def get_subtree(self, tree_id: str, node_id: str) -> list[ContextNode]:
        with self._lease(tree_id) as store:
            return store.get_subtree(node_id)

    def delete_node(self, tree_id: str, node_id: str, *, recursive: bool = False) -> None:
        hooks = self.hooks_for(tree_id)
        with self._lease(tree_id) as store:
            change = store.delete_node(node_id, recursive=recursive)
        self._publish_change(change, hooks)
        log_operation(
            logger,
            "context.router",
            "delete_node",
            phase="completed",
            tree_id=tree_id,
            node_id=node_id,
            recursive=recursive,
            change=change,
        )

    def report_context_used(
        self,
        tree_id: str,
        node_id: str,
        tokens: int,
        *,
        token_limit: int = 0,
        node_tokens: dict[str, int] | None = None,
    ) -> ContextUsed:
        """Report how many model-input tokens one tree path occupied."""

        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise ValueError("tokens must be a non-negative integer")
        if (
            isinstance(token_limit, bool)
            or not isinstance(token_limit, int)
            or token_limit < 0
        ):
            raise ValueError("token_limit must be a non-negative integer")
        normalized_node_tokens: dict[str, int] = {}
        for raw_id, raw_tokens in (node_tokens or {}).items():
            if isinstance(raw_tokens, bool) or not isinstance(raw_tokens, int) or raw_tokens < 0:
                raise ValueError("node token counts must be non-negative integers")
            normalized_node_tokens[str(raw_id)] = raw_tokens

        hooks = self.hooks_for(tree_id)
        with self._lease(tree_id) as store:
            store.get_node(node_id)
        usage = ContextUsed(
            tree_id=str(tree_id),
            node_id=str(node_id),
            tokens=tokens,
            token_limit=token_limit,
            usage_ratio=(tokens / token_limit) if token_limit else 0.0,
            node_tokens=normalized_node_tokens,
            time=self._now(),
        )
        with self._lease(tree_id) as store:
            store.enqueue_context_used(usage)
        hooks.wake()
        log_operation(
            logger,
            "context.router",
            "report_context_used",
            phase="completed",
            tree_id=tree_id,
            node_id=node_id,
            tokens=tokens,
            token_limit=token_limit,
            node_tokens=normalized_node_tokens,
            usage_ratio=usage.usage_ratio,
        )
        return usage

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            hook_sets = tuple(self._hook_sets.values())
        for hooks in hook_sets:
            hooks.close(wait=True)
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._publisher.clear()
            self._hook_sets.clear()
            idle_ids = [tree_id for tree_id, entry in self._cache.items() if entry.users == 0]
            for tree_id in idle_ids:
                self._cache.pop(tree_id).store.close()
            self._index.close()
        log_operation(
            logger,
            "context.router",
            "close",
            phase="completed",
            level=logging.DEBUG,
            directory=self.directory,
            closed_hook_sets=len(hook_sets),
            closed_idle_trees=len(idle_ids),
        )
