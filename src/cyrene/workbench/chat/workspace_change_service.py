"""Track workspace changes and generated files across chat runs."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .chat_repository import ChatRepository
from cyrene.workbench.workspaces.workspace_changes import (
    WorkspaceSnapshot,
    WorkspaceSnapshotIndex,
    build_change_set,
    list_chat_change_sets,
    save_change_set,
)

logger = logging.getLogger(__name__)


def _generated_file(path, change):
    name = Path(path).name or path
    return {
        "id": str(change.get("id") or f"workspace_{uuid.uuid4().hex[:12]}"),
        "name": name,
        "path": path,
        "content_type": mimetypes.guess_type(name)[0]
        or "application/octet-stream",
        "size": int(change.get("afterSize") or 0),
        "kind": "file",
        "source": "agent",
    }


@dataclass(slots=True)
class WorkspaceChangesBaseline:
    snapshot: WorkspaceSnapshot | None
    workspace_key: str = ""
    run_id: str = ""
    overlapping_run_ids: set[str] = field(default_factory=set)
    released: bool = False


class WorkspaceChangeService:
    """Small run-scoped workspace snapshot service used by Chat routes."""

    def __init__(self, db_path: str, repository: ChatRepository) -> None:
        self.db_path = str(db_path or "")
        self.repository = repository
        self._lock = asyncio.Lock()
        self._active: dict[str, dict[str, WorkspaceChangesBaseline]] = {}
        self._indexes: dict[str, WorkspaceSnapshotIndex] = {}
        self._index_users: dict[str, int] = {}
        self._idle_indexes: dict[str, asyncio.TimerHandle] = {}
        self._closing_indexes: set[asyncio.Task] = set()
        self._idle_seconds = 30.0

    def _retain_index(self, key: str) -> WorkspaceSnapshotIndex:
        timer = self._idle_indexes.pop(key, None)
        if timer:
            timer.cancel()
        self._index_users[key] = self._index_users.get(key, 0) + 1
        return self._index(key)

    def _release_index(self, key: str) -> None:
        remaining = self._index_users.get(key, 0) - 1
        if remaining > 0:
            self._index_users[key] = remaining
        else:
            self._index_users.pop(key, None)
            self._schedule_index_expiry(key)

    def _schedule_index_expiry(self, key: str) -> None:
        if key in self._index_users or key in self._idle_indexes:
            return
        index = self._indexes.get(key)
        if index is None:
            return

        def expire():
            self._idle_indexes.pop(key, None)
            if key in self._index_users or self._indexes.get(key) is not index:
                return
            # Detach before awaiting close; a new capture gets a fresh index
            # and a complete scan, including changes made while unobserved.
            self._indexes.pop(key)
            task = asyncio.create_task(index.close())
            self._closing_indexes.add(task)
            task.add_done_callback(self._closing_indexes.discard)

        self._idle_indexes[key] = asyncio.get_running_loop().call_later(self._idle_seconds, expire)

    def _index(self, workspace_key: str) -> WorkspaceSnapshotIndex:
        index = self._indexes.get(workspace_key)
        if index is None:
            index = WorkspaceSnapshotIndex(workspace_key)
            self._indexes[workspace_key] = index
        return index

    def prewarm(self, workspace_dir: str | Path | None) -> None:
        if not workspace_dir:
            return
        try:
            key = str(Path(workspace_dir).expanduser().resolve())
            asyncio.get_running_loop()
        except (OSError, RuntimeError):
            return
        self._index(key).start()
        self._schedule_index_expiry(key)

    async def capture(
        self,
        workspace_dir: str | Path | None,
        run_id: str = "",
    ) -> WorkspaceChangesBaseline:
        if not workspace_dir:
            return WorkspaceChangesBaseline(None)
        try:
            key = str(Path(workspace_dir).expanduser().resolve())
        except OSError:
            return WorkspaceChangesBaseline(None)
        normalized_run_id = str(run_id or f"snapshot_{uuid.uuid4().hex}")
        index = self._retain_index(key)
        try:
            snapshot = await index.snapshot()
            baseline = WorkspaceChangesBaseline(snapshot, key, normalized_run_id)
            if snapshot is None:
                self._release_index(key)
                return baseline
            async with self._lock:
                active = self._active.setdefault(key, {})
                for other_id, other in active.items():
                    baseline.overlapping_run_ids.add(other_id)
                    other.overlapping_run_ids.add(normalized_run_id)
                active[normalized_run_id] = baseline
            return baseline
        except BaseException:
            self._release_index(key)
            raise

    async def finalize(
        self,
        *,
        chat_id: str,
        run_id: str,
        workspace_dir: str | Path | None,
        before: WorkspaceChangesBaseline | None,
        status: str,
        run: Any = None,
    ) -> dict[str, Any] | None:
        if before is None or before.snapshot is None or before.released:
            return None
        try:
            after = await self._index(before.workspace_key).snapshot()
            overlaps = sorted(before.overlapping_run_ids)
            change_set = await asyncio.to_thread(
                build_change_set,
                chat_id=chat_id,
                run_id=run_id,
                before=before.snapshot,
                after=after,
                status=status,
                attribution="overlapping" if overlaps else "exclusive",
                overlapping_run_ids=overlaps,
            )
            if change_set.get("fileCount"):
                await asyncio.to_thread(save_change_set, self.db_path, change_set)
                await asyncio.to_thread(self.sync_generated_files, chat_id, change_set)
            event = {
                "type": "workspace_changes",
                "chatId": chat_id,
                "runId": run_id,
                "changeSetId": change_set.get("id"),
                "status": status,
                "fileCount": int(change_set.get("fileCount") or 0),
                "additions": int(change_set.get("additions") or 0),
                "deletions": int(change_set.get("deletions") or 0),
                "attribution": str(change_set.get("attribution") or "exclusive"),
                "overlappingRunIds": list(change_set.get("overlappingRunIds") or []),
            }
            if run is not None:
                await run.publish(event)
            try:
                from cyrene.observability import debug

                await debug.publish_event(event, session_id=chat_id)
            except Exception:
                logger.debug("Workspace event publication failed", exc_info=True)
            return change_set
        except Exception:
            logger.exception("Failed to finalize workspace changes for chat %s", chat_id)
            return None
        finally:
            before.released = True
            async with self._lock:
                active = self._active.get(before.workspace_key)
                if active is not None:
                    active.pop(before.run_id, None)
                    if not active:
                        self._active.pop(before.workspace_key, None)
            self._release_index(before.workspace_key)

    def sync_generated_files(
        self,
        chat_id: str,
        change_set: dict[str, Any] | None = None,
    ) -> None:
        historical: list[dict[str, Any]] = []
        current = self.repository.get(chat_id)
        if current is not None and "generatedFiles" not in current:
            historical = list(
                reversed(list_chat_change_sets(self.db_path, chat_id))
            )

        def update(chat: dict[str, Any]) -> None:
            existing = {
                str(item.get("path") or ""): dict(item)
                for item in chat.get("generatedFiles") or ()
                if isinstance(item, Mapping) and str(item.get("path") or "")
            }
            changes = [*historical]
            if change_set is not None:
                changes.append(change_set)
            order: list[str] = []
            for item in changes:
                for change in item.get("files") or ():
                    if not isinstance(change, Mapping):
                        continue
                    path = str(change.get("path") or "").strip().replace("\\", "/")
                    if not path:
                        continue
                    if path in order:
                        order.remove(path)
                    order.append(path)
                    if str(change.get("changeType") or "") == "deleted":
                        existing.pop(path, None)
                        continue
                    existing[path] = _generated_file(path, change)
            ordered = [existing[path] for path in order if path in existing]
            ordered.extend(
                item for path, item in existing.items() if path not in order
            )
            chat["generatedFiles"] = ordered[:200]

        self.repository.mutate_one(chat_id, update)

    async def shutdown(self) -> None:
        for timer in self._idle_indexes.values():
            timer.cancel()
        self._idle_indexes.clear()
        self._index_users.clear()
        indexes = list(self._indexes.values())
        self._indexes.clear()
        await asyncio.gather(
            *self._closing_indexes,
            *(index.close() for index in indexes),
            return_exceptions=True,
        )
