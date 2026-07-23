"""Tool-control-plane observability helpers."""

from cyrene.tooling.wire import (
    get_wire_bundle_hash,
    get_wire_bundle_version,
    get_wire_tool_bundle,
)


def wire_bundle_metrics(actor: str = "main") -> dict[str, object]:
    bundle = get_wire_tool_bundle(actor)
    return {
        "actor": bundle.actor,
        "version": bundle.version,
        "sha256": bundle.sha256,
        "tool_count": len(bundle.definitions),
        "estimated_tokens": bundle.estimated_tokens,
    }


__all__ = [
    "get_wire_bundle_hash",
    "get_wire_bundle_version",
    "get_wire_tool_bundle",
    "wire_bundle_metrics",
]
