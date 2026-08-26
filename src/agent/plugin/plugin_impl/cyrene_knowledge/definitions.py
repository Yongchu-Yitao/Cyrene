"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'ListKnowledgeDocuments',
               'description': "List files in the current Workbench project's knowledge base, "
                              'including size, searchable-chunk status, document ID, and exact '
                              'readable path. Use SearchKnowledge for indexed passages or '
                              'AnalyzeAttachment with the returned path to inspect a specific '
                              'file.',
               'parameters': {'type': 'object',
                              'properties': {'limit': {'type': 'integer',
                                                       'description': 'Maximum number of files to '
                                                                      'return (default: 100, '
                                                                      'maximum: 500).'},
                                             'status': {'type': 'string',
                                                        'description': 'Optional document status '
                                                                       'filter, such as indexed, '
                                                                       'pending, or error.'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'SearchKnowledge',
               'description': "Search the current Workbench project's knowledge base for the most "
                              'relevant passages via hybrid keyword+vector retrieval. Results '
                              'include the raw cosine similarity when vector retrieval '
                              'contributes. Use ListKnowledgeDocuments first when the user asks '
                              'what files are available or requests coverage of all files.',
               'parameters': {'type': 'object',
                              'properties': {'query': {'type': 'string',
                                                       'description': 'Keyword or phrase to search '
                                                                      'for in documents.'},
                                             'k': {'type': 'integer',
                                                   'description': 'Maximum number of matching '
                                                                  'chunks to return (default: '
                                                                  '6).'}},
                              'required': ['query']}}})
_TOOL_DEFS_BY_NAME = {
    str(item["function"]["name"]): item
    for item in _TOOL_DEFS
}


def get_native_tool_def(name: str) -> dict[str, Any]:
    """Return an editable-pack-local copy of one declared schema."""

    target = str(name)
    try:
        definition = _TOOL_DEFS_BY_NAME[target]
    except KeyError as exc:
        raise KeyError(f"unknown local tool definition: {target}") from exc
    return deepcopy(definition)


__all__ = ["get_native_tool_def"]
