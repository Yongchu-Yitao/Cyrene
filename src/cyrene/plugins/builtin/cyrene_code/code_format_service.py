"""Code formatting application service."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cyrene.localization import localized

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class CodeFormatError(Exception):
    message: str
    status_code: int = 500

    def __str__(self) -> str:
        return self.message


class CodeFormatService:
    """Own formatter discovery, subprocess execution, and temp-file cleanup."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = Path(temp_dir)

    async def format(self, code: str, language: str) -> dict[str, object]:
        if language not in ("python", "py"):
            return {"formatted": code, "changed": False}
        if shutil.which("ruff") is None:
            return {
                "formatted": code,
                "changed": False,
                "warning": localized(
                    "The Ruff formatter is unavailable.",
                    "Ruff 格式化工具不可用。",
                ),
                "warning_code": "formatter_unavailable",
            }
        try:
            temp_path = self._write_temporary_source(code)
        except OSError as exc:
            logger.warning("Could not create temporary formatting source", exc_info=True)
            raise CodeFormatError(localized(
                "The code could not be prepared for formatting.",
                "无法准备待格式化的代码。",
            )) from exc
        try:
            try:
                await self._run_ruff(temp_path)
            except FileNotFoundError:
                return {
                    "formatted": code,
                    "changed": False,
                    "warning": localized(
                        "The Ruff formatter is unavailable.",
                        "Ruff 格式化工具不可用。",
                    ),
                    "warning_code": "formatter_unavailable",
                }
            formatted = await asyncio.to_thread(
                temp_path.read_text, encoding="utf-8"
            )
            return {"formatted": formatted, "changed": formatted != code}
        except (OSError, UnicodeError) as exc:
            logger.warning("Code formatting failed", exc_info=True)
            raise CodeFormatError(localized(
                "Code formatting failed.",
                "代码格式化失败。",
            )) from exc
        finally:
            self._remove_temporary_source(temp_path)

    def _write_temporary_source(self, code: str) -> Path:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".py",
            dir=self.temp_dir,
            delete=False,
        ) as handle:
            handle.write(code)
            return Path(handle.name)

    @staticmethod
    async def _run_ruff(temp_path: Path) -> tuple[bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            "ruff",
            "format",
            "--quiet",
            str(temp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return await process.communicate()

    @staticmethod
    def _remove_temporary_source(temp_path: Path) -> None:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            # Formatting has already completed (or failed); cleanup must not
            # replace that result with a secondary filesystem error.
            pass


__all__ = ["CodeFormatError", "CodeFormatService"]
