"""Editable Cyrene browser Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from .lifecycle import setup_browser_lifecycle

from . import (
    browser_click,
    browser_click_at,
    browser_click_ref,
    browser_navigate,
    browser_network_log,
    browser_request_takeover,
    browser_screenshot,
    browser_scroll,
    browser_snapshot,
    browser_tab_close,
    browser_tab_list,
    browser_tab_new,
    browser_tab_select,
    browser_type,
    browser_type_ref,
    browser_upload_files,
    browser_user_events,
    browser_wait,
)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = {
        **dict(getattr(module, "TOOL_METADATA", {})),
        "main_only": True,
    }
    timeout = 900.0 if name == "browser_request_takeover" else 180.0
    return Plugin(
        name=name,
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
        handler=module.handler,
        allow_parallel=bool(metadata.get("allow_parallel", not metadata.get("requires_order", True))),
        timeout_seconds=float(metadata.get("timeout_seconds", timeout)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_browser",
    description="Navigate and interact with browser sessions.",
    plugins=tuple(_plugin(module) for module in (
        browser_navigate,
        browser_snapshot,
        browser_screenshot,
        browser_click,
        browser_click_ref,
        browser_click_at,
        browser_type,
        browser_type_ref,
        browser_upload_files,
        browser_wait,
        browser_network_log,
        browser_tab_list,
        browser_tab_new,
        browser_tab_select,
        browser_tab_close,
        browser_scroll,
        browser_user_events,
        browser_request_takeover,
    )),
    setup=setup_browser_lifecycle,
)

__all__ = ["plugin_pack"]
