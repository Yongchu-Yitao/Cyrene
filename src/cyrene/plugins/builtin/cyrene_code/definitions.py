"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'DeleteShell',
               'description': 'Permanently terminate and delete a terminal created by the Agent in '
                              'this conversation. Ask the user and wait for confirmation before '
                              'calling. Deletion cancels any pending wake. If multiple terminal '
                              'panes are visible and no identifier is provided, ask which terminal '
                              'to use.',
               'parameters': {'type': 'object',
                              'properties': {'shell_id': {'type': 'string'},
                                             'name': {'type': 'string'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'InterruptShell',
               'description': 'Send Ctrl+C to an authorized running terminal without closing it. '
                              'If multiple terminal panes are visible and no identifier is '
                              'provided, ask the user which terminal to use.',
               'parameters': {'type': 'object',
                              'properties': {'shell_id': {'type': 'string'},
                                             'name': {'type': 'string'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'ListShells',
               'description': 'List terminals bound to the current conversation and terminals '
                              'currently visible in the active split. A visible terminal can be '
                              'returned even when no terminal is bound to this conversation.',
               'parameters': {'type': 'object', 'properties': {}}}},
 {'type': 'function',
  'function': {'name': 'ReadShell',
               'description': 'Read an authorized terminal. Without shell_id or name, '
                              'automatically use the single terminal currently visible in the '
                              'active split; this works even when it is not bound to the '
                              'conversation. view=screen returns the rendered VT viewport, '
                              'view=scrollback returns durable PTY history, view=commands returns '
                              'indexed local or remote commands, and view=command_output returns '
                              'one command output. If multiple terminal panes are visible, ask the '
                              'user which terminal to use.',
               'parameters': {'type': 'object',
                              'properties': {'shell_id': {'type': 'string'},
                                             'name': {'type': 'string'},
                                             'view': {'type': 'string',
                                                      'enum': ['screen',
                                                               'scrollback',
                                                               'commands',
                                                               'command_output'],
                                                      'default': 'screen'},
                                             'command_id': {'type': 'string',
                                                            'description': 'Command identifier '
                                                                           'required by '
                                                                           'view=command_output.'},
                                             'cursor': {'type': 'integer',
                                                        'minimum': 0,
                                                        'description': 'Scrollback byte sequence '
                                                                       'to read forward from. Omit '
                                                                       'to read the latest '
                                                                       'retained range.'},
                                             'max_bytes': {'type': 'integer',
                                                           'minimum': 1,
                                                           'maximum': 524288,
                                                           'default': 65536}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'SendShell',
               'description': 'Send text or a terminal key to an authorized shared terminal. '
                              'Without shell_id or name, automatically use the single terminal '
                              'currently visible in the active split, even when it is not bound to '
                              'the conversation. User input has priority and non-owned terminals '
                              'require explicit user authorization. If multiple terminal panes are '
                              'visible, ask which terminal to use.',
               'parameters': {'type': 'object',
                              'properties': {'shell_id': {'type': 'string'},
                                             'name': {'type': 'string'},
                                             'text': {'type': 'string'},
                                             'sensitive': {'type': 'boolean',
                                                           'description': 'Set true only when text '
                                                                          'is a password, '
                                                                          'passphrase, token, or '
                                                                          'other secret being '
                                                                          'entered into an '
                                                                          'existing terminal '
                                                                          'prompt. The input is '
                                                                          'sent normally but '
                                                                          'redacted from tool '
                                                                          'activity and '
                                                                          'permission-review '
                                                                          'records.'},
                                             'key': {'type': 'string',
                                                     'enum': ['enter',
                                                              'escape',
                                                              'tab',
                                                              'shift_tab',
                                                              'up',
                                                              'down',
                                                              'left',
                                                              'right',
                                                              'home',
                                                              'end',
                                                              'insert',
                                                              'delete',
                                                              'page_up',
                                                              'page_down',
                                                              'backspace',
                                                              'f1',
                                                              'f2',
                                                              'f3',
                                                              'f4',
                                                              'f5',
                                                              'f6',
                                                              'f7',
                                                              'f8',
                                                              'f9',
                                                              'f10',
                                                              'f11',
                                                              'f12',
                                                              'ctrl_space',
                                                              'ctrl_a',
                                                              'ctrl_b',
                                                              'ctrl_c',
                                                              'ctrl_d',
                                                              'ctrl_e',
                                                              'ctrl_f',
                                                              'ctrl_g',
                                                              'ctrl_h',
                                                              'ctrl_i',
                                                              'ctrl_j',
                                                              'ctrl_k',
                                                              'ctrl_l',
                                                              'ctrl_m',
                                                              'ctrl_n',
                                                              'ctrl_o',
                                                              'ctrl_p',
                                                              'ctrl_q',
                                                              'ctrl_r',
                                                              'ctrl_s',
                                                              'ctrl_t',
                                                              'ctrl_u',
                                                              'ctrl_v',
                                                              'ctrl_w',
                                                              'ctrl_x',
                                                              'ctrl_y',
                                                              'ctrl_z']}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'ShowShell',
               'description': 'Show an authorized terminal in a split. Creates a split when only '
                              'one pane is open; otherwise replaces one existing pane. Use only '
                              'when the user explicitly asks to open or show it. If multiple '
                              'terminal panes are visible and no identifier is provided, ask which '
                              'terminal to use.',
               'parameters': {'type': 'object',
                              'properties': {'shell_id': {'type': 'string'},
                                             'name': {'type': 'string'}},
                              'required': []}}},
 {'type': 'function',
  'function': {'name': 'StartShell',
               'description': 'Create a conversation-bound terminal in the Cyrene Terminal Daemon. '
                              "It appears in the terminal list but does not replace the user's "
                              'current view. A managed SSH initial command is sent only after the '
                              'injected remote launcher confirms that the connection is ready. '
                              'With wake_on_exit and a local initial command, the command runs as '
                              'a durable one-shot job and wakes this conversation after exit.',
               'parameters': {'type': 'object',
                              'properties': {'cwd': {'type': 'string'},
                                             'title': {'type': 'string',
                                                       'description': 'Terminal name. When the '
                                                                      'user supplies a name, pass '
                                                                      'it exactly; do not leave '
                                                                      'this field empty or only '
                                                                      'repeat the name in the '
                                                                      'response.'},
                                             'command': {'type': 'string',
                                                         'description': 'Optional initial command. '
                                                                        'Local shells run it after '
                                                                        'startup; managed SSH '
                                                                        'sends it only after the '
                                                                        'remote connection reports '
                                                                        'ready.'},
                                             'ssh_target': {'type': 'string',
                                                            'description': 'Optional OpenSSH Host '
                                                                           'alias or user@host. '
                                                                           'Creates a managed '
                                                                           'remote terminal '
                                                                           'without storing '
                                                                           'credentials.'},
                                             'remote_cwd': {'type': 'string',
                                                            'description': 'Initial absolute '
                                                                           'directory on the '
                                                                           'remote host.'},
                                             'tmux_session': {'type': 'string',
                                                              'description': 'Optional remote tmux '
                                                                             'session name. Cyrene '
                                                                             'attaches or creates '
                                                                             'it and can restore '
                                                                             'it after transport '
                                                                             'loss.'},
                                             'wake_on_exit': {'type': 'boolean',
                                                              'description': 'When true with an '
                                                                             'initial command, run '
                                                                             'that command as a '
                                                                             'one-shot background '
                                                                             'job and '
                                                                             'automatically wake '
                                                                             'this Workbench chat '
                                                                             'when it completes '
                                                                             '(success or '
                                                                             'failure). Without an '
                                                                             'initial command, '
                                                                             'wake only after the '
                                                                             'persistent shell '
                                                                             'process exits. '
                                                                             'Prefer this over '
                                                                             'sleeping, polling, '
                                                                             'or blocking for long '
                                                                             'jobs.'},
                                             'wake_note': {'type': 'string',
                                                           'description': 'Optional short intent '
                                                                          'remembered for the wake '
                                                                          "turn (e.g. 'review "
                                                                          'training metrics and '
                                                                          'propose next '
                                                                          "hyperparams')."}}}}})
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
