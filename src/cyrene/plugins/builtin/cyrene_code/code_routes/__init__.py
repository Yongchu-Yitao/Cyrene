"""Composition for code-related API routes."""

from fastapi import APIRouter

from ..code_format_service import CodeFormatService
from ..project_files import ProjectFileService
from ..workspace_diff_service import WorkspaceDiffService
from .diff import register_diff_routes
from .files import register_file_routes
from .format import register_format_routes
from .execution import register_execution_routes
from ..workspace_execution import WorkspaceExecutionService


def register_code_routes(
    parent: APIRouter,
    files: ProjectFileService,
    diffs: WorkspaceDiffService,
    formatter: CodeFormatService,
    execution: WorkspaceExecutionService,
) -> None:
    router = APIRouter(prefix="/api/code", tags=["code"])
    register_file_routes(router, files)
    register_format_routes(router, formatter)
    register_diff_routes(router, diffs)
    register_execution_routes(router, execution)
    parent.include_router(router)


__all__ = ["register_code_routes"]
