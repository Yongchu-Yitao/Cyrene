"""Composition root for the project-isolated Workbench literature library."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from cyrene.knowledge.library_services import (
    LibraryApplicationService,
    LibraryIngestService,
    LibraryRepository,
    ZoteroSyncService,
)
from cyrene.knowledge.workspace import ensure_workspace_db, resolve_workspace_id
from cyrene.runtime.attachments import EXPORTS_DIR, UPLOADS_DIR
from cyrene.runtime.integration_settings import get_zotero_settings
from route.workbench.library_routes.collections import register_collection_routes
from route.workbench.library_routes.common import require_library_workspace
from route.workbench.library_routes.ingest_search import register_ingest_search_routes
from route.workbench.library_routes.items import register_item_routes
from route.workbench.library_routes.notes_relations import register_note_relation_routes
from route.workbench.library_routes.zotero import register_zotero_routes


def register_workbench_library_routes(parent_router: APIRouter) -> None:
    """Compose library services and install the focused route slices."""
    repository = LibraryRepository()
    application = LibraryApplicationService(
        repository=repository,
        ensure_db=ensure_workspace_db,
        resolve_workspace=resolve_workspace_id,
        uploads_dir=UPLOADS_DIR,
        exports_dir=EXPORTS_DIR,
    )
    ingest = LibraryIngestService(application=application, uploads_dir=UPLOADS_DIR)
    zotero = ZoteroSyncService(application=application, settings=get_zotero_settings)
    def require_workspace(workspace: str = "") -> None:
        require_library_workspace(workspace, resolve_workspace_id)

    router = APIRouter(dependencies=[Depends(require_workspace)])

    register_collection_routes(router, application)
    register_item_routes(router, application)
    register_note_relation_routes(router, application)
    register_ingest_search_routes(router, application, ingest)
    register_zotero_routes(router, zotero)
    parent_router.include_router(router)


__all__ = ["register_workbench_library_routes"]
