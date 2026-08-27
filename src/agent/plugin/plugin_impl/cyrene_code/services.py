"""Session services owned by the editable code Plugin pack."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent.plugin import PluginContext, PluginSetupContext
from agent.plugin.native_runtime import run_context_value

_TERMINAL_TITLE_PATTERNS = (
    re.compile(
        r"(?:名为|名称(?:是|为)|叫作?|命名为)\s*[\"'“‘]"
        r"(?P<title>[^\"'”’\r\n]{1,60})[\"'”’]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:terminal|shell)\s+(?:named|called)\s+[\"'“‘]"
        r"(?P<title>[^\"'”’\r\n]{1,60})[\"'”’]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:名为|名称(?:是|为)|叫作?|命名为)\s*"
        r"(?P<title>[^\"'”’\s，。；：,;:]{1,60})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:terminal|shell)\s+(?:named|called)\s+"
        r"(?P<title>[^\"'”’\s,.;:]{1,60})",
        re.IGNORECASE,
    ),
)


def requested_terminal_title(explicit_title: str, user_request: str) -> str:
    """Honor an explicit title, with a conservative user-request fallback."""

    explicit = str(explicit_title or "").strip()
    if explicit:
        return explicit[:60]
    request = str(user_request or "")
    for pattern in _TERMINAL_TITLE_PATTERNS:
        match = pattern.search(request)
        if match:
            title = str(match.group("title") or "").strip().strip("\"'“”‘’")
            if title:
                return title[:60]
    return ""


@runtime_checkable
class TerminalService(Protocol):
    """Host port used by shell Plugins."""

    async def resolve(
        self,
        context: PluginContext,
        *,
        terminal_id: str = "",
        name: str = "",
    ) -> dict[str, Any]: ...

    async def create(
        self,
        context: PluginContext,
        **arguments: Any,
    ) -> dict[str, Any]: ...

    async def screen(self, terminal_id: str) -> dict[str, Any]: ...

    async def scrollback(
        self,
        terminal_id: str,
        *,
        cursor: int | None,
        max_bytes: int,
    ) -> dict[str, Any]: ...

    async def commands(self, terminal_id: str) -> dict[str, Any]: ...

    async def command_output(
        self,
        terminal_id: str,
        command_id: str,
    ) -> dict[str, Any]: ...

    async def input(self, terminal_id: str, data: str) -> dict[str, Any]: ...

    async def interrupt(self, terminal_id: str) -> dict[str, Any]: ...

    async def remove(self, terminal_id: str) -> dict[str, Any]: ...

    async def list_owned(
        self,
        context: PluginContext,
        *,
        include_exited: bool = True,
    ) -> list[dict[str, Any]]: ...

    async def list_visible(
        self,
        context: PluginContext,
    ) -> list[dict[str, Any]]: ...

    async def animate(
        self,
        context: PluginContext,
        terminal_id: str,
        action: str,
    ) -> bool: ...

    async def show(
        self,
        context: PluginContext,
        terminal_id: str,
    ) -> dict[str, Any]: ...


def _scope(context: PluginContext) -> tuple[str, str, str]:
    project_id = str(run_context_value(context, "project_id") or "").strip()
    session_id = str(run_context_value(context, "session_id") or "").strip()
    ui_instance_id = str(run_context_value(context, "ui_instance_id") or "").strip()
    if not session_id:
        raise ValueError("Terminal tools require an active conversation session.")
    if not project_id:
        raise ValueError("The current Plugin session is not attached to a project.")
    return project_id, session_id, ui_instance_id


async def _surface_request(
    ui_instance_id: str,
    method: str,
    arguments: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    if not ui_instance_id:
        return {"ok": False, "error": "no_current_surface"}
    from cyrene.workbench.ui_surface import request

    return await request(ui_instance_id, method, arguments, timeout=timeout)


class CyreneTerminalService:
    """Native adapter from the Plugin port to Cyrene's terminal daemon and UI."""

    @staticmethod
    def _client() -> Any:
        from cyrene.terminal.client import get_terminal_daemon_client

        return get_terminal_daemon_client()

    async def create(
        self,
        context: PluginContext,
        **arguments: Any,
    ) -> dict[str, Any]:
        project_id, session_id, _ui_instance_id = _scope(context)
        agent_id = str(run_context_value(context, "agent_id", "main") or "main")
        caller = str(run_context_value(context, "caller", "main_agent") or "")
        if agent_id != "main" or caller not in {
            "main",
            "main_agent",
            "execution_agent",
        }:
            raise PermissionError("Only the main Agent can create terminals.")
        return await self._client().create_agent_terminal(
            project_id,
            owner_chat_id=session_id,
            **arguments,
        )

    async def screen(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().screen(str(terminal_id or ""))

    async def scrollback(
        self,
        terminal_id: str,
        *,
        cursor: int | None,
        max_bytes: int,
    ) -> dict[str, Any]:
        return await self._client().scrollback(
            str(terminal_id or ""),
            cursor=cursor,
            max_bytes=max_bytes,
        )

    async def commands(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().commands(str(terminal_id or ""))

    async def command_output(
        self,
        terminal_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        return await self._client().command_output(
            str(terminal_id or ""),
            str(command_id or ""),
        )

    async def input(self, terminal_id: str, data: str) -> dict[str, Any]:
        return await self._client().input(str(terminal_id or ""), data)

    async def interrupt(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().interrupt(str(terminal_id or ""))

    async def remove(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().remove(str(terminal_id or ""))

    async def _current_terminal_id(self, ui_instance_id: str) -> str:
        result = await _surface_request(
            ui_instance_id,
            "terminal.current",
            {},
            timeout=3.0,
        )
        if result.get("ok"):
            return str(result.get("terminalId") or "")
        if str(result.get("error") or "") == "multiple_terminals_visible":
            candidates = [
                dict(item)
                for item in result.get("terminals") or []
                if isinstance(item, dict)
            ]
            labels = [
                f"{str(item.get('title') or 'Terminal')} "
                f"({str(item.get('terminalId') or '')})"
                for item in candidates
            ]
            detail = ", ".join(labels) if labels else "multiple visible terminals"
            raise ValueError(
                "Multiple terminal panes are currently visible: "
                + detail
                + ". Provide a terminal name or terminal_id."
            )
        return ""

    async def resolve(
        self,
        context: PluginContext,
        *,
        terminal_id: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        project_id, _session_id, ui_instance_id = _scope(context)
        requested_id = str(terminal_id or "").strip()
        requested_name = str(name or "").strip().casefold()
        if not requested_id and not requested_name:
            requested_id = await self._current_terminal_id(ui_instance_id)
            if not requested_id:
                raise ValueError("No terminal is currently open. Provide terminal_id or name.")

        listing = await self._client().list(project_id)
        terminals = [dict(item) for item in listing.get("terminals") or []]
        matches = [
            item
            for item in terminals
            if (requested_id and str(item.get("id") or "") == requested_id)
            or (
                requested_name
                and str(item.get("title") or "").strip().casefold()
                == requested_name
            )
        ]
        if not matches:
            raise ValueError("Terminal not found in the current project.")
        if requested_name and len(matches) > 1:
            raise ValueError("Multiple terminals have that name; use terminal_id.")
        return matches[0]

    async def list_owned(
        self,
        context: PluginContext,
        *,
        include_exited: bool = True,
    ) -> list[dict[str, Any]]:
        project_id, session_id, _ui_instance_id = _scope(context)
        result = await self._client().list(project_id, owner_chat_id=session_id)
        items = [dict(item) for item in result.get("terminals") or []]
        if include_exited:
            return items
        return [
            item
            for item in items
            if str(item.get("status") or "") in {"starting", "running"}
        ]

    async def list_visible(
        self,
        context: PluginContext,
    ) -> list[dict[str, Any]]:
        project_id, _session_id, ui_instance_id = _scope(context)
        result = await _surface_request(
            ui_instance_id,
            "terminal.current",
            {},
            timeout=3.0,
        )
        candidates = [
            dict(item)
            for item in result.get("terminals") or []
            if isinstance(item, dict) and str(item.get("terminalId") or "")
        ]
        current_id = str(result.get("terminalId") or "")
        if current_id and not any(
            str(item.get("terminalId") or "") == current_id for item in candidates
        ):
            candidates.append({"terminalId": current_id})
        if not candidates:
            return []

        listing = await self._client().list(project_id)
        by_id = {
            str(item.get("id") or ""): dict(item)
            for item in listing.get("terminals") or []
        }
        visible: list[dict[str, Any]] = []
        for candidate in candidates:
            terminal = by_id.get(str(candidate.get("terminalId") or ""))
            if terminal is None:
                continue
            terminal["visible"] = True
            terminal["visibleSide"] = str(candidate.get("side") or "")
            visible.append(terminal)
        return visible

    async def animate(
        self,
        context: PluginContext,
        terminal_id: str,
        action: str,
    ) -> bool:
        _project_id, _session_id, ui_instance_id = _scope(context)
        try:
            result = await _surface_request(
                ui_instance_id,
                "terminal.control",
                {
                    "terminalId": str(terminal_id or ""),
                    "action": str(action or "input"),
                },
                timeout=3.0,
            )
        except Exception:
            return False
        return bool(result.get("ok") and result.get("highlighted"))

    async def show(
        self,
        context: PluginContext,
        terminal_id: str,
    ) -> dict[str, Any]:
        _project_id, _session_id, ui_instance_id = _scope(context)
        if not ui_instance_id:
            raise RuntimeError("The current Plugin session has no attached UI surface.")
        terminal = await self.resolve(context, terminal_id=terminal_id)
        result = await _surface_request(
            ui_instance_id,
            "terminal.show",
            {"terminalId": str(terminal.get("id") or ""), "side": "right"},
            timeout=5.0,
        )
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "Could not open the terminal."))
        return terminal


def terminal_service(context: PluginContext) -> TerminalService:
    service = context.services.get("terminals")
    if not isinstance(service, TerminalService):
        raise RuntimeError("The code Plugin pack requires the terminals service")
    return service


def setup(context: PluginSetupContext) -> None:
    """Publish session-scoped code services exactly once."""

    if "terminals" not in context.services:
        context.provide("terminals", CyreneTerminalService())
    if "code_index_db" not in context.services:
        workspace_key = sha256(
            str(Path(context.workspace).expanduser().resolve()).encode("utf-8")
        ).hexdigest()[:20]
        context.provide(
            "code_index_db",
            Path(context.data_directory).expanduser().resolve()
            / "code-index"
            / f"{workspace_key}.db",
        )


__all__ = [
    "CyreneTerminalService",
    "TerminalService",
    "requested_terminal_title",
    "setup",
    "terminal_service",
]
