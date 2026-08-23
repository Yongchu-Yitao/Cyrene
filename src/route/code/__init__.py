"""Composition for code-related API routes."""

from fastapi import APIRouter

from cyrene.workbench.code_format_service import CodeFormatService
from cyrene.workbench.project_files import ProjectFileService
from cyrene.workbench.workspace_diff_service import WorkspaceDiffService
from route.code.diff import register_diff_routes
from route.code.files import register_file_routes
from route.code.format import register_format_routes


def register_code_routes(
    parent: APIRouter,
    files: ProjectFileService,
    diffs: WorkspaceDiffService,
    formatter: CodeFormatService,
) -> None:
    router = APIRouter(prefix="/api/code", tags=["code"])
    register_file_routes(router, files)
    register_format_routes(router, formatter)
    register_diff_routes(router, diffs)
    parent.include_router(router)


__all__ = ["register_code_routes"]
