"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'broadcast_agent_message',
               'description': 'CAUTION: Broadcast a message to ALL peer sub-agents simultaneously '
                              '— use SPARINGLY. Every broadcast interrupts every peer. Only '
                              'broadcast information that EVERY peer genuinely needs (e.g. a '
                              'shared source URL, a critical deadline). For targeted coordination, '
                              'use send_agent_message instead.',
               'parameters': {'type': 'object',
                              'properties': {'content': {'type': 'string',
                                                         'description': 'Message content to '
                                                                        'broadcast to all peers'}},
                              'required': ['content']}}},
 {'type': 'function',
  'function': {'name': 'query_round',
               'description': 'Inspect currently live rounds and their progress. Use this when the '
                              'user asks how a background round is going or wants the status of a '
                              'still-running discussion.',
               'parameters': {'type': 'object',
                              'properties': {'round_id': {'type': 'string',
                                                          'description': 'Optional specific live '
                                                                         'round id to inspect'}}}}},
 {'type': 'function',
  'function': {'name': 'send_agent_message',
               'description': 'Send a message to another sub-agent via inbox. Use this to '
                              'communicate with other sub-agents.',
               'parameters': {'type': 'object',
                              'properties': {'to': {'type': 'string',
                                                    'description': 'Target agent ID'},
                                             'content': {'type': 'string',
                                                         'description': 'Message content'}},
                              'required': ['to', 'content']}}},
 {'type': 'function',
  'function': {'name': 'spawn_subagent',
               'description': 'Main agent only. Spawn a sub-agent. If the user explicitly asks for '
                              'N subagents, named peer agents, or one subagent per '
                              'item/person/city/option, call this tool once for EACH requested '
                              'agent in the same assistant turn before expecting peer '
                              'coordination. Subagents must not spawn more subagents; they should '
                              'coordinate with peers via send_agent_message and finish via quit.',
               'parameters': {'type': 'object',
                              'properties': {'agent_id': {'type': 'string',
                                                          'description': 'Unique ID for the '
                                                                         'sub-agent'},
                                             'task': {'type': 'string',
                                                      'description': 'The task for the sub-agent '
                                                                     'to complete'},
                                             'mode': {'type': 'string',
                                                      'enum': ['execution', 'discussion'],
                                                      'description': 'Worker mode. Use execution '
                                                                     'for independent '
                                                                     'research/coding/file work; '
                                                                     'use discussion for moderated '
                                                                     'peer conversation. Defaults '
                                                                     'to execution, while '
                                                                     'moderator/participant roles '
                                                                     'always imply discussion.'},
                                             'success_criteria': {'type': 'array',
                                                                  'items': {'type': 'string'},
                                                                  'maxItems': 20,
                                                                  'description': 'Concrete '
                                                                                 'conditions that '
                                                                                 'prove the '
                                                                                 'execution task '
                                                                                 'is complete. '
                                                                                 'Discussion '
                                                                                 'agents may use '
                                                                                 'this for '
                                                                                 'required topics '
                                                                                 'or a synthesis '
                                                                                 'requirement.'},
                                             'max_messages': {'type': 'integer',
                                                              'minimum': 1,
                                                              'maximum': 50,
                                                              'description': 'Optional per-agent '
                                                                             'message cap for a '
                                                                             'discussion worker. '
                                                                             'Ignored by execution '
                                                                             'workers; otherwise '
                                                                             'the configured '
                                                                             'discussion default '
                                                                             'applies.'},
                                             'discussion_id': {'type': 'string',
                                                               'description': 'Optional stable '
                                                                              'discussion '
                                                                              'identifier. '
                                                                              'Discussion workers '
                                                                              'with the same id '
                                                                              'share round, '
                                                                              'message, and '
                                                                              'information-gain '
                                                                              'budgets. Defaults '
                                                                              'to the parent round '
                                                                              'id.'},
                                             'use_secondary': {'type': 'boolean',
                                                               'description': 'Route this '
                                                                              'sub-agent to the '
                                                                              'secondary (local '
                                                                              'small) model for '
                                                                              'simple tasks that '
                                                                              "don't need the main "
                                                                              "model's full "
                                                                              'reasoning.'},
                                             'role': {'type': 'string',
                                                      'enum': ['moderator', 'participant'],
                                                      'description': 'Optional role for '
                                                                     'multi-agent discussions. '
                                                                     "'moderator' speaks first and "
                                                                     'drives the discussion; '
                                                                     "'participant' waits for the "
                                                                     'moderator then contributes '
                                                                     'substantively.'}},
                              'required': ['agent_id', 'task']}}})
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
