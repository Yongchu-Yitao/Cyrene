"""Project query and file HTTP adapters."""

from __future__ import annotations

import asyncio
import mimetypes

from fastapi import APIRouter
from fastapi.responses import FileResponse

from cyrene.workbench.projects.project_files import ProjectFileError, ProjectFileService
from cyrene.workbench.projects.project_services import ProjectApplicationService
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import error_response


def _file_error(exc: ProjectFileError):
    return error_response(exc.message, exc.status_code, exc.code, **exc.details)


def register_project_query_file_routes(
    router: APIRouter,
    projects: ProjectApplicationService,
    files: ProjectFileService,
) -> None:
    @router.get("/api/projects")
    async def api_workbench_projects(detail: str = "full"):
        return await projects.list(detail)

    @router.get("/api/projects/{project_id}/files")
    async def api_workbench_project_files(
        project_id: str,
        path: str = ".",
        query: str = "",
    ):
        try:
            result = await files.list_files(project_id, path, query)
        except ProjectFileError as exc:
            return _file_error(exc)
        return {"ok": True, **result}

    @router.get("/api/projects/{project_id}/files/content/{file_path:path}")
    async def api_workbench_project_file_content(project_id: str, file_path: str):
        """Stream a regular project file for the Workbench split viewer."""
        try:
            target = await files.resolve_preview(project_id, file_path)
        except ProjectFileError as exc:
            return _file_error(exc)
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            target,
            filename=target.name,
            media_type=media_type,
            content_disposition_type="inline",
        )

    @router.get("/api/projects/{project_id}/files/edit/{file_path:path}")
    async def api_workbench_project_text_file(project_id: str, file_path: str):
        """Read an editable UTF-8 project file with an optimistic-lock version."""
        try:
            payload = await asyncio.to_thread(files.read_editable, project_id, file_path)
        except ProjectFileError as exc:
            return _file_error(exc)
        return {"ok": True, "path": str(file_path).replace("\\", "/"), **payload}

    @router.put("/api/projects/{project_id}/files/edit/{file_path:path}")
    async def api_workbench_update_project_text_file(
        project_id: str,
        file_path: str,
        body: api_models.ProjectTextFileUpdateBody,
    ):
        """Atomically save an existing UTF-8 project file with conflict detection."""
        try:
            result = await files.save_editable(
                project_id,
                file_path,
                body.content,
                expected_version=body.expectedVersion,
                force=body.force,
            )
        except ProjectFileError as exc:
            return _file_error(exc)
        return {"ok": True, **result}


__all__ = ["register_project_query_file_routes"]
