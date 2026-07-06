"""Central registry for Cyrene native tools and tool groups."""

from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_NATIVE_TOOL_MODULES = [
    "cyrene.tool_impl.send_telegram",  # send_telegram
    "cyrene.tool_impl.send_message",  # send_message
    "cyrene.tool_impl.send_message_to_user",  # send_message_to_user
    "cyrene.tool_impl.send_file",  # send_file
    "cyrene.tool_impl.ask_user",  # ask_user
    "cyrene.tool_impl.enter_plan_mode",  # enter_plan_mode
    "cyrene.tool_impl.update_plan_progress",  # update_plan_progress
    "cyrene.tool_impl.deep_reflect",  # DeepReflect
    "cyrene.tool_impl.prompt_claude_code",  # PromptClaudeCode
    "cyrene.tool_impl.schedule_task",  # schedule_task
    "cyrene.tool_impl.list_tasks",  # list_tasks
    "cyrene.tool_impl.pause_task",  # pause_task
    "cyrene.tool_impl.resume_task",  # resume_task
    "cyrene.tool_impl.cancel_task",  # cancel_task
    "cyrene.tool_impl.read",  # Read
    "cyrene.tool_impl.write",  # Write
    "cyrene.tool_impl.edit",  # Edit
    "cyrene.tool_impl.analyze_attachment",  # AnalyzeAttachment
    "cyrene.tool_impl.glob",  # Glob
    "cyrene.tool_impl.grep",  # Grep
    "cyrene.tool_impl.bash",  # Bash
    "cyrene.tool_impl.recall_memory",  # RecallMemory
    "cyrene.tool_impl.recall_conversation",  # RecallConversation
    "cyrene.tool_impl.retire_short_term_memory",  # retire_short_term_memory
    "cyrene.tool_impl.search_project_memory",  # search_project_memory
    "cyrene.tool_impl.save_project_memory",  # save_project_memory
    "cyrene.tool_impl.retire_project_memory",  # retire_project_memory
    "cyrene.tool_impl.set_task_goal",  # set_task_goal
    "cyrene.tool_impl.update_task_plan",  # update_task_plan
    "cyrene.tool_impl.list_knowledge_documents",  # ListKnowledgeDocuments
    "cyrene.tool_impl.search_knowledge",  # SearchKnowledge
    "cyrene.tool_impl.start_shell",  # StartShell
    "cyrene.tool_impl.send_shell",  # SendShell
    "cyrene.tool_impl.list_shells",  # ListShells
    "cyrene.tool_impl.close_shell",  # CloseShell
    "cyrene.tool_impl.web_fetch",  # WebFetch
    "cyrene.tool_impl.web_search",  # WebSearch
    "cyrene.tool_impl.quit",  # quit
    "cyrene.tool_impl.send_agent_message",  # send_agent_message
    "cyrene.tool_impl.broadcast_agent_message",  # broadcast_agent_message
    "cyrene.tool_impl.spawn_subagent",  # spawn_subagent
    "cyrene.tool_impl.query_round",  # query_round
    "cyrene.tool_impl.check_claude_code",  # CheckClaudeCode
    "cyrene.tool_impl.start_claude_code",  # StartClaudeCode
    "cyrene.tool_impl.install_skill",  # InstallSkill
    "cyrene.tool_impl.uninstall_skill",  # UninstallSkill
    "cyrene.tool_impl.list_skills",  # ListSkills
    "cyrene.tool_impl.send_wechat_file",  # send_wechat_file
    "cyrene.tool_impl.browser_navigate",  # browser_navigate
    "cyrene.tool_impl.browser_snapshot",  # browser_snapshot
    "cyrene.tool_impl.browser_screenshot",  # browser_screenshot
    "cyrene.tool_impl.browser_click",  # browser_click
    "cyrene.tool_impl.browser_click_ref",  # browser_click_ref
    "cyrene.tool_impl.browser_click_text",  # browser_click_text
    "cyrene.tool_impl.browser_click_at",  # browser_click_at
    "cyrene.tool_impl.browser_type",  # browser_type
    "cyrene.tool_impl.browser_type_ref",  # browser_type_ref
    "cyrene.tool_impl.browser_wait",  # browser_wait
    "cyrene.tool_impl.browser_network_log",  # browser_network_log
    "cyrene.tool_impl.browser_tab_list",  # browser_tab_list
    "cyrene.tool_impl.browser_tab_new",  # browser_tab_new
    "cyrene.tool_impl.browser_tab_select",  # browser_tab_select
    "cyrene.tool_impl.browser_tab_close",  # browser_tab_close
    "cyrene.tool_impl.browser_scroll",  # browser_scroll
    "cyrene.tool_impl.browser_user_events",  # browser_user_events
    "cyrene.tool_impl.browser_request_takeover",  # browser_request_takeover
    "cyrene.tool_impl.send_notification",  # send_notification
    "cyrene.tool_impl.track_entity",  # track_entity
    "cyrene.tool_impl.update_entity",  # update_entity
    "cyrene.tool_impl.list_entities",  # list_entities
    "cyrene.tool_impl.query_entities",  # query_entities
    "cyrene.tool_impl.delete_entity",  # delete_entity
]

