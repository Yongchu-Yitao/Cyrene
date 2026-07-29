"""Canonical native/integration capability catalog."""

from __future__ import annotations

import importlib
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.tool_impl import NATIVE_TOOL_MODULES
from cyrene.runtime.settings_store import is_tool_pack_enabled
from cyrene.tooling.packs import (
    CAPABILITY_BINDINGS,
    MODULE_TOOL_NAMES,
    PACK_BY_WIRE_NAME,
    WIRE_NAME_BY_CONCRETE_TOOL,
)

logger = logging.getLogger(__name__)

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
    "app_use",
    "browser_navigate",
    "browser_snapshot",
    "browser_screenshot",
    "browser_click",
    "browser_click_ref",
    "browser_click_text",
    "browser_click_at",
    "browser_type",
    "browser_type_ref",
    "browser_upload_files",
    "browser_wait",
    "browser_network_log",
    "browser_tab_list",
    "browser_tab_new",
    "browser_tab_select",
    "browser_tab_close",
    "browser_scroll",
    "browser_user_events",
    "browser_request_takeover",
    "RemoteCyreneAction",
    "RemoteHarness",
    "RunRemoteCyrene",
}

AGENT_TOOL_GROUPS: dict[str, set[str]] = {
    "main": set(),
    "subagent_blocklist": set(_MAIN_ONLY_TOOLS),
}

TOOL_DEFS: list[dict[str, Any]] = []
TOOL_HANDLERS: dict[str, Any] = {}
TOOL_METADATA: dict[str, dict[str, Any]] = {}

_READ_ONLY_TOOLS = {
    "Read", "AnalyzeAttachment", "Glob", "Grep", "ListMemories", "RecallMemory",
    "RecallConversation", "search_project_memory", "ListKnowledgeDocuments",
    "SearchKnowledge", "ListLibraryItems", "SearchLibrary", "ListShells", "WebFetch", "WebSearch", "query_round",
    "CheckClaudeCode", "ListSkills", "GetLearnedSkill", "list_tasks",
    "list_entities", "query_entities", "browser_user_events", "GitStatus",
    "GitDiff", "GitLog", "SearchSymbol", "FindReferences", "GetFileSymbols",
    "LintCode", "CodeReview", "browser_snapshot", "browser_network_log",
    "browser_tab_list",
    "ListRemoteDevices", "RemoteCyreneStatus",
}

_REQUIRES_ORDER_TOOLS = {
    "browser_navigate", "browser_snapshot", "browser_screenshot", "browser_click",
    "browser_click_ref", "browser_click_text", "browser_click_at", "browser_type",
    "browser_type_ref", "browser_wait", "browser_network_log", "browser_tab_list",
    "browser_upload_files",
    "browser_tab_new", "browser_tab_select", "browser_tab_close", "browser_scroll",
    "browser_user_events", "browser_request_takeover",
}

_RESOURCE_KEY_TEMPLATES: dict[str, tuple[str, ...]] = {
    "Read": ("fs:{path}",),
    "Write": ("fs:{path}",),
    "Edit": ("fs:{path}",),
    "AnalyzeAttachment": ("fs:{path}",),
    "Grep": ("fs:{path}",),
    "GetFileSymbols": ("fs:{path}",),
    "LintCode": ("fs:{path}",),
    "FormatCode": ("fs:{path}",),
    "CodeReview": ("fs:{path}",),
    "Glob": ("fs:workspace",),
    "SearchSymbol": ("code-index:workspace",),
    "FindReferences": ("code-index:workspace",),
    "WebFetch": ("network:web",),
    "WebSearch": ("network:web",),
    "ListMemories": ("memory:short-term", "memory:project"),
    "RecallMemory": ("memory:short-term",),
    "RecallConversation": ("memory:conversations",),
    "search_project_memory": ("memory:project",),
    "ListKnowledgeDocuments": ("knowledge:project",),
    "SearchKnowledge": ("knowledge:project",),
    "ListLibraryItems": ("library:project",),
    "SearchLibrary": ("library:project", "knowledge:project"),
    "UpdateLibraryMetadata": ("library:project",),
    "list_tasks": ("db:scheduled-tasks",),
    "list_entities": ("db:entities",),
    "query_entities": ("db:entities",),
    "ListSkills": ("skills:installed",),
    "GetLearnedSkill": ("skills:learned",),
    "GitStatus": ("git:workspace",),
    "GitDiff": ("git:workspace",),
    "GitLog": ("git:workspace",),
    "ListShells": ("shell:registry",),
    "SendShell": ("shell:{shell_id}",),
    "CloseShell": ("shell:{shell_id}",),
    "ListRemoteDevices": ("remote:chat-context",),
    "RemoteCyreneStatus": ("remote:{device_id}",),
    "RemoteHarness": ("remote:{device_id}",),
    "RemoteCyreneAction": ("remote:{device_id}",),
    "RunRemoteCyrene": ("remote:{device_id}",),
}
for _browser_tool_name in _REQUIRES_ORDER_TOOLS:
    if _browser_tool_name.startswith("browser_"):
        _RESOURCE_KEY_TEMPLATES[_browser_tool_name] = ("browser:active-tab",)
