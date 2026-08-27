"""Model-facing schemas owned by the CLI Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_DEFINITIONS: dict[str, dict[str, Any]] = {
    "ListCliPlugins": {
        "type": "function",
        "function": {
            "name": "ListCliPlugins",
            "description": "List enabled CLI Plugins available in the Agent process environment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional name or description filter."},
                },
            },
        },
    },
    "SearchCliPlugins": {
        "type": "function",
        "function": {
            "name": "SearchCliPlugins",
            "description": "Search installable CLI Plugins and return exact reviewed installation requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "advanced": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        },
    },
    "ManageCliPlugins": {
        "type": "function",
        "function": {
            "name": "ManageCliPlugins",
            "description": "Install, uninstall, enable, disable, bind, or unbind CLI Plugins. Use only an exact install_request returned by SearchCliPlugins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["install", "uninstall", "enable", "disable", "bind", "unbind"]},
                    "plugin_id": {"type": "string"},
                    "version": {"type": "string"},
                    "path": {"type": "string"},
                    "request": {"type": "object"},
                },
                "required": ["action", "plugin_id"],
            },
        },
    },
}


def get_definition(name: str) -> dict[str, Any]:
    return deepcopy(_DEFINITIONS[str(name)])


__all__ = ["get_definition"]
