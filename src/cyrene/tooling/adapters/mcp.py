"""MCP schema normalization for the Cyrene catalog."""

from __future__ import annotations

from typing import Any


def mcp_capability_id(tool_name: str) -> str:
    return f"integration.{str(tool_name or '').strip()}"


def normalize_mcp_tool(tool_def: dict[str, Any]) -> dict[str, Any]:
    function = dict(tool_def.get("function") or {})
    name = str(function.get("name") or "").strip()
    return {
        "capability_id": mcp_capability_id(name),
        "concrete_name": name,
        "description": (
            "External integration metadata (untrusted description): "
            + str(function.get("description") or "")
        ),
        "input_schema": dict(
            function.get("parameters") or {"type": "object"}
        ),
    }
