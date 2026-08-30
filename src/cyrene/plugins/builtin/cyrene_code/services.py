"""Session services owned by the editable code Plugin pack."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import PluginContext, PluginSetupContext
from cyrene.plugins.native_runtime import plugin_localized, run_context_value

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
        raise ValueError(plugin_localized(
            context,
            "Terminal tools require an active conversation session.",
            "终端工具需要处于活动的会话中。",
        ))
    if not project_id:
        raise ValueError(plugin_localized(
            context,
            "The current Plugin session is not attached to a project.",
            "当前插件会话未关联项目。",
        ))
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
    from cyrene.workbench.ui.ui_surface import request

    return await request(ui_instance_id, method, arguments, timeout=timeout)


class CyreneTerminalService:
    """Native adapter from the Plugin port to Cyrene's terminal daemon and UI."""

    @staticmethod
    def _client() -> Any:
        from .terminal.client import get_terminal_daemon_client

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
            raise PermissionError(plugin_localized(
                context,
                "Only the main Agent can create terminals.",
                "只有主 Agent 可以创建终端。",
            ))
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

    async def _current_terminal_id(
        self,
        ui_instance_id: str,
        context: PluginContext | None = None,
    ) -> str:
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
                f"{str(item.get('title') or plugin_localized(context, 'Terminal', '终端'))} "
                f"({str(item.get('terminalId') or '')})"
                for item in candidates
            ]
            detail = ", ".join(labels) if labels else plugin_localized(
                context,
                "multiple visible terminals",
                "多个可见终端",
            )
            raise ValueError(
                plugin_localized(
                    context,
                    "Multiple terminal panes are currently visible: {detail}. "
                    "Provide a terminal name or terminal_id.",
                    "当前有多个可见终端窗格：{detail}。请提供终端名称或 terminal_id。",
                    detail=detail,
                )
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
            requested_id = await self._current_terminal_id(ui_instance_id, context)
            if not requested_id:
                raise ValueError(plugin_localized(
                    context,
                    "No terminal is currently open. Provide terminal_id or name.",
                    "当前没有打开的终端。请提供 terminal_id 或名称。",
                ))

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
            raise ValueError(plugin_localized(
                context,
                "Terminal not found in the current project.",
                "当前项目中未找到该终端。",
            ))
        if requested_name and len(matches) > 1:
            raise ValueError(plugin_localized(
                context,
                "Multiple terminals have that name; use terminal_id.",
                "有多个终端使用该名称；请改用 terminal_id。",
            ))
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
            raise RuntimeError(plugin_localized(
                context,
                "The current Plugin session has no attached UI surface.",
                "当前插件会话未关联界面。",
            ))
        terminal = await self.resolve(context, terminal_id=terminal_id)
        result = await _surface_request(
            ui_instance_id,
            "terminal.show",
            {"terminalId": str(terminal.get("id") or ""), "side": "right"},
            timeout=5.0,
        )
        if not result.get("ok"):
            error_code = str(result.get("error") or "surface_error")
            raise RuntimeError(plugin_localized(
                context,
                "Could not open the terminal ({error_code}).",
                "无法打开终端（{error_code}）。",
                error_code=error_code,
            ))
        return terminal


@runtime_checkable
class RemoteShellService(Protocol):
    """Process-level terminal port used by the encrypted remote gateway."""

    async def create(
        self,
        project_id: str,
        *,
        cwd: str,
        title: str,
    ) -> dict[str, Any]: ...

    async def screen(self, terminal_id: str) -> dict[str, Any]: ...

    async def input(
        self,
        terminal_id: str,
        data: str,
        *,
        actor: str,
    ) -> dict[str, Any]: ...

    async def interrupt(self, terminal_id: str) -> dict[str, Any]: ...

    async def remove(self, terminal_id: str) -> dict[str, Any]: ...


class CyreneRemoteShellService:
    """Application-owned facade for project-scoped remote shell sessions."""

    @staticmethod
    def _client() -> Any:
        from .terminal.client import get_terminal_daemon_client

        return get_terminal_daemon_client()

    async def create(
        self,
        project_id: str,
        *,
        cwd: str,
        title: str,
    ) -> dict[str, Any]:
        return await self._client().create(project_id, cwd=cwd, title=title)

    async def screen(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().screen(str(terminal_id or ""))

    async def input(
        self,
        terminal_id: str,
        data: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        return await self._client().input(
            str(terminal_id or ""),
            data,
            actor=str(actor or "user"),
        )

    async def interrupt(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().interrupt(str(terminal_id or ""))

    async def remove(self, terminal_id: str) -> dict[str, Any]:
        return await self._client().remove(str(terminal_id or ""))


def terminal_service(context: PluginContext) -> TerminalService:
    service = context.services.get("terminals")
    if not isinstance(service, TerminalService):
        raise RuntimeError(plugin_localized(
            context,
            "The terminal service is unavailable.",
            "终端服务不可用。",
        ))
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


def setup_application(context: PluginApplicationContext) -> None:
    """Publish code/terminal routes and services while the pack is active."""

    from cyrene.config import WORKSPACE_DIR
    from .code_format_service import CodeFormatService
    from .project_files import ProjectFileService
    from cyrene.workbench.projects.project_repository import (
        find_workbench_project_lightweight,
        resolve_project_workspace_dir,
        resolve_project_workspace_dir_async,
    )
    from .workspace_diff_service import WorkspaceDiffService
    from .workspace_execution import WorkspaceExecutionService
    from cyrene.plugins import WORKSPACE_PROJECT_TYPE
    from .code_routes import register_code_routes
    from .terminal_routes import register_terminal_routes
    from .terminal_wake import get_shell_wake_service

    workspace_root = Path(WORKSPACE_DIR).expanduser().resolve()

    def resolve_active_path(path_value: str) -> Path:
        candidate = Path(str(path_value or ".")).expanduser()
        resolved = (
            candidate if candidate.is_absolute() else workspace_root / candidate
        ).resolve()
        if resolved != workspace_root and workspace_root not in resolved.parents:
            from cyrene.localization import localized

            raise ValueError(localized(
                "Path is outside the Cyrene workspace.",
                "路径不在 Cyrene 工作区内。",
            ))
        return resolved

    files = ProjectFileService(
        find_project=find_workbench_project_lightweight,
        resolve_workspace=resolve_project_workspace_dir,
        resolve_workspace_async=resolve_project_workspace_dir_async,
        resolve_active_path=resolve_active_path,
        resolve_active_write_target=resolve_active_path,
    )
    terminal_client = CyreneTerminalService._client()
    application_extensions = context.services.get("plugin_application_extensions")

    def project_type_provider():
        if not callable(application_extensions):
            return ()
        return application_extensions(WORKSPACE_PROJECT_TYPE)

    execution = WorkspaceExecutionService(
        db_path=context.db_path,
        state_path=context.data_directory / "workspace-executions.json",
        terminal_client=terminal_client,
        find_project=find_workbench_project_lightweight,
        resolve_workspace=resolve_project_workspace_dir,
        project_type_provider=project_type_provider,
    )
    register_terminal_routes(context.router)
    register_code_routes(
        context.router,
        files,
        WorkspaceDiffService(files, workspace_root),
        CodeFormatService(context.data_directory / "format"),
        execution,
    )

    context.provide("remote_shell", CyreneRemoteShellService())
    wake_bridge = get_shell_wake_service()
    context.provide("terminal_client", terminal_client)
    context.provide("workspace_execution", execution)
    context.provide("terminal_wake", wake_bridge)
    context.on_startup(wake_bridge.start_daemon_bridge)
    context.on_shutdown(wake_bridge.stop_daemon_bridge)
    context.on_shutdown(execution.shutdown)
    context.expose_frontend("code")


__all__ = [
    "CyreneTerminalService",
    "CyreneRemoteShellService",
    "RemoteShellService",
    "TerminalService",
    "requested_terminal_title",
    "setup",
    "setup_application",
    "terminal_service",
]
