"""Thin code-formatting HTTP adapter."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..code_format_service import CodeFormatError, CodeFormatService


class FormatBody(BaseModel):
    code: str
    language: str = "python"


def register_format_routes(router: APIRouter, service: CodeFormatService) -> None:
    @router.post("/format")
    async def format_code(body: FormatBody):
        """Format code using ruff (Python) or return unchanged for other languages."""
        try:
            return await service.format(body.code, body.language)
        except CodeFormatError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


__all__ = ["FormatBody", "register_format_routes"]
