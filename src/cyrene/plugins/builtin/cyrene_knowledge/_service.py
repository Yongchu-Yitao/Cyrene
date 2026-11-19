"""Shared access to the knowledge service published by this Plugin pack."""

from __future__ import annotations

import json

from cyrene.core.plugin import PluginContext
from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID


def knowledge_service(context: PluginContext):
    service = context.services.get("knowledge")
    if service is None:
        raise RuntimeError("The cyrene_knowledge Plugin pack is not attached to this Agent session")
    return service


__all__ = ["knowledge_service"]

def search_response(context, message, hits):
    data = context.data
    if not (data.get("personal_assistant") or data.get("project_id") == ASSISTANT_SCOPE_ID
            or data.get("assistant_owner_session_id") == ASSISTANT_ID
            or (data.get("session_id") or context.tree_id) == ASSISTANT_ID):
        return message
    sources = {}
    for hit in hits:
        key = (hit.get("workspace"), hit.get("item_id"), hit.get("attachment_id"))
        if all(key) and key not in sources and hit.get("content") and hit.get("document_name"):
            sources[key] = {field: hit.get(field) for field in
                            ("workspace", "item_id", "attachment_id", "document_name", "content_type")}
            sources[key]["excerpt"] = str(hit["content"]).strip()[:500]
    if not sources:
        return message
    return json.dumps({"ok": True, "message": message, "presentation": {
        "kind": "knowledge_sources", "sources": list(sources.values()),
    }}, ensure_ascii=False)
