"""Thin diff computation HTTP adapters."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..project_files import ProjectFileError
from ..workspace_diff_service import (
    WorkspaceDiffError,
    WorkspaceDiffService,
)


class DiffBody(BaseModel):
    mode: str = "text"
    left: str = ""
    right: str = ""


class GitDiffBody(BaseModel):
    path: str = ""
    staged: bool = False


def register_diff_routes(router: APIRouter, service: WorkspaceDiffService) -> None:
    @router.post("/diff")
    async def compute_diff(body: DiffBody):
        """Compute a unified diff between two texts or two files."""
        try:
            return await service.compute(body.mode, body.left, body.right)
        except ProjectFileError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @router.post("/git-diff")
    async def compute_git_diff(body: GitDiffBody):
        """Compute git diff for the current workspace or a specific path."""
        try:
            return await service.git_diff(body.path, body.staged)
        except (ProjectFileError, WorkspaceDiffError) as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


__all__ = ["DiffBody", "GitDiffBody", "register_diff_routes"]
