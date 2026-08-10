"""Code-related API routes — file operations, formatting, and diff."""
from fastapi import APIRouter

from route.code.diff import router as diff_router
from route.code.files import router as files_router
from route.code.format import router as format_router

router = APIRouter(prefix="/api/code", tags=["code"])

router.include_router(files_router)
router.include_router(format_router)
router.include_router(diff_router)
