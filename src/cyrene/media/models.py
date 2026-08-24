"""Shared value objects for media jobs and provider outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MEDIA_KINDS = frozenset({"image", "video", "music"})
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(slots=True)
class MediaArtifact:
    """One provider output before it is registered as a Cyrene attachment."""

    filename: str
    content_type: str = "application/octet-stream"
    data: bytes | None = None
    path: Path | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MediaProviderResult:
    artifacts: list[MediaArtifact]
    provider_job_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class MediaProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, code: str = "") -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.code = str(code or "")


__all__ = [
    "MEDIA_KINDS",
    "TERMINAL_JOB_STATUSES",
    "MediaArtifact",
    "MediaProviderError",
    "MediaProviderResult",
]
