"""Input schemas for the editable knowledge Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_SPECS: dict[str, dict[str, Any]] = {
    "ListKnowledgeDocuments": {
        "description": ("List files in the current Workbench project's knowledge base, including indexing state, searchable chunks, document ID, size, and readable path."),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum files to return (default 100, maximum 500).",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter such as indexed, pending, or error.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "SearchKnowledge": {
        "description": ("Search the current Workbench project's knowledge base using hybrid keyword and vector retrieval, returning the most relevant indexed passages."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "k": {
                    "type": "integer",
                    "description": "Maximum passages to return (default 6, maximum 50).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "ListLibraryItems": {
        "description": ("List structured literature items in the current Workbench project, including stable paper IDs, bibliographic metadata, reading state, and citekeys."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional title, author, DOI, venue, or abstract filter.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional reading status filter.",
                },
                "collection_id": {
                    "type": "string",
                    "description": "Optional project-library collection ID.",
                },
                "tag": {"type": "string", "description": "Optional exact tag filter."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 50, maximum 200).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "SearchLibrary": {
        "description": ("Search the current project's literature library by bibliographic metadata and indexed evidence. Returns stable paper IDs and evidence passages."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic, title, author, DOI, or evidence phrase.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum papers to return (default 8, maximum 30).",
                },
                "status": {"type": "string", "description": "Optional reading-status filter."},
                "tag": {"type": "string", "description": "Optional exact tag filter."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "UpdateLibraryMetadata": {
        "description": ("Write verified bibliographic metadata to one project-library item. Existing values are preserved unless overwrite is true. Never write guessed metadata."),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Stable paper ID returned by a library read tool.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Verified metadata fields to write.",
                    "properties": {
                        "item_type": {"type": "string"},
                        "title": {"type": "string"},
                        "abstract": {"type": "string"},
                        "authors": {"type": "array", "items": {"type": "string"}},
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
                    "description": "Replace existing fields with verified values.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Source URLs included in the audit summary.",
                },
            },
            "required": ["paper_id", "metadata"],
            "additionalProperties": False,
        },
    },
}


def get_plugin_spec(name: str) -> dict[str, Any]:
    try:
        return deepcopy(_SPECS[name])
    except KeyError as exc:
        raise KeyError(f"Unknown knowledge Plugin: {name}") from exc


def get_native_tool_def(name: str) -> dict[str, Any]:
    """Compatibility projection for code that still inspects function definitions."""

    spec = get_plugin_spec(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": spec["description"],
            "parameters": spec["input_schema"],
        },
    }


__all__ = ["get_native_tool_def", "get_plugin_spec"]
