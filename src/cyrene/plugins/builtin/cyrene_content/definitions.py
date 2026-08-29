"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'AnalyzeAttachment',
               'description': 'Analyze an uploaded attachment or workspace file. PDFs and Office '
                              'documents (DOCX/PPTX/XLSX, including extensionless uploads) are '
                              'parsed to text locally. Images run downloaded local PP-OCRv6 first, '
                              'then fall back to vision when text is insufficient or the prompt '
                              'needs visual understanding. Use the exact path returned by '
                              'ListKnowledgeDocuments for knowledge-base files.',
               'parameters': {'type': 'object',
                              'properties': {'path': {'type': 'string',
                                                      'description': 'Absolute path to the '
                                                                     'uploaded file or a '
                                                                     'workspace-relative path.'},
                                             'prompt': {'type': 'string',
                                                        'description': 'Optional custom '
                                                                       'instruction for image '
                                                                       'analysis.'},
                                             'force_refresh': {'type': 'boolean',
                                                               'description': 'Recompute analysis '
                                                                              'instead of using '
                                                                              'cached sidecar '
                                                                              'output.'}},
                              'required': ['path']}}},
 {'type': 'function',
  'function': {'name': 'WebFetch',
               'description': 'Fetch a URL. HTML responses are automatically converted to readable '
                              'text with a limited number of HTTP(S) links preserved; other text '
                              'responses are returned unchanged.',
               'parameters': {'type': 'object',
                              'properties': {'url': {'type': 'string'}},
                              'required': ['url']}}},
 {'type': 'function',
  'function': {'name': 'WebSearch',
               'description': 'Search the web and return source evidence. Use detail="preview" for '
                              'ordinary queries; it fetches only the first three result pages, '
                              'waits without a deadline for the first successful page, then waits '
                              'at most 5 seconds for the other pages. Use detail="content" when '
                              'broader page-level evidence is needed. Synthesize the answer from '
                              'the returned evidence.',
               'parameters': {'type': 'object',
                              'properties': {'query': {'type': 'string'},
                                             'detail': {'type': 'string',
                                                        'enum': ['preview', 'content'],
                                                        'default': 'preview'},
                                             'max_results': {'type': 'integer',
                                                             'minimum': 1,
                                                             'maximum': 8,
                                                             'default': 5}},
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
