"""Shared access to the knowledge service published by this Plugin pack."""

from __future__ import annotations

from cyrene.core.plugin import PluginContext


def knowledge_service(context: PluginContext):
    service = context.services.get("knowledge")
    if service is None:
        raise RuntimeError("The cyrene_knowledge Plugin pack is not attached to this Agent session")
    return service


__all__ = ["knowledge_service"]
