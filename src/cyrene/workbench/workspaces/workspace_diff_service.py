"""Workspace diff application service for the code editor."""

from __future__ import annotations

import asyncio
import difflib
import logging
from dataclasses import dataclass
from pathlib import Path

from cyrene.localization import localized
from cyrene.workbench.projects.project_files import ProjectFileService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkspaceDiffError(Exception):
    message: str
    status_code: int
    code: str

    def __str__(self) -> str:
        return self.message


class WorkspaceDiffService:
    """Compute text, file, and git diffs within the configured workspace."""

    def __init__(self, files: ProjectFileService, workspace_root: Path):
        self.files = files
        self.workspace_root = Path(workspace_root).resolve()

    async def compute(self, mode: str, left: str, right: str) -> dict[str, object]:
        left_text = left
        right_text = right
        if mode == "file":
            left_text = await self.files.read_diff_text(left)
            right_text = await self.files.read_diff_text(right)
        diff = unified_diff(left_text, right_text, left, right)
        return {"diff": diff, "has_changes": bool(diff.strip())}

    async def git_diff(self, path: str = "", staged: bool = False) -> dict[str, object]:
        command = ["git", "diff"]
        resolved: Path | None = None
        relative = ""
        if staged:
            command.append("--staged")
        if path:
            resolved = self.files.resolve_code_path(path).resolve()
            try:
                relative = str(resolved.relative_to(self.workspace_root))
            except ValueError as exc:
                raise WorkspaceDiffError(
                    localized(
                        "The path is outside the Git workspace.",
                        "该路径位于 Git 工作区之外。",
                    ),
                    403,
                    "workspace_path_forbidden",
                ) from exc
            command.extend(["--", relative])
        return_code, stdout, stderr = await self._run_git(command, timeout=30.0)
        if return_code not in (0, 1):
            logger.warning(
                "Git diff failed [return_code=%s stderr=%s]",
                return_code,
                stderr.decode("utf-8", errors="replace")[:1000],
            )
            raise WorkspaceDiffError(
                localized(
                    "Git could not calculate the requested diff.",
                    "Git 无法计算请求的差异。",
                ),
                400,
                "git_diff_failed",
            )
        diff = stdout.decode("utf-8", errors="replace")
        if resolved is not None and not staged and not diff.strip():
            diff = await self._untracked_file_diff(resolved, relative)
        return {
            "diff": diff,
            "has_changes": bool(diff.strip()),
            "path": path,
            "staged": staged,
        }

    async def _untracked_file_diff(self, target: Path, relative: str) -> str:
        if not target.is_file():
            return ""
        return_code, _, _ = await self._run_git(
            ["git", "ls-files", "--error-unmatch", "--", relative], timeout=None
        )
        if return_code == 0:
            return ""
        try:
            content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return ""
        if not content:
            return ""
        return unified_diff("", content, "/dev/null", f"b/{relative}")

    async def _run_git(
        self, command: list[str], *, timeout: float | None
    ) -> tuple[int, bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.workspace_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            communication = process.communicate()
            stdout, stderr = (
                await asyncio.wait_for(communication, timeout=timeout)
                if timeout is not None
                else await communication
            )
        except asyncio.TimeoutError as exc:
            raise WorkspaceDiffError(
                localized("Git diff timed out.", "Git 差异计算超时。"),
                504,
                "git_diff_timeout",
            ) from exc
        except FileNotFoundError as exc:
            raise WorkspaceDiffError(
                localized("Git is unavailable.", "Git 不可用。"),
                500,
                "git_unavailable",
            ) from exc
        return int(process.returncode or 0), stdout, stderr


def unified_diff(
    left_text: str,
    right_text: str,
    left_label: str = "a",
    right_label: str = "b",
) -> str:
    return "".join(
        difflib.unified_diff(
            left_text.splitlines(keepends=True),
            right_text.splitlines(keepends=True),
            fromfile=left_label,
            tofile=right_label,
        )
    )


__all__ = ["WorkspaceDiffError", "WorkspaceDiffService", "unified_diff"]
