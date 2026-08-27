"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'ListEnvironment',
               'description': 'List enabled, installed or system-detected MCP servers, CLI tools, '
                              'and runtimes available to Cyrene. Disabled extensions are hidden. '
                              'Returns compact metadata only and does not change the system. Use '
                              'the cyrene_skills Plugin pack for Skills.',
               'parameters': {'type': 'object',
                              'properties': {'kind': {'type': 'string',
                                                      'enum': ['all', 'mcp', 'cli', 'toolchain'],
                                                      'description': 'Optional environment '
                                                                     'category; defaults to all.'},
                                             'query': {'type': 'string',
                                                       'description': 'Optional text filter over '
                                                                      'installed IDs, names, '
                                                                      'descriptions, and '
                                                                      'versions.'}}}}},
 {'type': 'function',
  'function': {'name': 'ManageExtensions',
               'description': 'List, search, install, install a fixed local MCP configuration, '
                              'uninstall, enable, disable, or select a default version for Cyrene '
                              'extensions. Use only exact requests returned by extension search; '
                              'never guess fields. Persistent mutations always pass through the '
                              'reviewer even in full_access mode.',
               'parameters': {'type': 'object',
                              'properties': {'action': {'type': 'string',
                                                        'enum': ['list',
                                                                 'search',
                                                                 'install',
                                                                 'install_local_mcp',
                                                                 'uninstall',
                                                                 'enable',
                                                                 'disable',
                                                                 'set_default']},
                                             'kind': {'type': 'string',
                                                      'enum': ['skill', 'mcp', 'cli', 'toolchain']},
                                             'extension_id': {'type': 'string'},
                                             'query': {'type': 'string'},
                                             'version': {'type': 'string'},
                                             'advanced': {'type': 'boolean'},
                                             'request': {'type': 'object',
                                                         'description': 'Exact request returned by '
                                                                        'extension search. For '
                                                                        'install_local_mcp, config '
                                                                        'is required and must be a '
                                                                        'deterministic MCP '
                                                                        'declaration.',
                                                         'properties': {'version': {'type': 'string'},
                                                                        'remote': {'type': 'object'},
                                                                        'package': {'type': 'object'},
                                                                        'source': {'type': 'object'},
                                                                        'ref': {'type': 'string'},
                                                                        'spec': {'type': 'object'},
                                                                        'url': {'type': 'string'},
                                                                        'subdirs': {'type': 'array',
                                                                                    'items': {'type': 'string'}},
                                                                        'distribution': {'type': 'string'},
                                                                        'config': {'type': 'object',
                                                                                   'properties': {'name': {'type': 'string'},
                                                                                                  'transport': {'type': 'string',
                                                                                                                'enum': ['stdio',
                                                                                                                         'sse',
                                                                                                                         'streamable_http']},
                                                                                                  'command': {'type': 'string',
                                                                                                              'description': 'Existing '
                                                                                                                             'deterministic '
                                                                                                                             'executable; '
                                                                                                                             'stdio '
                                                                                                                             'only.'},
                                                                                                  'args': {'type': 'array',
                                                                                                           'items': {'type': 'string'}},
                                                                                                  'env': {'type': 'object',
                                                                                                          'additionalProperties': {'type': 'string'}},
                                                                                                  'url': {'type': 'string'},
                                                                                                  'headers': {'type': 'object',
                                                                                                              'additionalProperties': {'type': 'string'}},
                                                                                                  'version': {'type': 'string'},
                                                                                                  'enabled': {'type': 'boolean'}},
                                                                                   'required': ['name',
                                                                                                'transport',
                                                                                                'version'],
                                                                                   'additionalProperties': False}},
                                                         'additionalProperties': False}},
                              'required': ['action']}}},
 {'type': 'function',
  'function': {'name': 'SearchEnvironment',
               'description': 'Search available MCP servers, CLI tools, and runtimes without '
                              'installing them. Disabled extensions are hidden. Results include '
                              'installed state and a deterministic install_request that can be '
                              'passed to the reviewed environment manager. Use the cyrene_skills Plugin pack for '
                              'Skills.',
               'parameters': {'type': 'object',
                              'properties': {'query': {'type': 'string',
                                                       'description': 'Capability, package, '
                                                                      'command, runtime, or plugin '
                                                                      'to find.'},
                                             'kind': {'type': 'string',
                                                      'enum': ['all', 'mcp', 'cli', 'toolchain'],
                                                      'description': 'Optional category; defaults '
                                                                     'to all.'},
                                             'advanced': {'type': 'boolean',
                                                          'description': 'Include higher-risk mise '
                                                                         'backends such as npm, '
                                                                         'pipx, cargo, and go.'},
                                             'limit': {'type': 'integer',
                                                       'minimum': 1,
                                                       'maximum': 50},
                                             'cursor': {'type': 'string',
                                                        'description': 'Optional MCP Registry '
                                                                       'continuation cursor.'}},
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
