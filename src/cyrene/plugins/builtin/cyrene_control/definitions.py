"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'ask_user',
               'description': 'Ask the user a clarification question and pause until they answer. '
                              'Use this liberally — asking is better than assuming. Trigger when: '
                              'the request is ambiguous, details are missing, multiple reasonable '
                              'approaches exist, or you need sign-off before a risky action. If '
                              'you need to ask the user anything, use this tool instead of putting '
                              'a question in assistant text. Use freeform text for open questions, '
                              'or add a short options array for structured choices. The UI always '
                              'allows custom answers even with options.',
               'parameters': {'type': 'object',
                              'properties': {'text': {'type': 'string',
                                                      'description': 'The clarification question '
                                                                     'to show the user.'},
                                             'options': {'type': 'array',
                                                         'description': 'Optional short option '
                                                                        'labels when structured '
                                                                        'choices would help.',
                                                         'items': {'type': 'string'}}},
                              'required': ['text']}}},
 {'type': 'function',
  'function': {'name': 'DeepReflect',
               'description': 'Main agent only. Reframe the next working context when the current '
                              "approach is not satisfying the user's goal, repeated work is not "
                              'converging, or user guidance shows the direction is wrong. Do not '
                              'use this merely because one tool failed. The visible transcript is '
                              'preserved; future LLM context uses a compressed reflection packet. '
                              'Call this as the only tool in its tool-call turn; after it completes, '
                              'continue automatically from the rewritten context.',
               'parameters': {'type': 'object',
                              'properties': {'goal_gap': {'type': 'string',
                                                          'description': 'What user goal or '
                                                                         'requirement is not being '
                                                                         'satisfied by the current '
                                                                         'approach.'},
                                             'user_requirement': {'type': 'string',
                                                                  'description': 'Optional exact '
                                                                                 'user requirement '
                                                                                 'or correction '
                                                                                 'that should '
                                                                                 'guide the '
                                                                                 'reframing.'},
                                             'focus': {'type': 'string',
                                                       'description': 'Optional next-direction '
                                                                      'focus for the reflection '
                                                                      'worker.'}},
                              'required': ['goal_gap']}}},
 {'type': 'function',
  'function': {'name': 'enter_plan_mode',
               'description': "Main agent only. Enter PLAN MODE: decompose the user's request into "
                              'ordered steps, each broken into concrete tasks, show the plan in '
                              "the right sidebar's 计划 tab, and ask the user to approve / reject / "
                              'revise before doing any real work. Use this proactively for '
                              'complex, multi-step, or risky tasks where the user would benefit '
                              'from reviewing the approach first. Do NOT combine with other tools '
                              "in the same turn; calling this pauses the round for the user's "
                              'decision.',
               'parameters': {'type': 'object',
                              'properties': {'title': {'type': 'string',
                                                       'description': 'Short title for the proposed plan.'},
                                             'summary': {'type': 'string',
                                                         'description': 'Concise explanation of the approach and its constraints.'},
                                             'steps': {'type': 'array',
                                                       'minItems': 1,
                                                       'maxItems': 20,
                                                       'items': {'type': 'object',
                                                                 'properties': {'title': {'type': 'string'},
                                                                                'tasks': {'type': 'array',
                                                                                          'items': {'type': 'string'},
                                                                                          'maxItems': 20}},
                                                                 'required': ['title'],
                                                                 'additionalProperties': False}}},
                              'required': ['title', 'steps'],
                              'additionalProperties': False}}},
 {'type': 'function',
  'function': {'name': 'update_plan_progress',
               'description': 'Main agent only. Update the durable Workbench plan before and after '
                              'executing a plan step so the user can see exactly which step is '
                              'active. Use only when an approved plan is being executed.',
               'parameters': {'type': 'object',
                              'properties': {'step': {'type': 'integer',
                                                      'minimum': 1,
                                                      'description': '1-based plan step number.'},
                                             'status': {'type': 'string',
                                                        'enum': ['in_progress',
                                                                 'completed',
                                                                 'failed',
                                                                 'skipped'],
                                                        'description': 'New status for this step.'},
                                             'note': {'type': 'string',
                                                      'description': 'Optional short progress or '
                                                                     'result note shown to the '
                                                                     'user.'}},
                              'required': ['step', 'status']}}})
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