_RESOURCE_KEY_TEMPLATES["app_use"] = ("desktop:app-use",)

_RESOURCE_PARALLEL_WRITES = {"Write", "Edit", "FormatCode", "CloseShell"}
_RESOURCE_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _default_tool_metadata(name: str) -> dict[str, Any]:
    read_only = name in _READ_ONLY_TOOLS
    resource_keys = _RESOURCE_KEY_TEMPLATES.get(name, (f"tool:{name}",))
    return {
        "read_only": read_only,
        "resource_keys": tuple(resource_keys),
        "requires_order": (
            name in _REQUIRES_ORDER_TOOLS
            or (not read_only and name not in _RESOURCE_PARALLEL_WRITES)
        ),
    }


def _normalize_resource_key(key: str) -> str:
    value = str(key or "").strip()
    if not value.startswith("fs:"):
        return value
    raw_path = value[3:].strip()
    if not raw_path or raw_path == "workspace":
        return "fs:workspace"
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            from cyrene.agent.context import active_workspace_dir

            path = active_workspace_dir() / path
        return f"fs:{path.resolve()}"
    except Exception:
        return f"fs:{raw_path}"


def get_tool_execution_metadata(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve internal scheduling metadata without exposing it to the model API."""
    tool_name = str(name or "")
    metadata = dict(TOOL_METADATA.get(tool_name) or _default_tool_metadata(tool_name))
    args = dict(arguments or {})
    resolved_keys: list[str] = []
    for template in metadata.get("resource_keys") or ():
        rendered = str(template)
        missing = False
        for field in _RESOURCE_FIELD_RE.findall(rendered):
            raw = args.get(field)
            if raw in (None, ""):
                missing = True
                break
            rendered = rendered.replace("{" + field + "}", str(raw))
        resolved_keys.append(
            _normalize_resource_key(rendered if not missing else f"tool:{tool_name}")
        )
    return {
        "read_only": bool(metadata.get("read_only")),
        "resource_keys": tuple(dict.fromkeys(key for key in resolved_keys if key)),
        "requires_order": bool(metadata.get("requires_order")),
    }


def register_tool(
    tool_def: dict[str, Any],
    handler: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
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
    TOOL_METADATA[name] = {
        **_default_tool_metadata(name),
        **dict(metadata or {}),
    }


def register_tools(
    tool_defs: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    tool_metadata: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Register a batch of tool definitions and handlers."""
    for tool_def in tool_defs:
        name = str((tool_def.get("function") or {}).get("name") or "").strip()
        register_tool(
            tool_def,
            tool_handlers.get(name),
            (tool_metadata or {}).get(name),
        )


def _load_native_tools() -> None:
    for module_name in NATIVE_TOOL_MODULES:
        module = importlib.import_module(module_name)
        register_tool(
            module.TOOL_DEF,
            module.handler,
            getattr(module, "TOOL_METADATA", None),
        )


def _register_map_tools() -> None:
    importlib.import_module("cyrene.tool_impl.map.tools").register_to(
        TOOL_DEFS,
        TOOL_HANDLERS,
    )


def _register_code_tools() -> None:
    importlib.import_module("cyrene.tool_impl.code").register_all(
        TOOL_DEFS,
        TOOL_HANDLERS,
    )


def _initialize_registry() -> None:
    if TOOL_DEFS or TOOL_HANDLERS:
        return
    _load_native_tools()
    _register_map_tools()
    _register_code_tools()
    for tool_def in TOOL_DEFS:
        name = str((tool_def.get("function") or {}).get("name") or "")
        TOOL_METADATA.setdefault(name, _default_tool_metadata(name))


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
    """Return tool defs filtered by actor and whole-package settings."""

    blocked = _tool_blocklist_for_actor(actor)
    defs = [
        td for td in TOOL_DEFS
        if td["function"]["name"] not in blocked
        and (
            td["function"]["name"] not in WIRE_NAME_BY_CONCRETE_TOOL
            or is_tool_pack_enabled(
                WIRE_NAME_BY_CONCRETE_TOOL[td["function"]["name"]]
            )
        )
    ]

    try:
        from cyrene.tooling.backends.mcp_manager import get_manager as _get_mcp_mgr

        manager = _get_mcp_mgr()
        if is_tool_pack_enabled("integration_tools"):
            for mcp_td in manager.get_tool_defs():
                name = mcp_td["function"]["name"]
                if name not in blocked:
                    defs.append(mcp_td)
    except Exception:
        logger.warning("Failed to fetch MCP tool defs", exc_info=True)

    return defs


_initialize_registry()


@dataclass(frozen=True)
class Capability:
    """One deferred capability and its concrete execution target."""

    capability_id: str
    pack_id: str
    wire_name: str
    concrete_name: str
    description: str
    input_schema: dict[str, Any]
    external: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "description": self.description,
        }

    def detail(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
            "source": "integration" if self.external else "native",
        }


