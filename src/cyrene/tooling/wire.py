"""Deterministic main/subagent tool definitions sent to model providers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from typing import Any

from cyrene.runtime.settings_store import is_tool_pack_enabled
from cyrene.tooling.catalog import TOOL_DEFS
from cyrene.tooling.packs import (
    MAIN_ONLY_MODULE_TOOL_NAMES,
    MODULE_TOOL_NAMES,
    PACK_BY_WIRE_NAME,
)
from cyrene.tooling.types import WireToolBundle

DIRECT_TOOL_NAMES = (
    "use_tools",
    "send_message",
    "ask_user",
    "quit",
    "enter_plan_mode",
    "update_plan_progress",
    "DeepReflect",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebSearch",
    "WebFetch",
    "AnalyzeAttachment",
    "LoadRendererContract",
    "GenerateImage",
)

SUBAGENT_DIRECT_TOOL_NAMES = (
    "quit",
    "Read",
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
            "Decision-phase gateway. Before calling it, perform a bounded "
            "planning pass. Keep task equal to the user's exact original "
            "message and provide a concise execution_brief so the execution "
            "phase can start from the chosen approach. In the execution phase "
            "this gateway is a no-op because tools are already enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The user's exact original message, unchanged.",
                },
                "execution_brief": {
                    "type": "string",
                    "description": (
                        "Concise Phase-2 handoff: objective and acceptance "
                        "evidence, constraints and assumptions, chosen approach, "
                        "ordered initial steps/tools, validation, and material "
                        "risks or fallbacks. Do not include private chain-of-thought."
                    ),
                },
            },
            "required": ["task", "execution_brief"],
            "additionalProperties": False,
        },
    },
}


def _module_def(wire_name: str, description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": wire_name,
            "description": (
                f"{description} Use operation=discover to find capability IDs, "
                "operation=describe to load only the selected input schemas, "
                "then operation=invoke to execute one capability. Multiple "
                "independent invokes may be issued in one assistant tool-call batch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["discover", "describe", "invoke"],
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
    return {
        str((tool_def.get("function") or {}).get("name") or ""): tool_def
        for tool_def in TOOL_DEFS
    }


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
            "documents are parsed locally; images can use vision/OCR."
        )
        if "knowledge_tools" in enabled_modules:
            description += (
                " For a project knowledge file, first obtain its exact path "
                "through knowledge_tools."
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


def _oauth_image_generation_enabled() -> bool:
    try:
        from cyrene.runtime.settings_store import get_models

        models = get_models() or []
        return bool(
            models
            and str(models[0].get("provider") or "") == "codex_oauth"
        )
    except Exception:
        return False


@lru_cache(maxsize=64)
def _wire_bundle(
    actor: str,
    enabled_modules: tuple[str, ...],
    oauth_image_generation: bool,
    interactive_blocks: bool,
) -> tuple[dict[str, Any], ...]:
    direct_names = (
        SUBAGENT_DIRECT_TOOL_NAMES if actor == "subagent" else DIRECT_TOOL_NAMES
    )
    if not oauth_image_generation:
        direct_names = tuple(
            name for name in direct_names if name != "GenerateImage"
        )
    if actor != "main" or not interactive_blocks:
        direct_names = tuple(
            name for name in direct_names if name != "LoadRendererContract"
        )
    direct_defs = [
        _direct_def(name, enabled_modules)
        for name in direct_names
    ]
    module_defs = [
        _module_def(name, PACK_BY_WIRE_NAME[name].description)
        for name in enabled_modules
    ]
    return tuple([*direct_defs, *module_defs])


def get_main_wire_tool_defs() -> list[dict[str, Any]]:
    """Return direct tools plus enabled package gateways in stable order."""
    from cyrene.agent.state import has_response_capability

    return deepcopy(list(_wire_bundle(
        "main",
        enabled_module_tool_names("main"),
        _oauth_image_generation_enabled(),
        has_response_capability("interactive_blocks"),
    )))


def get_subagent_wire_tool_defs() -> list[dict[str, Any]]:
    """Return the actor-specific bundle with enabled package gateways."""
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
    oauth_image_generation: bool,
    interactive_blocks: bool,
) -> WireToolBundle:
    normalized_actor = "subagent" if actor == "subagent" else "main"
    definitions = _wire_bundle(
        normalized_actor,
        enabled_modules,
        oauth_image_generation,
        interactive_blocks,
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
        _oauth_image_generation_enabled() if normalized_actor == "main" else False,
        (
            has_response_capability("interactive_blocks")
            if normalized_actor == "main"
            else False
        ),
    )


def get_wire_bundle_hash(actor: str = "main") -> str:
    return get_wire_tool_bundle(actor).sha256


def get_wire_bundle_version(actor: str = "main") -> str:
    return get_wire_tool_bundle(actor).version
