"""Agent-facing access to conversation-bound Cyrene Terminal Daemon sessions."""

from __future__ import annotations

import re
from typing import Any, Literal

from cyrene.agent.context import current_run_context
from cyrene.terminal.client import get_terminal_daemon_client
from cyrene.workbench.context import resolve_workbench_project_id_for_session

Access = Literal["read", "write", "show"]

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
    """Keep an explicitly requested terminal name even if the model omits it.

    Tool arguments remain authoritative.  The conservative fallback only
    recognizes direct Chinese/English naming phrases from the current user
    request, preventing a terminal from being silently stored as ``Terminal N``
    while the Agent tells the user that it used their requested name.
    """
    explicit = str(explicit_title or "").strip()
    if explicit:
        return explicit[:60]
    request = str(user_request or "")
    for pattern in _TERMINAL_TITLE_PATTERNS:
        match = pattern.search(request)
        if not match:
            continue
        title = str(match.group("title") or "").strip().strip("\"'“”‘’")
        if title:
            return title[:60]
    return ""


def _context_scope(*, allow_side_question: bool = True) -> tuple[Any, str, str]:
    context = current_run_context()
    chat_id = str(context.session_id or "").strip()
    if not chat_id:
        raise ValueError("Terminal tools require an active Workbench conversation.")
    from cyrene.workbench.chat import get_workbench_chat

    chat = get_workbench_chat(chat_id)
    if (
        not allow_side_question
        and chat
        and str(chat.get("kind") or "") == "side-agent"
    ):
        raise PermissionError("Side questions cannot create terminals.")
    project_id = str(resolve_workbench_project_id_for_session(chat_id) or "").strip()
    if not project_id:
        raise ValueError("The current conversation is not attached to a project.")
    return context, project_id, chat_id


def agent_creation_scope() -> tuple[Any, str, str]:
    context, project_id, chat_id = _context_scope(allow_side_question=False)
    if context.agent_id != "main" or context.caller not in {
        "main", "main_agent", "execution_agent",
    }:
        raise PermissionError("Only the main conversation Agent can create terminals.")
    return context, project_id, chat_id


def _explicit_access(text: str, access: Access) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    mentions_terminal = bool(re.search(r"终端|terminal|shell", normalized))
    if not mentions_terminal:
        return False
    if access == "read":
        return bool(re.search(r"看|查看|读|检查|inspect|look|read|check", normalized))
    if access == "write":
        return bool(re.search(r"操作|输入|执行|按|control|operate|type|input|run|send", normalized))
    return bool(re.search(r"打开|显示|给我看|open|show|reveal", normalized))


async def _surface_current_terminal(ui_instance_id: str) -> str:
    if not ui_instance_id:
        return ""
    from cyrene.workbench.ui_surface import request

    result = await request(ui_instance_id, "terminal.current", {}, timeout=3.0)
    if result.get("ok"):
        return str(result.get("terminalId") or "")
    if str(result.get("error") or "") == "multiple_terminals_visible":
        candidates = [
            dict(item) for item in result.get("terminals") or []
            if isinstance(item, dict)
        ]
        labels = [
            f"{str(item.get('title') or 'Terminal')} ({str(item.get('terminalId') or '')})"
            for item in candidates
        ]
        detail = ", ".join(labels) if labels else "multiple visible terminals"
        raise ValueError(
            "Multiple terminal panes are currently visible: " + detail
            + ". Ask the user which terminal to use, then provide its name or terminal_id."
        )
    return ""


async def animate_terminal_control(terminal_id: str, action: str) -> bool:
    """Show the shared Agent-control animation when the terminal is visible."""
    context = current_run_context()
    if not context.ui_instance_id:
        return False
    from cyrene.workbench.ui_surface import request

    try:
        result = await request(
            context.ui_instance_id,
            "terminal.control",
            {"terminalId": str(terminal_id or ""), "action": str(action or "input")},
            timeout=3.0,
        )
    except Exception:
        # A detached, closing, or stale renderer must never block terminal I/O.
        return False
    return bool(result.get("ok") and result.get("highlighted"))


async def resolve_terminal(
    *, terminal_id: str = "", name: str = "", access: Access = "read",
) -> dict[str, Any]:
    context, project_id, chat_id = _context_scope()
    client = get_terminal_daemon_client()
    listing = await client.list(project_id)
    terminals = [dict(item) for item in listing.get("terminals") or []]
    requested_id = str(terminal_id or "").strip()
    requested_name = str(name or "").strip().casefold()

    if not requested_id and not requested_name:
        requested_id = await _surface_current_terminal(context.ui_instance_id)
        if not requested_id:
            raise ValueError("No terminal is currently open. Provide terminal_id or name.")

    matches = [
        item for item in terminals
        if (requested_id and str(item.get("id") or "") == requested_id)
        or (requested_name and str(item.get("title") or "").strip().casefold() == requested_name)
    ]
    if not matches:
        raise ValueError("Terminal not found in the current project.")
    if requested_name and len(matches) > 1:
        raise ValueError("Multiple terminals have that name; use terminal_id.")
    terminal = matches[0]
    owned = (
        str(terminal.get("ownerChatId") or "") == chat_id
        and str(terminal.get("createdBy") or "") == "agent"
    )
    if not owned and not _explicit_access(context.user_request_text, access):
        verb = {"read": "look at", "write": "operate", "show": "open"}[access]
        raise PermissionError(
            f"The user must explicitly ask the Agent to {verb} this terminal in the current turn."
        )
    return terminal


async def list_agent_terminals(*, include_exited: bool = True) -> list[dict[str, Any]]:
    _context, project_id, chat_id = _context_scope()
    result = await get_terminal_daemon_client().list(project_id, owner_chat_id=chat_id)
    items = [dict(item) for item in result.get("terminals") or []]
    if not include_exited:
        items = [item for item in items if str(item.get("status") or "") in {"starting", "running"}]
    return items


async def show_terminal(terminal_id: str) -> dict[str, Any]:
    context, _project_id, _chat_id = _context_scope()
    terminal = await resolve_terminal(terminal_id=terminal_id, access="show")
    if not context.ui_instance_id:
        raise RuntimeError("The current conversation has no attached UI surface.")
    from cyrene.workbench.ui_surface import request

    result = await request(
        context.ui_instance_id,
        "terminal.show",
        {"terminalId": str(terminal.get("id") or ""), "side": "right"},
        timeout=5.0,
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Could not open the terminal."))
    return terminal


__all__ = [
    "agent_creation_scope", "animate_terminal_control", "list_agent_terminals",
    "requested_terminal_title", "resolve_terminal", "show_terminal",
]
