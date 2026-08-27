"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'send_file',
               'description': 'Main agent only. Deliver a file you actually created as a '
                              'downloadable artifact. The file must exist; never guess paths or '
                              'merely print one in chat. If the user requests a specific save '
                              'location, save the file there first, then call this tool, including '
                              'for authorized paths outside the workspace. This tool does not save '
                              'or move files.',
               'parameters': {'type': 'object',
                              'properties': {'path': {'type': 'string',
                                                      'description': 'Real workspace-relative or '
                                                                     'absolute file path; '
                                                                     'authorized user-requested '
                                                                     'locations are supported.'},
                                             'name': {'type': 'string',
                                                      'description': 'Optional display filename '
                                                                     'shown in the WebUI.'},
                                             'text': {'type': 'string',
                                                      'description': 'Brief description of the '
                                                                     'file contents. Keep it '
                                                                     'factual and short.'}},
                              'required': ['path']}}},
 {'type': 'function',
  'function': {'name': 'send_message',
               'description': 'Main agent only. Send a brief user-visible mid-run reply in the '
                              'current chat. For tool-using work this MUST be the first call in '
                              'the first execution batch, immediately followed by the first useful '
                              'tool call in the same batch whenever safe. Never use this for '
                              'subagent coordination or subagent final delivery.',
               'parameters': {'type': 'object',
                              'properties': {'text': {'type': 'string'}},
                              'required': ['text']}}},
 {'type': 'function',
  'function': {'name': 'send_message_to_user',
               'description': 'Reply directly to the user. Only available when the user has '
                              "@mentioned you directly. Use this to respond to the user's direct "
                              'message. Not for normal rounds — return the final response normally.',
               'parameters': {'type': 'object',
                              'properties': {'text': {'type': 'string'}},
                              'required': ['text']}}},
 {'type': 'function',
  'function': {'name': 'send_notification',
               'description': 'Send a desktop or webhook notification. Use for alerts, reminders, '
                              "or when you need the user's attention outside the chat. Supports "
                              'Telegram and WeChat if configured.',
               'parameters': {'type': 'object',
                              'properties': {'title': {'type': 'string',
                                                       'description': 'Short notification title.'},
                                             'text': {'type': 'string',
                                                      'description': 'Notification body text.'},
                                             'channel': {'type': 'string',
                                                         'description': "Delivery channel: 'auto' "
                                                                        '(try all available), '
                                                                        "'desktop', 'webhook', "
                                                                        "'telegram', 'wechat', or "
                                                                        "'sse'."}},
                              'required': ['text']}}},
 {'type': 'function',
  'function': {'name': 'send_telegram',
               'description': 'Send a Telegram message to the user. NOT for agent-to-agent '
                              'communication — use send_agent_message instead.',
               'parameters': {'type': 'object',
                              'properties': {'text': {'type': 'string'}},
                              'required': ['text']}}},
 {'type': 'function',
  'function': {'name': 'send_wechat_file',
               'description': 'Send a file you have CREATED to the user via WeChat. Only works '
                              'when the current conversation is on the WeChat channel — files are '
                              'encrypted with AES-128-ECB and uploaded to CDN. A delivery notice '
                              'appears in the WebUI chat history.',
               'parameters': {'type': 'object',
                              'properties': {'path': {'type': 'string',
                                                      'description': 'Workspace-relative or '
                                                                     'absolute path to a file you '
                                                                     'created that actually '
                                                                     'exists.'},
                                             'name': {'type': 'string',
                                                      'description': 'Optional display filename '
                                                                     'shown in WeChat and WebUI.'},
                                             'text': {'type': 'string',
                                                      'description': 'Brief description shown '
                                                                     'alongside the file in '
                                                                     'WebUI.'}},
                              'required': ['path']}}})
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
