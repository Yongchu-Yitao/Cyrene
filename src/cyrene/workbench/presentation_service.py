"""Public Workbench presentation and conversation-data boundaries."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.plugin import active_plugin_service
from cyrene.config import DB_PATH
from cyrene.workbench import presentation_runtime
from cyrene.workbench.session_presentation import (
    WorkbenchSessionExport,
    WorkbenchSessionPresentation,
)

logger = logging.getLogger(__name__)

_CORE_SEARCH_TYPES = frozenset({"project", "task", "chat"})


class PresentationQueryService:
    """Expose read-only UI projections without leaking runtime internals to HTTP."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        frontend_modules: Sequence[str] = (),
        search_providers: Mapping[str, Callable[[str, int], Any]] | None = None,
        plugin_host: Any = None,
    ) -> None:
        self._db_path = str(Path(db_path or DB_PATH).expanduser().resolve())
        self._plugin_host = plugin_host
        self._frontend_modules = tuple(
            dict.fromkeys(
                str(module or "").strip()
                for module in frontend_modules
                if str(module or "").strip()
            )
        )
        self._search_providers = dict(search_providers or {})

    def _current_frontend_modules(self) -> tuple[str, ...]:
        values = (
            self._plugin_host.frontend_modules
            if self._plugin_host is not None
            else self._frontend_modules
        )
        return tuple(
            dict.fromkeys(
                str(module or "").strip()
                for module in values
                if str(module or "").strip()
            )
        )

    def _current_search_providers(self) -> dict[str, Callable[[str, int], Any]]:
        if self._plugin_host is not None:
            return dict(self._plugin_host.search_providers)
        return dict(self._search_providers)

    @property
    def search_types(self) -> frozenset[str]:
        """Return every result type currently reachable through global search."""

        return _CORE_SEARCH_TYPES | frozenset(self._current_search_providers())

    async def search_workbench(
        self,
        query: str,
        types: set[str],
        per_type_limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        providers = self._current_search_providers()
        provider_types = set(types) & set(providers)
        results = await presentation_runtime._search_workbench_items(
            query,
            set(types) - provider_types,
            per_type_limit,
            self._db_path,
        )
        for result_type in provider_types:
            try:
                value = providers[result_type](query, per_type_limit)
                if inspect.isawaitable(value):
                    value = await value
                results[result_type] = (
                    [dict(item) for item in value if isinstance(item, Mapping)]
                    if isinstance(value, (list, tuple))
                    else []
                )
            except Exception:
                logger.exception("Plugin Workbench search failed for %s", result_type)
                results[result_type] = []
        return results

    async def ui_data(self, timezone_name: str = "") -> dict[str, Any]:
        payload = await presentation_runtime._build_ui_data(
            timezone_name,
            self._db_path,
        )
        payload["pluginModules"] = list(self._current_frontend_modules())
        return payload

    async def dashboard(self, timezone_name: str = "") -> dict[str, Any]:
        timezone = presentation_runtime._resolve_ui_tz(timezone_name)
        return await presentation_runtime._build_dashboard(timezone, self._db_path)

    def config(self) -> dict[str, Any]:
        return presentation_runtime._build_config()

    def user(self) -> dict[str, Any]:
        return presentation_runtime._build_user()

class WorkbenchSessionApplicationService:
    """Manage Workbench conversation presentation, export, and cleanup."""

    def __init__(
        self,
        db_path: str | Path,
    ) -> None:
        self._presentation = WorkbenchSessionPresentation(db_path)

    async def list_sessions(self) -> dict[str, Any]:
        return {
            "sessions": await asyncio.to_thread(self._presentation.list),
        }

    async def clear_session(self, chat_id: str) -> dict[str, Any]:
        session, deleted_archives = await asyncio.to_thread(
            self._presentation.clear,
            chat_id,
        )
        return {
            "ok": True,
            "session": session,
            "deletedArchives": deleted_archives,
        }

    async def delete_session(self, chat_id: str) -> dict[str, Any]:
        deleted_archives = await asyncio.to_thread(
            self._presentation.delete,
            chat_id,
        )
        return {"ok": True, "deletedArchives": deleted_archives}

    async def export_session(
        self,
        chat_id: str,
        output_format: str,
    ) -> WorkbenchSessionExport:
        return await asyncio.to_thread(
            self._presentation.export,
            chat_id,
            output_format,
        )


__all__ = [
    "PresentationQueryService",
    "WorkbenchSessionApplicationService",
]