def _function_definitions() -> dict[str, dict[str, Any]]:
    return {
        str((tool_def.get("function") or {}).get("name") or ""): tool_def
        for tool_def in TOOL_DEFS
    }


def _mcp_definitions() -> dict[str, dict[str, Any]]:
    try:
        from cyrene.tooling.backends.mcp_manager import get_manager

        return {
            str((tool_def.get("function") or {}).get("name") or ""): tool_def
            for tool_def in get_manager().get_tool_defs()
        }
    except Exception:
        return {}


def module_wire_names() -> tuple[str, ...]:
    return MODULE_TOOL_NAMES


def _model_reference_map() -> dict[str, str]:
    return {
        concrete_name: capability_id
        for bindings in CAPABILITY_BINDINGS.values()
        for capability_id, concrete_name in bindings
    }


def _model_facing_value(value: Any) -> Any:
    """Replace hidden implementation names in deferred schemas/descriptions."""
    if isinstance(value, str):
        rendered = value
        for concrete_name, capability_id in sorted(
            _model_reference_map().items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            rendered = rendered.replace(concrete_name, capability_id)
        return rendered
    if isinstance(value, dict):
        return {key: _model_facing_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_model_facing_value(item) for item in value]
    return value


def _native_capabilities(wire_name: str) -> list[Capability]:
    pack = PACK_BY_WIRE_NAME.get(wire_name)
    if pack is None:
        return []
    definitions = _function_definitions()
    result: list[Capability] = []
    for capability_id, concrete_name in CAPABILITY_BINDINGS.get(wire_name, ()):
        tool_def = definitions.get(concrete_name)
        if not tool_def:
            continue
        function = tool_def.get("function") or {}
        result.append(Capability(
            capability_id=str(capability_id),
            pack_id=pack.pack_id,
            wire_name=wire_name,
            concrete_name=str(concrete_name),
            description=str(_model_facing_value(function.get("description") or "")),
            input_schema=dict(_model_facing_value(
                function.get("parameters") or {"type": "object"}
            )),
        ))
    return result


def _integration_capabilities() -> list[Capability]:
    from cyrene.tooling.adapters.mcp import normalize_mcp_tool

    result: list[Capability] = []
    native_names = set(_function_definitions())
    for concrete_name, tool_def in sorted(_mcp_definitions().items()):
        if concrete_name in native_names:
            continue
        normalized = normalize_mcp_tool(tool_def)
        result.append(Capability(
            capability_id=str(normalized["capability_id"]),
            pack_id="integration",
            wire_name="integration_tools",
            concrete_name=str(normalized["concrete_name"]),
            description=str(_model_facing_value(normalized["description"])),
            input_schema=dict(_model_facing_value(normalized["input_schema"])),
            external=True,
        ))
    return result


def capabilities_for_pack(
    wire_name: str,
    *,
    actor: str = "main",
    include_disabled: bool = False,
) -> list[Capability]:
    if not include_disabled and not is_tool_pack_enabled(wire_name):
        return []
    capabilities = (
        _integration_capabilities()
        if wire_name == "integration_tools"
        else _native_capabilities(wire_name)
    )
    return [
        capability
        for capability in capabilities
        if is_tool_allowed_for_actor(capability.concrete_name, actor)
    ]


def all_capabilities(
    *,
    actor: str = "main",
    include_disabled: bool = False,
) -> list[Capability]:
    result: list[Capability] = []
    for wire_name in module_wire_names():
        result.extend(capabilities_for_pack(
            wire_name,
            actor=actor,
            include_disabled=include_disabled,
        ))
    return result


def get_capability(
    capability_id: str,
    *,
    actor: str = "main",
    include_disabled: bool = False,
) -> Capability | None:
    target = str(capability_id or "").strip()
    return next(
        (
            capability
            for capability in all_capabilities(
                actor=actor,
                include_disabled=include_disabled,
            )
            if capability.capability_id == target
        ),
        None,
    )


def get_capability_by_concrete_name(
    concrete_name: str,
    *,
    actor: str = "main",
    include_disabled: bool = False,
) -> Capability | None:
    target = str(concrete_name or "").strip()
    return next(
        (
            capability
            for capability in all_capabilities(
                actor=actor,
                include_disabled=include_disabled,
            )
            if capability.concrete_name == target
        ),
        None,
    )


def search_capability_items(
    items: list[Any],
    *,
    query: str = "",
    limit: int = 20,
) -> list[Any]:
    """Rank a pack-scoped capability list without making verbose queries empty."""
    bounded_limit = max(1, min(int(limit or 20), 50))
    terms = list(dict.fromkeys(
        term.casefold()
        for term in re.findall(r"[\w.-]+", str(query or ""), flags=re.UNICODE)
        if term
    ))
    if not terms:
        return list(items[:bounded_limit])

    ranked: list[tuple[int, int, Any]] = []
    for index, item in enumerate(items):
        capability_id = str(getattr(item, "capability_id", "") or "")
        concrete_name = str(getattr(item, "concrete_name", "") or "")
        description = str(getattr(item, "description", "") or "")
        identity = f"{capability_id} {concrete_name}".casefold()
        haystack = f"{identity} {description}".casefold()
        matched = sum(1 for term in terms if term in haystack)
        identity_matches = sum(1 for term in terms if term in identity)
        # Identity hits are more discriminating than prose hits. Keep the
        # declaration order as the final tie-breaker so results stay stable.
        ranked.append((matched + identity_matches * 2, index, item))

    if any(score > 0 for score, _index, _item in ranked):
        ranked.sort(key=lambda row: (-row[0], row[1]))
    return [item for _score, _index, item in ranked[:bounded_limit]]


def discover_capabilities(
    wire_name: str,
    *,
    actor: str = "main",
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    items = capabilities_for_pack(wire_name, actor=actor)
    matches = search_capability_items(items, query=query, limit=limit)
    return [capability.summary() for capability in matches]


def describe_capabilities(
    wire_name: str,
    capability_ids: list[str],
    *,
    actor: str = "main",
) -> list[dict[str, Any]]:
    available = {
        capability.capability_id: capability
        for capability in capabilities_for_pack(wire_name, actor=actor)
    }
    return [
        available[capability_id].detail()
        for capability_id in capability_ids[:20]
        if capability_id in available
    ]