# Tools that only the main agent may use. Subagents get the same registry with
# these names filtered out at selection time.
_MAIN_ONLY_TOOLS = {
    "send_telegram",
    "send_message",
    "send_file",
    "send_wechat_file",
    "ask_user",
    "enter_plan_mode",
    "update_plan_progress",
    "DeepReflect",
    "retire_short_term_memory",
    "save_project_memory",
    "retire_project_memory",
    "set_task_goal",
    "update_task_plan",
    "spawn_subagent",
    "query_round",
    "browser_navigate",
    "browser_snapshot",
    "browser_screenshot",
    "browser_click",
    "browser_click_ref",
    "browser_click_text",
    "browser_click_at",
    "browser_type",
    "browser_type_ref",
    "browser_wait",
    "browser_network_log",
    "browser_tab_list",
    "browser_tab_new",
    "browser_tab_select",
    "browser_tab_close",
    "browser_scroll",
    "browser_user_events",
    "browser_request_takeover",
}

AGENT_TOOL_GROUPS: dict[str, set[str]] = {
    "main": set(),
    "subagent_blocklist": set(_MAIN_ONLY_TOOLS),
}

TOOL_DEFS: list[dict[str, Any]] = []
TOOL_HANDLERS: dict[str, Any] = {}


def register_tool(tool_def: dict[str, Any], handler: Any | None = None) -> None:
    """Register or replace one tool definition and optionally its handler."""
    name = str((tool_def.get("function") or {}).get("name") or "").strip()
    if not name:
        raise ValueError("tool definition is missing function.name")
    for index, existing in enumerate(TOOL_DEFS):
        existing_name = str((existing.get("function") or {}).get("name") or "")
        if existing_name == name:
            TOOL_DEFS[index] = tool_def
            break
    else:
        TOOL_DEFS.append(tool_def)
    if handler is not None:
        TOOL_HANDLERS[name] = handler


def register_tools(tool_defs: list[dict[str, Any]], tool_handlers: dict[str, Any]) -> None:
    """Register a batch of tool definitions and handlers."""
    for tool_def in tool_defs:
        name = str((tool_def.get("function") or {}).get("name") or "").strip()
        register_tool(tool_def, tool_handlers.get(name))


def _load_native_tools() -> None:
    for module_name in _NATIVE_TOOL_MODULES:
        module = importlib.import_module(module_name)
        register_tool(module.TOOL_DEF, module.handler)


def _register_map_tools() -> None:
    from cyrene.map_pin_tool import register_to
    register_to(TOOL_DEFS, TOOL_HANDLERS)


def _register_code_tools() -> None:
    from cyrene.code_tools import register_all
    register_all(TOOL_DEFS, TOOL_HANDLERS)


def _initialize_registry() -> None:
    if TOOL_DEFS or TOOL_HANDLERS:
        return
    _load_native_tools()
    _register_map_tools()
    _register_code_tools()


def get_tool_names() -> list[str]:
    return [td["function"]["name"] for td in TOOL_DEFS]


def get_active_tool_defs() -> list[dict[str, Any]]:
    """Return enabled tool defs for the main agent, plus MCP tools."""
    return get_active_tool_defs_for_actor("main")


def _tool_blocklist_for_actor(actor: str) -> set[str]:
    return set(_MAIN_ONLY_TOOLS) if actor == "subagent" else set()


def is_tool_allowed_for_actor(name: str, actor: str = "main") -> bool:
    return str(name or "") not in _tool_blocklist_for_actor(actor)


def get_active_tool_defs_for_actor(actor: str = "main") -> list[dict[str, Any]]:
    """Return enabled tool defs filtered for the requested actor type."""
    from cyrene.settings_store import is_tool_enabled

    blocked = _tool_blocklist_for_actor(actor)
    defs = [
        td for td in TOOL_DEFS
        if is_tool_enabled(td["function"]["name"]) and td["function"]["name"] not in blocked
    ]

    try:
        from cyrene.mcp_manager import get_manager as _get_mcp_mgr

        manager = _get_mcp_mgr()
        for mcp_td in manager.get_tool_defs():
            name = mcp_td["function"]["name"]
            if is_tool_enabled(name) and name not in blocked:
                defs.append(mcp_td)
    except Exception:
        logger.warning("Failed to fetch MCP tool defs", exc_info=True)

    return defs


_initialize_registry()
