"""Thin file read/write HTTP adapters for the code editor."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cyrene.workbench.project_files import ProjectFileError, ProjectFileService


class FileWriteBody(BaseModel):
    path: str
    content: str


def register_file_routes(router: APIRouter, files: ProjectFileService) -> None:
    @router.get("/file")
    async def read_file(path: str = Query(...)):
        """Read a file from the workspace."""
        try:
            return await files.read_code_file(path)
        except ProjectFileError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @router.put("/file")
    async def write_file(body: FileWriteBody):
        """Write a file to the workspace."""
        try:
            return await files.write_code_file(body.path, body.content)
        except ProjectFileError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


__all__ = ["FileWriteBody", "register_file_routes"]
