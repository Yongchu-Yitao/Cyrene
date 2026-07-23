"""Public facade for Cyrene's tool control plane.

Application code should depend on this stable API or on a focused
``cyrene.tooling`` module. Concrete handlers live under ``cyrene.tool_impl``.
"""

from cyrene.tooling.catalog import (
    all_capabilities,
    describe_capabilities,
    discover_capabilities,
    get_capability,
)
from cyrene.tooling.gateway import (
    execute_capability,
    execute_wire_tool,
    execute_wire_tool_in_context,
)
from cyrene.tooling.snapshot import build_catalog_snapshot
from cyrene.tooling.wire import (
    get_main_wire_tool_defs,
    get_subagent_wire_tool_defs,
    get_wire_bundle_hash,
    get_wire_bundle_version,
    get_wire_tool_bundle,
)

__all__ = [
    "all_capabilities",
    "build_catalog_snapshot",
    "describe_capabilities",
    "discover_capabilities",
    "execute_capability",
    "execute_wire_tool",
    "execute_wire_tool_in_context",
    "get_capability",
    "get_main_wire_tool_defs",
    "get_subagent_wire_tool_defs",
    "get_wire_bundle_hash",
    "get_wire_bundle_version",
    "get_wire_tool_bundle",
]
