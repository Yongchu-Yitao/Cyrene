"""Provider-neutral contract for background media generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from ..models import MediaProviderResult


ProgressCallback = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]


class MediaProvider(ABC):
    """One stateless adapter for a configured media service."""

    name: str
    supported_kinds: frozenset[str]

    def supports(self, kind: str) -> bool:
        return str(kind or "").strip().lower() in self.supported_kinds

    @abstractmethod
    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        """Generate media and return durable bytes or paths, never ephemeral URLs."""


async def emit_progress(
    progress: ProgressCallback,
    message: str,
    *,
    provider_job_id: str = "",
    state: dict[str, Any] | None = None,
) -> None:
    await progress(str(message or ""), str(provider_job_id or ""), state)


__all__ = ["MediaProvider", "ProgressCallback", "emit_progress"]
