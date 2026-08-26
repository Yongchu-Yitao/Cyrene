"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'delete_entity',
               'description': 'Delete or archive an entity by full UUID, unique UUID prefix, or '
                              'exact title. If an exact title matches multiple entities, returns '
                              'their IDs without deleting anything. Default is soft delete '
                              '(archived).',
               'parameters': {'type': 'object',
                              'properties': {'id': {'type': 'string',
                                                    'description': 'Full entity UUID or a unique '
                                                                   'UUID prefix'},
                                             'title': {'type': 'string',
                                                       'description': 'Exact entity title; use '
                                                                      'this when id is '
                                                                      'unavailable'},
                                             'type': {'type': 'string',
                                                      'description': 'Optional entity type to '
                                                                     'disambiguate an exact title'},
                                             'permanent': {'type': 'boolean',
                                                           'description': 'true=permanent delete, '
                                                                          'false=archive'}}}}},
 {'type': 'function',
  'function': {'name': 'list_entities',
               'description': 'List entities with optional filtering by type and status.',
               'parameters': {'type': 'object',
                              'properties': {'type': {'type': 'string',
                                                      'description': 'Filter by type'},
                                             'status': {'type': 'string',
                                                        'enum': ['active',
                                                                 'paused',
                                                                 'done',
                                                                 'archived',
                                                                 'abandoned'],
                                                        'description': 'Filter by status'},
                                             'limit': {'type': 'integer',
                                                       'description': 'Max results, default '
                                                                      '50'}}}}},
 {'type': 'function',
  'function': {'name': 'query_entities',
               'description': 'Search entities by keyword and filter by due date.',
               'parameters': {'type': 'object',
                              'properties': {'q': {'type': 'string',
                                                   'description': 'Search keyword'},
                                             'type': {'type': 'string',
                                                      'description': 'Filter by type'},
                                             'due_before': {'type': 'string',
                                                            'description': 'Due before this date '
                                                                           '(ISO 8601)'}}}}},
 {'type': 'function',
  'function': {'name': 'track_entity',
               'description': 'Track an entity (task, project, decision, knowledge, relationship, '
                              'event, resource, idea, problem, habit). Used for explicit recording '
                              'or implicit extraction.',
               'parameters': {'type': 'object',
                              'properties': {'type': {'type': 'string',
                                                      'enum': ['task',
                                                               'project',
                                                               'decision',
                                                               'knowledge',
                                                               'relationship',
                                                               'event',
                                                               'resource',
                                                               'idea',
                                                               'problem',
                                                               'habit'],
                                                      'description': 'Entity type'},
                                             'title': {'type': 'string',
                                                       'description': 'Brief title'},
                                             'content': {'type': 'string',
                                                         'description': 'Detailed description'},
                                             'priority': {'type': 'string',
                                                          'enum': ['high', 'medium', 'low'],
                                                          'description': 'Priority level'},
                                             'due_date': {'type': 'string',
                                                          'description': 'Due date in ISO 8601 '
                                                                         'format'},
                                             'people': {'type': 'array',
                                                        'items': {'type': 'string'},
                                                        'description': 'Related people'},
                                             'tags': {'type': 'array',
                                                      'items': {'type': 'string'},
                                                      'description': 'Tags'},
                                             'source': {'type': 'string',
                                                        'enum': ['explicit', 'extracted'],
                                                        'description': 'Source type'},
                                             'confidence': {'type': 'number',
                                                            'description': 'Confidence 0-1'},
                                             'source_round_id': {'type': 'string',
                                                                 'description': 'Source round ID'}},
                              'required': ['type', 'title']}}},
 {'type': 'function',
  'function': {'name': 'update_entity',
               'description': 'Update an entity field.',
               'parameters': {'type': 'object',
                              'properties': {'id': {'type': 'string', 'description': 'Entity ID'},
                                             'field': {'type': 'string',
                                                       'enum': ['status',
                                                                'priority',
                                                                'due_date',
                                                                'content',
                                                                'tags',
                                                                'people',
                                                                'title',
                                                                'effort',
                                                                'metadata'],
                                                       'description': 'Field to update'},
                                             'value': {'description': 'New value'}},
                              'required': ['id', 'field', 'value']}}})
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
