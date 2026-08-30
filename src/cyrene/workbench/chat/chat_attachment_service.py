"""Resolve files that are explicitly referenced by Workbench chat records."""

from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from cyrene.platform.attachments import (
    EXPORTS_DIR,
    UPLOADS_DIR,
    image_dimensions,
    attachment_kind_from_meta,
    build_public_attachment_payload,
    safe_attachment_filename,
)


class UploadSource(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


class ChatAttachmentError(Exception):
    """A request-safe error raised by the Workbench attachment service."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ChatAttachmentService:
    """Own Workbench upload persistence and managed-file resolution."""

    def __init__(
        self,
        *,
        uploads_dir: Path = UPLOADS_DIR,
        exports_dir: Path = EXPORTS_DIR,
    ) -> None:
        self.uploads_dir = Path(uploads_dir)
        self.exports_dir = Path(exports_dir)

    async def upload(self, files: Sequence[UploadSource]) -> dict[str, Any]:
        if not files:
            raise ChatAttachmentError("no files uploaded", status_code=400)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        uploaded = [await self._store_upload(source) for source in files]
        return {"files": uploaded}

    def resolve_upload(self, attachment_id: str) -> Path:
        return self._resolve_managed_file(
            self.uploads_dir, attachment_id, kind="upload"
        )

    def resolve_export(self, attachment_id: str) -> Path:
        return self._resolve_managed_file(
            self.exports_dir, attachment_id, kind="export"
        )

    async def _store_upload(self, source: UploadSource) -> dict[str, Any]:
        safe_name = safe_attachment_filename(source.filename or "upload.bin", "upload")
        target = self.uploads_dir / f"{uuid.uuid4().hex}_{safe_name}"
        size = 0
        try:
            with target.open("wb") as destination:
                while chunk := await source.read(65_536):
                    destination.write(chunk)
                    size += len(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        content_type = str(
            source.content_type
            or mimetypes.guess_type(str(target))[0]
            or "application/octet-stream"
        )
        kind = attachment_kind_from_meta(content_type, target.name)
        width, height = (
            image_dimensions(target) if kind == "image" else (None, None)
        )
        return {
            "id": target.name,
            "name": source.filename or safe_name,
            "path": str(target.resolve()),
            "content_type": content_type,
            "size": size,
            "kind": kind,
            "url": f"/api/workbench/uploads/{target.name}",
            **({"width": width} if isinstance(width, int) else {}),
            **({"height": height} if isinstance(height, int) else {}),
        }

    @staticmethod
    def _validate_attachment_id(attachment_id: str) -> str:
        value = str(attachment_id or "").strip()
        if (
            not value
            or value in {".", ".."}
            or "\x00" in value
            or "/" in value
            or "\\" in value
            or Path(value).name != value
        ):
            raise ChatAttachmentError("invalid attachment path", status_code=400)
        return value

    def _resolve_managed_file(
        self,
        root: Path,
        attachment_id: str,
        *,
        kind: str,
    ) -> Path:
        value = self._validate_attachment_id(attachment_id)
        resolved_root = root.resolve()
        target = (resolved_root / value).resolve()
        if target.parent != resolved_root:
            raise ChatAttachmentError("invalid attachment path", status_code=400)
        if not target.is_file():
            raise ChatAttachmentError(f"{kind} not found", status_code=404)
        return target


def normalize_chat_attachments(attachments: Any) -> list[dict[str, Any]]:
    """Validate the private attachment records accepted by an Agent turn."""

    items = attachments if isinstance(attachments, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        record: dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "file"),
            "path": path,
            "content_type": str(
                item.get("content_type") or "application/octet-stream"
            ),
            "size": max(0, int(item.get("size") or 0)),
            "kind": str(item.get("kind") or "file"),
        }
        for dimension in ("width", "height"):
            value = item.get(dimension)
            if str(value if value is not None else "").strip().isdigit():
                record[dimension] = int(value)
        normalized.append(record)
    return normalized


def public_chat_attachment(item: dict[str, Any]) -> dict[str, Any]:
    """Return an attachment payload with no private filesystem path."""

    return build_public_attachment_payload(item)


def attachment_prompt_block(items: list[dict[str, Any]]) -> str:
    """Tell the model how to inspect the exact uploaded files it received."""

    if not items:
        return ""
    lines = [
        "",
        "[Uploaded attachments]",
        (
            "The user uploaded the following files into the local "
            "workspace-accessible runtime data directory."
        ),
        (
            "Before answering anything about these files, inspect each relevant "
            "attachment with the attachment-analysis Plugin."
        ),
        "Do not infer file contents from a filename, extension, or metadata alone.",
        (
            "If an attachment is missing or unavailable, stop and ask the user "
            "to upload it again."
        ),
        (
            "Do not scan unrelated device directories for a replacement copy."
        ),
    ]
    lines.extend(
        f'- {item["name"]} ({item["content_type"]}): {item["path"]}'
        for item in items
    )
    return "\n".join(lines)


def referenced_chat_attachment_target(
    chat: dict[str, Any],
    attachment_id: str,
) -> tuple[dict[str, Any], Path]:
    """Return a referenced attachment without searching outside owned roots."""

    attachment = next(
        (
            item
            for message in chat.get("messages") or []
            if isinstance(message, dict)
            for item in message.get("attachments") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == str(attachment_id)
        ),
        None,
    )
    if attachment is None:
        raise LookupError("attachment is not referenced by this chat")

    referenced_path = str(attachment.get("path") or "").strip()
    if referenced_path:
        candidate = Path(referenced_path).expanduser().resolve()
        if candidate.exists() and candidate.is_file():
            return attachment, candidate

    url = str(attachment.get("url") or "")
    if url.startswith("/api/workbench/uploads/"):
        candidate_roots = (UPLOADS_DIR,)
    elif url.startswith("/api/workbench/exports/"):
        candidate_roots = (EXPORTS_DIR,)
    else:
        candidate_roots = (EXPORTS_DIR, UPLOADS_DIR)
    route_name = Path(url).name if url else Path(str(attachment_id)).name
    for root in candidate_roots:
        candidate = (root / route_name).resolve()
        if (
            candidate.exists()
            and candidate.is_file()
            and candidate.parent == root.resolve()
        ):
            return attachment, candidate
    raise FileNotFoundError("referenced attachment file is unavailable")


__all__ = [
    "ChatAttachmentError",
    "ChatAttachmentService",
    "UploadSource",
    "attachment_prompt_block",
    "normalize_chat_attachments",
    "public_chat_attachment",
    "referenced_chat_attachment_target",
]
