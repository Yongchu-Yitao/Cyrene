"""Deterministic main/subagent tool definitions sent to model providers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from typing import Any

from cyrene.runtime.settings_store import is_tool_pack_enabled
from cyrene.tooling.cache_invalidation import register_tool_cache_invalidator
from cyrene.tooling.packs import (
    MAIN_ONLY_MODULE_TOOL_NAMES,
    MODULE_TOOL_NAMES,
)
from cyrene.tooling.types import WireToolBundle


TOOLBOX_TOOL_NAME = "toolbox"

DIRECT_TOOL_NAMES = (
    "use_tools",
    "send_message",
    "ask_user",
    "quit",
    "enter_plan_mode",
    "update_plan_progress",
    "DeepReflect",
    "Read",
    "read_tool_result",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebSearch",
    "WebFetch",
    "AnalyzeAttachment",
    "LoadRendererContract",
    "PowerPointGetContext",
    "PowerPointInspect",
    "PowerPointApplyBatch",
    "PowerPointRenderSlide",
    "PowerPointToolSearch",
)

POWERPOINT_CORE_TOOL_NAMES = (
    "PowerPointGetContext",
    "PowerPointInspect",
    "PowerPointApplyBatch",
    "PowerPointRenderSlide",
    "PowerPointToolSearch",
)

SUBAGENT_DIRECT_TOOL_NAMES = (
    "quit",
    "Read",
    "read_tool_result",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebSearch",
    "WebFetch",
    "AnalyzeAttachment",
)

_USE_TOOLS_DEF = {
    "type": "function",
    "function": {
        "name": "use_tools",
        "description": (
            "Decision-phase gateway to execution. Use it when actions, inspection, "
            "retrieval, or verification are needed. Do not make a full plan first. "
            "Provide only a short execution_brief; the original user message is "
            "already present in the conversation. It is a no-op in execution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "execution_brief": {
                    "type": "string",
                    "maxLength": 300,
                    "description": (
                        "Phase-2 handoff under 300 characters containing only the "
                        "intent, first useful action, and hard user constraints."
                    ),
                },
            },
            "required": ["execution_brief"],
            "additionalProperties": False,
        },
    },
}


def _toolbox_def() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOLBOX_TOOL_NAME,
            "description": (
                "Stable gateway for all deferred capabilities allowed in this run. "
                "Use operation=search to find capability IDs across every enabled "
                "package, operation=describe to load only selected schemas and "
                "usage guidance, then operation=invoke to execute one capability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["search", "describe", "invoke"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional discover search terms.",
                    },
                    "capability_id": {
                        "type": "string",
                        "description": "Capability to invoke or describe.",
                    },
                    "capability_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                        "description": "Capabilities to describe (maximum 20).",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments validated against the described capability schema.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        },
    }


def _concrete_defs() -> dict[str, dict[str, Any]]:
    from cyrene.tooling.catalog import get_effective_function_definitions

    return get_effective_function_definitions()


def _direct_def(
    name: str,
    enabled_modules: tuple[str, ...],
) -> dict[str, Any]:
    if name == "use_tools":
        return _USE_TOOLS_DEF
    tool_def = deepcopy(_concrete_defs()[name])
    if name == "AnalyzeAttachment":
        description = (
            "Analyze an uploaded attachment or workspace file. PDFs and Office "
            "documents are parsed locally; images can use vision/OCR. Project "
            "knowledge paths, when available, can be found through toolbox."
        )
        tool_def["function"]["description"] = description
    return tool_def


def enabled_module_tool_names(actor: str = "main") -> tuple[str, ...]:
    """Return enabled package gateways in their stable declaration order."""
    return tuple(
        name
        for name in MODULE_TOOL_NAMES
        if is_tool_pack_enabled(name)
        and not (actor == "subagent" and name in MAIN_ONLY_MODULE_TOOL_NAMES)
    )


@lru_cache(maxsize=64)
def _wire_bundle(
    actor: str,
    enabled_modules: tuple[str, ...],
    interactive_blocks: bool,
    powerpoint_installed: bool,
) -> tuple[dict[str, Any], ...]:
    direct_names = (
        SUBAGENT_DIRECT_TOOL_NAMES if actor == "subagent" else DIRECT_TOOL_NAMES
    )
    if actor != "main" or "office_tools" not in enabled_modules or not powerpoint_installed:
        direct_names = tuple(name for name in direct_names if name not in POWERPOINT_CORE_TOOL_NAMES)
    if actor != "main" or not interactive_blocks:
        direct_names = tuple(
            name for name in direct_names if name != "LoadRendererContract"
        )
    direct_defs = [
        _direct_def(name, enabled_modules)
        for name in direct_names
    ]
    # One universal gateway keeps the provider-visible tool schema byte-stable
    # as packages and dynamic integrations change. Package switches are enforced
    # by the catalog snapshot and search/describe/invoke execution path.
    module_defs = [_toolbox_def()]
    return tuple([*direct_defs, *module_defs])


def get_main_wire_tool_defs() -> list[dict[str, Any]]:
    """Return direct tools plus the universal deferred-capability gateway."""
    from cyrene.agent.state import has_response_capability

    return deepcopy(list(_wire_bundle(
        "main",
        enabled_module_tool_names("main"),
        has_response_capability("interactive_blocks"),
        _powerpoint_addin_installed(),
    )))


def get_subagent_wire_tool_defs() -> list[dict[str, Any]]:
    """Return the subagent direct tools plus the universal gateway."""
    return deepcopy(list(_wire_bundle(
        "subagent",
        enabled_module_tool_names("subagent"),
        False,
        False,
    )))


@lru_cache(maxsize=64)
def _get_wire_tool_bundle(
    actor: str,
    enabled_modules: tuple[str, ...],
    interactive_blocks: bool,
    powerpoint_installed: bool,
) -> WireToolBundle:
    normalized_actor = "subagent" if actor == "subagent" else "main"
    definitions = _wire_bundle(
        normalized_actor,
        enabled_modules,
        interactive_blocks,
        powerpoint_installed,
    )
    encoded = json.dumps(
        definitions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return WireToolBundle(
        version="cyrene-wire-v1",
        actor=normalized_actor,
        definitions=deepcopy(definitions),
        sha256=hashlib.sha256(encoded).hexdigest(),
        estimated_tokens=max(1, len(encoded) // 4),
    )


def get_wire_tool_bundle(actor: str = "main") -> WireToolBundle:
    normalized_actor = "subagent" if actor == "subagent" else "main"
    from cyrene.agent.state import has_response_capability

    return _get_wire_tool_bundle(
        normalized_actor,
        enabled_module_tool_names(normalized_actor),
        (
            has_response_capability("interactive_blocks")
            if normalized_actor == "main"
            else False
        ),
        _powerpoint_addin_installed() if normalized_actor == "main" else False,
    )


def get_wire_bundle_hash(actor: str = "main") -> str:
    return get_wire_tool_bundle(actor).sha256


def get_wire_bundle_version(actor: str = "main") -> str:
    return get_wire_tool_bundle(actor).version


def invalidate_wire_tool_cache() -> None:
    """Invalidate both definition and hashed-bundle caches after file changes."""
    _wire_bundle.cache_clear()
    _get_wire_tool_bundle.cache_clear()


register_tool_cache_invalidator(invalidate_wire_tool_cache)


def _powerpoint_addin_installed() -> bool:
    try:
        from cyrene.office.installation import powerpoint_addin_installed

        return powerpoint_addin_installed()
    except Exception:
        return False
