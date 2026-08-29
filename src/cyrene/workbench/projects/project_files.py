"""Project workspace file application service.

The HTTP adapter owns request validation and response serialization.  This
service owns workspace containment, file discovery, UTF-8 editing and
optimistic concurrency so those rules have one explicit dependency boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from cyrene.localization import localized

logger = logging.getLogger(__name__)

class ProjectFileEntryDTO(TypedDict):
    name: str
    path: str
    kind: str
    size: int
    modifiedNs: int


class EditableProjectFileDTO(TypedDict):
    content: str
    version: str
    modifiedNs: int
    size: int
    bom: bool
    contentType: str


@dataclass(slots=True)
class ProjectFileError(Exception):
    message: str
    status_code: int
    code: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class ProjectFileService:
    """Workspace-safe file browsing and editing for one project repository."""

    editable_text_extensions = {
        ".bash", ".c", ".cc", ".cfg", ".conf", ".cpp", ".css", ".csv",
        ".env", ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java",
        ".js", ".json", ".jsx", ".kt", ".log", ".md", ".mdx", ".php",
        ".properties", ".py", ".rb", ".rs", ".rst", ".scss", ".sh",
        ".sql", ".svelte", ".swift", ".toml", ".ts", ".tsx", ".txt",
        ".vue", ".xml", ".yaml", ".yml",
    }
    editable_text_names = {
        ".editorconfig", ".env", ".gitattributes", ".gitignore", ".npmrc",
        "dockerfile", "license", "makefile", "readme",
    }
    max_editable_text_bytes = 4 * 1024 * 1024
    code_languages = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".html": "html",
        ".htm": "html", ".css": "css", ".json": "json", ".md": "markdown",
        ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
        ".sql": "sql", ".sh": "shell", ".bash": "shell", ".rs": "rust",
        ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
        ".h": "c", ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".kt": "kotlin", ".txt": "text",
    }

    def __init__(
        self,
        *,
        find_project: Callable[[str], dict[str, Any] | None],
        resolve_workspace: Callable[[dict[str, Any]], str],
        resolve_workspace_async: Callable[[dict[str, Any]], Awaitable[str]],
        resolve_active_path: Callable[[str], Path] | None = None,
        resolve_active_write_target: Callable[[str], Path] | None = None,
    ) -> None:
        self._find_project = find_project
        self._resolve_workspace = resolve_workspace
        self._resolve_workspace_async = resolve_workspace_async
        self._resolve_active_path = resolve_active_path
        self._resolve_active_write_target = resolve_active_write_target

    async def read_code_file(self, requested_path: str) -> dict[str, Any]:
        target = self._resolve_code_path(requested_path, write=False)
        if not target.exists():
            raise ProjectFileError(
                localized(
                    "File not found: {path}",
                    "未找到文件：{path}",
                    path=requested_path,
                ),
                404,
                "file_not_found",
            )
        if not target.is_file():
            raise ProjectFileError(
                localized(
                    "The path is not a file: {path}",
                    "该路径不是文件：{path}",
                    path=requested_path,
                ),
                400,
                "not_a_file",
            )
        try:
            content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectFileError(
                localized(
                    "The file is not UTF-8 text.",
                    "该文件不是 UTF-8 文本。",
                ),
                400,
                "not_utf8_text",
            ) from exc
        stat = await asyncio.to_thread(target.stat)
        return {
            "content": content,
            "language": self.code_languages.get(target.suffix.lower(), "text"),
            "size": stat.st_size,
            "path": str(target),
        }

    async def write_code_file(
        self, requested_path: str, content: str
    ) -> dict[str, Any]:
        target = self._resolve_code_path(requested_path, write=True)

        def write() -> int:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return target.stat().st_size

        try:
            size = await asyncio.to_thread(write)
        except OSError as exc:
            logger.warning("Project code file write failed", exc_info=True)
            raise ProjectFileError(
                localized(
                    "The file could not be written.",
                    "无法写入文件。",
                ),
                500,
                "file_write_failed",
            ) from exc
        return {"status": "ok", "path": str(target), "size": size}

    async def read_diff_text(self, requested_path: str) -> str:
        target = self._resolve_code_path(requested_path, write=False)
        if not target.exists():
            raise ProjectFileError(
                localized(
                    "File not found: {path}",
                    "未找到文件：{path}",
                    path=requested_path,
                ),
                404,
                "file_not_found",
            )
        try:
            return await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectFileError(
                localized(
                    "Files must be UTF-8 text.",
                    "文件必须是 UTF-8 文本。",
                ),
                400,
                "not_utf8_text",
            ) from exc

    def resolve_code_path(self, requested_path: str) -> Path:
        return self._resolve_code_path(requested_path, write=False)

    def _resolve_code_path(self, requested_path: str, *, write: bool) -> Path:
        resolver = (
            self._resolve_active_write_target if write else self._resolve_active_path
        )
        if resolver is None:
            raise RuntimeError("Active workspace file resolver is not configured")
        try:
            return resolver(requested_path)
        except ValueError as exc:
            logger.info("Workspace path was rejected", exc_info=True)
            raise ProjectFileError(
                localized(
                    "The requested path is outside the active workspace.",
                    "请求的路径位于当前工作区之外。",
                ),
                403,
                "workspace_path_forbidden",
            ) from exc

    async def list_files(
        self,
        project_id: str,
        requested_path: str = ".",
        query: str = "",
    ) -> dict[str, Any]:
        project = self._project(project_id)
        root = await self._workspace_root_async(project)
        requested = str(requested_path or ".").replace("\\", "/").strip() or "."
        candidate = (root / requested).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise ProjectFileError(
                localized(
                    "The path is outside the project workspace.",
                    "该路径位于项目工作区之外。",
                ),
                400,
                "invalid_workspace_path",
            )
        if not candidate.is_dir():
            raise ProjectFileError(
                localized("Directory not found.", "未找到目录。"),
                404,
                "directory_not_found",
            )

        normalized_query = str(query or "").strip().casefold()

        def entry_payload(item: Path) -> ProjectFileEntryDTO:
            info = item.stat()
            return {
                "name": item.name,
                "path": item.relative_to(root).as_posix(),
                "kind": "directory" if item.is_dir() else "file",
                "size": int(info.st_size) if item.is_file() else 0,
                "modifiedNs": int(info.st_mtime_ns),
            }

        def list_entries() -> list[ProjectFileEntryDTO]:
            entries: list[ProjectFileEntryDTO] = []
            for item in sorted(
                candidate.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())
            ):
                if item.is_symlink() or item.name in {".git", "node_modules", "__pycache__"}:
                    continue
                entries.append(entry_payload(item))
                if len(entries) >= 500:
                    break
            return entries

        def search_entries() -> list[ProjectFileEntryDTO]:
            entries: list[ProjectFileEntryDTO] = []
            ignored = {".git", "node_modules", "__pycache__"}
            for directory, names, filenames in os.walk(root, followlinks=False):
                names[:] = sorted(
                    name
                    for name in names
                    if name not in ignored and not (Path(directory) / name).is_symlink()
                )
                items = [Path(directory) / name for name in names]
                items.extend(Path(directory) / name for name in sorted(filenames))
                for item in items:
                    if item.is_symlink():
                        continue
                    relative = item.relative_to(root).as_posix()
                    if (
                        normalized_query not in item.name.casefold()
                        and normalized_query not in relative.casefold()
                    ):
                        continue
                    entries.append(entry_payload(item))
                    if len(entries) >= 500:
                        return entries
            return entries

        return {
            "path": (
                "."
                if normalized_query or candidate == root
                else candidate.relative_to(root).as_posix()
            ),
            "query": str(query or "").strip(),
            "entries": await asyncio.to_thread(
                search_entries if normalized_query else list_entries
            ),
        }

    async def resolve_preview(self, project_id: str, file_path: str) -> Path:
        project = self._project(project_id)
        root = await self._workspace_root_async(project)
        return self._resolve_file(root, file_path, symlink_action="previewed")

    def read_editable(self, project_id: str, file_path: str) -> EditableProjectFileDTO:
        target = self._editable_target(project_id, file_path)
        return self._editable_text_payload(target)

    async def save_editable(
        self,
        project_id: str,
        file_path: str,
        content: str,
        *,
        expected_version: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        target = self._editable_target(project_id, file_path)
        current = await asyncio.to_thread(self._editable_text_payload, target)
        expected = str(expected_version or "").strip()
        if expected and expected != current["version"] and not force:
            raise ProjectFileError(
                localized(
                    "The file changed after it was opened.",
                    "文件在打开后已发生更改。",
                ),
                409,
                "text_file_conflict",
                {
                    "version": current["version"],
                    "modifiedNs": current["modifiedNs"],
                },
            )

        encoded = content.encode("utf-8")
        if current["bom"]:
            encoded = b"\xef\xbb\xbf" + encoded
        if len(encoded) > self.max_editable_text_bytes:
            raise ProjectFileError(
                localized(
                    "The text file is too large to save.",
                    "文本文件过大，无法保存。",
                ),
                413,
                "text_file_too_large",
                {"maxBytes": self.max_editable_text_bytes},
            )

        def atomic_write() -> dict[str, Any]:
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".cyrene-edit-", dir=target.parent, delete=False
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected and not force:
                    latest = target.read_bytes()
                    latest_version = hashlib.sha256(latest).hexdigest()
                    if latest_version != expected:
                        latest_stat = target.stat()
                        return {
                            "conflict": True,
                            "version": latest_version,
                            "modifiedNs": int(latest_stat.st_mtime_ns),
                        }
                os.chmod(temp_path, target.stat().st_mode)
                os.replace(temp_path, target)
                temp_path = None
                stat = target.stat()
                return {
                    "path": str(file_path).replace("\\", "/"),
                    "version": hashlib.sha256(encoded).hexdigest(),
                    "modifiedNs": int(stat.st_mtime_ns),
                    "size": len(encoded),
                }
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

        try:
            result = await asyncio.to_thread(atomic_write)
        except OSError as exc:
            logger.warning("Editable project file save failed", exc_info=True)
            raise ProjectFileError(
                localized(
                    "The project file could not be saved.",
                    "无法保存项目文件。",
                ),
                403,
                "text_file_not_writable",
            ) from exc
        if result.get("conflict"):
            raise ProjectFileError(
                localized(
                    "The file changed while it was being saved.",
                    "文件在保存期间已发生更改。",
                ),
                409,
                "text_file_conflict",
                {
                    "version": result["version"],
                    "modifiedNs": result["modifiedNs"],
                },
            )
        return result

    def _project(self, project_id: str) -> dict[str, Any]:
        project = self._find_project(project_id)
        if project is None:
            raise ProjectFileError(
                localized("Project not found.", "未找到项目。"),
                404,
                "project_not_found",
            )
        return project

    async def _workspace_root_async(self, project: dict[str, Any]) -> Path:
        raw_root = await self._resolve_workspace_async(project)
        if not raw_root:
            raise ProjectFileError(
                localized(
                    "The project has no workspace.",
                    "该项目没有工作区。",
                ),
                404,
                "workspace_unavailable",
            )
        return Path(raw_root).expanduser().resolve()

    def _editable_target(self, project_id: str, file_path: str) -> Path:
        project = self._project(project_id)
        raw_root = self._resolve_workspace(project)
        if not raw_root:
            raise ProjectFileError(
                localized(
                    "The project has no workspace.",
                    "该项目没有工作区。",
                ),
                404,
                "workspace_unavailable",
            )
        root = Path(raw_root).expanduser().resolve()
        return self._resolve_file(root, file_path, symlink_action="accessed")

    @staticmethod
    def _resolve_file(root: Path, file_path: str, *, symlink_action: str) -> Path:
        requested = str(file_path or "").replace("\\", "/").strip()
        if not requested:
            raise ProjectFileError(
                localized("File path is required.", "必须提供文件路径。"),
                400,
                "invalid_workspace_path",
            )
        cursor = root
        for part in Path(requested).parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                raise ProjectFileError(
                    localized(
                        "Symbolic links cannot be used for this operation.",
                        "此操作不能使用符号链接。",
                    ),
                    403,
                    "symlink_not_allowed",
                )
        try:
            target = (root / requested).resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ProjectFileError(
                localized(
                    "The project file path is invalid.",
                    "项目文件路径无效。",
                ),
                400,
                "invalid_workspace_path",
            ) from exc
        if target != root and root not in target.parents:
            raise ProjectFileError(
                localized(
                    "The path is outside the project workspace.",
                    "该路径位于项目工作区之外。",
                ),
                400,
                "invalid_workspace_path",
            )
        if not target.is_file():
            raise ProjectFileError(
                localized("File not found.", "未找到文件。"),
                404,
                "file_not_found",
            )
        return target

    def _editable_text_payload(self, target: Path) -> EditableProjectFileDTO:
        stat = target.stat()
        if stat.st_size > self.max_editable_text_bytes:
            raise ProjectFileError(
                localized(
                    "The text file is too large to edit.",
                    "文本文件过大，无法编辑。",
                ),
                413,
                "text_file_too_large",
                {"maxBytes": self.max_editable_text_bytes},
            )
        media_type = mimetypes.guess_type(target.name)[0] or ""
        extension = target.suffix.lower()
        normalized_name = target.name.lower()
        if not (
            media_type.startswith("text/")
            or extension in self.editable_text_extensions
            or normalized_name in self.editable_text_names
        ):
            raise ProjectFileError(
                localized(
                    "This file type cannot be edited as text.",
                    "此文件类型不能作为文本编辑。",
                ),
                415,
                "text_file_type_unsupported",
            )
        try:
            raw = target.read_bytes()
            has_bom = raw.startswith(b"\xef\xbb\xbf")
            content = raw.decode("utf-8-sig" if has_bom else "utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectFileError(
                localized(
                    "Only UTF-8 text files can be edited.",
                    "只能编辑 UTF-8 文本文件。",
                ),
                415,
                "text_file_encoding_unsupported",
            ) from exc
        return {
            "content": content,
            "version": hashlib.sha256(raw).hexdigest(),
            "modifiedNs": int(stat.st_mtime_ns),
            "size": len(raw),
            "bom": has_bom,
            "contentType": media_type or "text/plain",
        }


__all__ = [
    "EditableProjectFileDTO",
    "ProjectFileEntryDTO",
    "ProjectFileError",
    "ProjectFileService",
]
