"""Agent tool for updating verified project-library metadata."""

from __future__ import annotations

from typing import Any


TOOL_NAME = "UpdateLibraryMetadata"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Write verified bibliographic metadata to one item in the current Workbench "
            "project's literature library. Use WebSearch and WebFetch first to research reliable "
            "sources, and use SearchLibrary or ListLibraryItems to obtain the stable paper_id. "
            "By default this fills only missing fields; set overwrite=true only when correcting "
            "metadata with stronger evidence. Never use this tool with guessed metadata."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Stable paper_id returned by SearchLibrary or ListLibraryItems.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Verified metadata fields to write.",
                    "properties": {
                        "item_type": {"type": "string"},
                        "title": {"type": "string"},
                        "abstract": {"type": "string"},
                        "authors": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Author names in publication order.",
                        },
                        "doi": {"type": "string"},
                        "isbn": {"type": "string"},
                        "url": {"type": "string"},
                        "venue": {"type": "string"},
                        "publisher": {"type": "string"},
                        "volume": {"type": "string"},
                        "issue": {"type": "string"},
                        "pages": {"type": "string"},
                        "language": {"type": "string"},
                        "year": {"type": "integer"},
                        "date_text": {"type": "string"},
                        "citekey": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Allow verified values to replace existing metadata. Defaults to false.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs consulted by the Agent, returned in the audit summary.",
                },
            },
            "required": ["paper_id", "metadata"],
        },
    },
}
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("library:project",),
    "requires_order": True,
}

_EDITABLE_FIELDS = {
    "item_type",
    "title",
    "abstract",
    "doi",
    "isbn",
    "url",
    "venue",
    "publisher",
    "volume",
    "issue",
    "pages",
    "language",
    "year",
    "date_text",
    "citekey",
    "tags",
}


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


async def _tool_update_library_metadata(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    paper_id = str(args.get("paper_id") or "").strip()
    metadata = args.get("metadata")
    if not paper_id:
        return "Error: paper_id is required."
    if not isinstance(metadata, dict) or not metadata:
        return "Error: metadata must contain at least one verified field."

    try:
        from cyrene.agent.state import _current_session_id
        from cyrene.knowledge import library
        from cyrene.workbench_context import ensure_knowledge_db_for_session

        db_path = await ensure_knowledge_db_for_session(_current_session_id.get())
        item = await library.get_item(db_path, paper_id)
        if not item:
            return "Error: the paper_id was not found in the current project library."

        overwrite = bool(args.get("overwrite"))
        patch: dict[str, Any] = {}
        skipped: list[str] = []
        for field in _EDITABLE_FIELDS:
            if field not in metadata or not _has_value(metadata[field]):
                continue
            if overwrite or not _has_value(item.get(field)):
                patch[field] = metadata[field]
            else:
                skipped.append(field)

        authors = metadata.get("authors")
        if isinstance(authors, list) and any(str(name or "").strip() for name in authors):
            if overwrite or not (item.get("creators") or []):
                patch["creators"] = [
                    {"name": str(name).strip(), "creator_type": "author"}
                    for name in authors
                    if str(name or "").strip()
                ]
            else:
                skipped.append("authors")

        if not patch:
            suffix = f" Existing fields skipped: {', '.join(sorted(set(skipped)))}." if skipped else ""
            return "No metadata was changed." + suffix

        updated = await library.update_item(db_path, paper_id, patch)
        sources = [
            str(url).strip()
            for url in (args.get("sources") or [])
            if str(url or "").strip()
        ]
        result = (
            f"Updated project-library paper {paper_id}. "
            f"Written fields: {', '.join(sorted(patch))}."
        )
        if skipped:
            result += f" Preserved existing fields: {', '.join(sorted(set(skipped)))}."
        if sources:
            result += " Sources: " + ", ".join(sources)
        if updated:
            result += f"\nTitle: {updated.get('title') or 'Untitled'}"
        return result
    except Exception as exc:
        return f"Error updating project-library metadata: {exc}"


handler = _tool_update_library_metadata

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "TOOL_METADATA",
    "handler",
    "_tool_update_library_metadata",
]
