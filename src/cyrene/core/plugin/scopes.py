"""Lifecycle scope ports for the host-independent Plugin core."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ApplicationPluginScope(Protocol):
    """Minimal process-scope surface consumed by core sessions."""

    plugin_directory: Path
    registry: Any
    runtime: Any
    load_failures: tuple[Any, ...]
    startup_failures: Mapping[str, str]

    @property
    def active_services(self) -> Mapping[str, Any]: ...

    def service(self, name: str) -> Any | None: ...
    def pack_operational(self, pack_id: str) -> bool: ...
    def pack_restart_required(self, pack_id: str) -> bool: ...


_APPLICATION_SCOPE_LOCK = threading.RLock()
_APPLICATION_SCOPE: ApplicationPluginScope | None = None


def set_application_plugin_scope(scope: ApplicationPluginScope | None) -> None:
    global _APPLICATION_SCOPE
    with _APPLICATION_SCOPE_LOCK:
        _APPLICATION_SCOPE = scope


def application_plugin_scope() -> ApplicationPluginScope | None:
    with _APPLICATION_SCOPE_LOCK:
        return _APPLICATION_SCOPE


def application_plugin_service(name: str) -> Any | None:
    scope = application_plugin_scope()
    return scope.service(name) if scope is not None else None


__all__ = [
    "ApplicationPluginScope",
    "application_plugin_scope",
    "application_plugin_service",
    "set_application_plugin_scope",
]
