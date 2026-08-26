"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'cancel_task',
               'description': 'Cancel and delete a scheduled task.',
               'parameters': {'type': 'object',
                              'properties': {'task_id': {'type': 'string'}},
                              'required': ['task_id']}}},
 {'type': 'function',
  'function': {'name': 'edit_task',
               'description': 'Partially update exactly one existing scheduled task by task_id. '
                              'Only provided fields are changed. When schedule_type, '
                              'schedule_value, or schedule_timezone changes, next_run is '
                              'recomputed. Paused tasks stay paused. Changing permission_mode to '
                              'full_access requires user confirmation.',
               'parameters': {'type': 'object',
                              'properties': {'task_id': {'type': 'string',
                                                         'description': 'Stable task id returned '
                                                                        'by schedule_task or '
                                                                        'list_tasks.'},
                                             'prompt': {'type': 'string'},
                                             'action_type': {'type': 'string',
                                                             'enum': ['message', 'agent_task']},
                                             'schedule_type': {'type': 'string',
                                                               'enum': ['cron',
                                                                        'interval',
                                                                        'once']},
                                             'schedule_value': {'type': 'string',
                                                                'description': 'Cron expression, '
                                                                               'interval seconds, '
                                                                               'or ISO-8601 '
                                                                               'datetime matching '
                                                                               'schedule_type.'},
                                             'schedule_timezone': {'type': 'string',
                                                                   'description': 'IANA timezone '
                                                                                  'used for cron '
                                                                                  'wall-clock '
                                                                                  'fields.'},
                                             'permission_mode': {'type': 'string',
                                                                 'enum': ['workspace_only',
                                                                          'full_access']}},
                              'required': ['task_id']}}},
 {'type': 'function',
  'function': {'name': 'list_tasks',
               'description': 'List all scheduled tasks.',
               'parameters': {'type': 'object', 'properties': {}}}},
 {'type': 'function',
  'function': {'name': 'pause_task',
               'description': 'Pause a scheduled task.',
               'parameters': {'type': 'object',
                              'properties': {'task_id': {'type': 'string'}},
                              'required': ['task_id']}}},
 {'type': 'function',
  'function': {'name': 'resume_task',
               'description': 'Resume a paused scheduled task.',
               'parameters': {'type': 'object',
                              'properties': {'task_id': {'type': 'string'}},
                              'required': ['task_id']}}},
 {'type': 'function',
  'function': {'name': 'schedule_task',
               'description': 'Schedule either an exact message or an Agent task. Use '
                              'action_type="message" to send the prompt text unchanged at the '
                              'scheduled time, or action_type="agent_task" (default) to execute '
                              'the prompt with tools and report the result. schedule_type must be '
                              'cron, interval, or once. Use permission_mode="full_access" only '
                              'when the task MUST read/write files outside the workspace (the user '
                              'will be asked to confirm at creation time).',
               'parameters': {'type': 'object',
                              'properties': {'prompt': {'type': 'string'},
                                             'schedule_type': {'type': 'string',
                                                               'enum': ['cron',
                                                                        'interval',
                                                                        'once']},
                                             'schedule_value': {'type': 'string',
                                                                'description': "For 'cron': a "
                                                                               'crontab expression '
                                                                               "(e.g. '0 9 * * "
                                                                               "*'). For "
                                                                               "'interval': the "
                                                                               'number of SECONDS '
                                                                               'between runs (e.g. '
                                                                               "'3600' = hourly). "
                                                                               "For 'once': an "
                                                                               'ISO-8601 datetime, '
                                                                               'or empty to run as '
                                                                               'soon as possible.'},
                                             'schedule_timezone': {'type': 'string',
                                                                   'description': 'IANA timezone '
                                                                                  'used for cron '
                                                                                  'wall-clock '
                                                                                  'fields (e.g. '
                                                                                  "'Asia/Shanghai'). "
                                                                                  'Defaults to '
                                                                                  "'UTC'."},
                                             'action_type': {'type': 'string',
                                                             'enum': ['message', 'agent_task'],
                                                             'description': 'message sends prompt '
                                                                            'unchanged; agent_task '
                                                                            'executes prompt and '
                                                                            'reports the result.'},
                                             'permission_mode': {'type': 'string',
                                                                 'enum': ['workspace_only',
                                                                          'full_access'],
                                                                 'description': 'Permission scope. '
                                                                                "'workspace_only' "
                                                                                '(default) '
                                                                                'restricts all '
                                                                                'file access to '
                                                                                'the workspace. '
                                                                                "'full_access' "
                                                                                'allows '
                                                                                'reading/writing '
                                                                                'anywhere — the '
                                                                                'user must confirm '
                                                                                'before the task '
                                                                                'is created.'}},
                              'required': ['prompt', 'schedule_type', 'schedule_value']}}},
 {'type': 'function',
  'function': {'name': 'set_task_goal',
               'description': "Set or correct THE CURRENT Workbench task's goal, short title, "
                              'and/or one-line summary (简介 — the brief shown under the title on '
                              'the task card). Provide at least one of them. Use this when the '
                              "task's goal/title/summary don't match what the work is actually "
                              "about — for example after you've explored the project and "
                              "understood what should be done, or when the user's first message "
                              'was a question rather than a goal. These are shown on the task card '
                              'and in the task list. IMPORTANT: once the user has manually edited '
                              'the title, you can no longer change the title (the call keeps the '
                              "user's title and tells you so) — you can still update the goal and "
                              'summary. Only valid inside a Workbench task; does nothing in a '
                              'plain chat.',
               'parameters': {'type': 'object',
                              'properties': {'goal': {'type': 'string',
                                                      'description': 'The task objective as one '
                                                                     'concise, self-contained '
                                                                     "sentence (e.g. 'Add OAuth "
                                                                     "login to the web app.')."},
                                             'title': {'type': 'string',
                                                       'description': 'Short task title, a few '
                                                                      'words (<= 24 chars). '
                                                                      'Ignored if the user has '
                                                                      'manually edited the title.'},
                                             'summary': {'type': 'string',
                                                         'description': 'One short sentence (简介) '
                                                                        "shown as the task's "
                                                                        'subtitle, summarizing '
                                                                        'what this task is '
                                                                        'about.'}},
                              'required': []}}})
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
