"""Shared, explicit dependencies for Workbench chat route slices."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.workbench.chat_service import ChatService
from cyrene.workbench.runtime_facade import WorkbenchRuntimeFacade

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatRouteContext:
    bot: Any
    db_path: str
    service: ChatService
    workbench_runtime: WorkbenchRuntimeFacade

    @classmethod
    def create(cls, *, bot: Any, db_path: str) -> "ChatRouteContext":
        service = ChatService(db_path)
        from cyrene.workbench import chat_groups, pinned_resources

        pinned_resources.configure(db_path)
        chat_groups.configure_store(db_path)
        context = cls(
            bot=bot,
            db_path=str(db_path),
            service=service,
            workbench_runtime=WorkbenchRuntimeFacade(),
        )
        context._configure_shell_wake()
        return context

    def runtime(self):
        return self.workbench_runtime

    def project_data_key(self, project_id: str) -> str:
        runtime = self.runtime()
        project = runtime.find_project_lightweight(project_id)
        return runtime.project_data_key(project) if project else project_id

    async def resolve_library_file_payload(
        self,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        body = dict(raw or {})
        nested = body.get("file") if isinstance(body.get("file"), dict) else {}
        source_kind = str(body.get("sourceKind") or nested.get("sourceKind") or "")
        item_id = str(body.get("libraryItemId") or nested.get("libraryItemId") or "")
        workspace = str(body.get("ownerProjectId") or nested.get("ownerProjectId") or "")
        if source_kind != "library" or not item_id or not workspace:
            return body
        try:
            from cyrene.knowledge import library as knowledge_library
            from cyrene.runtime.attachments import resolve_managed_attachment_path
            from cyrene.workbench.knowledge import ensure_kb_db

            kb_path = await ensure_kb_db(workspace)
            if not await knowledge_library.get_item(kb_path, item_id):
                return body
            attachment = await knowledge_library.get_primary_attachment(kb_path, item_id)
            if not attachment:
                return body
            stored_path = str(attachment.get("document_path") or attachment.get("path") or "")
            path = Path(stored_path)
            if not path.is_file():
                path = resolve_managed_attachment_path(stored_path)
            if path is None or not path.is_file():
                return body
            name = str(attachment.get("filename") or path.name)
            content_type = str(attachment.get("document_content_type") or attachment.get("content_type") or body.get("content_type") or "application/octet-stream")
            resolved_file = {
                **nested,
                "id": str(nested.get("id") or f"library:{workspace}:{item_id}"),
                "name": name,
                "path": str(path.resolve()),
                "url": str(body.get("url") or nested.get("url") or ""),
                "content_type": content_type,
                "size": int(path.stat().st_size),
                "kind": str(nested.get("kind") or "file"),
                "sourceKind": "library",
                "libraryItemId": item_id,
                "ownerProjectId": workspace,
            }
            return {
                **body,
                "name": name,
                "title": str(body.get("title") or name),
                "path": str(path.resolve()),
                "content_type": content_type,
                "size": int(path.stat().st_size),
                "sourceKind": "library",
                "libraryItemId": item_id,
                "ownerProjectId": workspace,
                "file": resolved_file,
            }
        except Exception:
            logger.exception(
                "Failed to resolve dragged library item %s in %s",
                item_id,
                workspace,
            )
            return body

    @staticmethod
    def public_pinned_resource(item: dict[str, Any]) -> dict[str, Any]:
        public = dict(item)
        public.pop("path", None)
        nested = public.get("file")
        if isinstance(nested, dict):
            public["file"] = {key: value for key, value in nested.items() if key != "path"}
        return public

    def _configure_shell_wake(self) -> None:
        from cyrene.runtime.shell_wake import get_shell_wake_service

        async def dispatch(wake: dict[str, Any]) -> str:
            return await self.service.dispatch_shell_wake_run(
                wake,
                bot=self.bot,
                db_path=self.db_path,
            )

        get_shell_wake_service().configure(
            dispatcher=dispatch,
            is_busy=lambda chat_id: self.service.run_manager.get(str(chat_id)) is not None,
        )


__all__ = ["ChatRouteContext"]
