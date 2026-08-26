"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'GenerateImage',
               'description': 'Main agent only. Generate one image through the active OpenAI OAuth '
                              'account and deliver it directly to the current WebUI conversation. '
                              'Use this for requests to draw, render, create, or generate a raster '
                              'image. This tool is unavailable to custom OpenAI-compatible/API Key '
                              'models.',
               'parameters': {'type': 'object',
                              'properties': {'prompt': {'type': 'string',
                                                        'description': 'A complete visual '
                                                                       'description of the image '
                                                                       'to generate.'},
                                             'size': {'type': 'string',
                                                      'enum': ['1024x1024',
                                                               '1536x1024',
                                                               '1024x1536'],
                                                      'description': 'Output dimensions. Defaults '
                                                                     'to 1024x1024.'},
                                             'quality': {'type': 'string',
                                                         'enum': ['low', 'medium', 'high'],
                                                         'description': 'Rendering quality. '
                                                                        'Defaults to medium.'},
                                             'output_format': {'type': 'string',
                                                               'enum': ['png', 'jpeg', 'webp'],
                                                               'description': 'Output file format. '
                                                                              'Defaults to png.'},
                                             'name': {'type': 'string',
                                                      'description': 'Optional filename shown in '
                                                                     'the WebUI.'}},
                              'required': ['prompt']}}},)
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
