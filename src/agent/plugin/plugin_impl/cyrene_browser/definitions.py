"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'browser_click',
               'description': 'Click an element on the current page. If the click navigates to a '
                              'new page, the tool waits for it to load and returns the new URL and '
                              'title. Works in the live embedded browser (Electron) — the user '
                              'sees the click happen. Call browser_navigate first.',
               'parameters': {'type': 'object',
                              'properties': {'selector': {'type': 'string',
                                                          'description': 'CSS selector for the '
                                                                         'element to click (e.g. '
                                                                         "'button.submit', "
                                                                         "'#login-btn', "
                                                                         '\'a[href="/page"]\')'}},
                              'required': ['selector']}}},
 {'type': 'function',
  'function': {'name': 'browser_navigate',
               'description': 'Navigate the current browser tab to a URL and return the page text '
                              'plus readable text links, clickable refs, and their real URLs. Use '
                              'this for a starting page, an exact URL explicitly requested by the '
                              'user, or only when the target cannot be reached through visible '
                              'page UI. Once a page is open, prefer browser_snapshot followed by '
                              'browser_click_ref instead of navigating directly to a link URL. '
                              'Always reuses the SAME tab — never opens a new one. Do NOT use '
                              'browser_tab_new unless the user explicitly says to keep a page '
                              'open. In the desktop app (Electron) the page is fully rendered '
                              '(images, video, interactive) and the user can see and operate the '
                              'live browser in the side panel.',
               'parameters': {'type': 'object',
                              'properties': {'url': {'type': 'string',
                                                     'description': 'The full URL to navigate to '
                                                                    '(e.g. '
                                                                    'https://example.com/page)'},
                                             'reason': {'type': 'string',
                                                        'enum': ['starting_page',
                                                                 'user_exact_url',
                                                                 'ui_unreachable'],
                                                        'description': 'Why direct URL navigation '
                                                                       'is necessary. Use '
                                                                       'user_exact_url only when '
                                                                       'the user explicitly '
                                                                       'requested this exact URL.'},
                                             'snapshot_token': {'type': 'string',
                                                                'description': 'Required only for '
                                                                               'ui_unreachable. '
                                                                               'Must be the opaque '
                                                                               'token returned by '
                                                                               'the latest '
                                                                               'browser_snapshot '
                                                                               'for the active '
                                                                               'page.'}},
                              'required': ['url', 'reason']}}},
 {'type': 'function',
  'function': {'name': 'browser_request_takeover',
               'description': 'Hand the browser to the user to log in. Call this AS SOON AS you '
                              'hit a login wall, CAPTCHA, or 2FA — before doing any deeper work on '
                              'the page. In the desktop app, the user completes it in the embedded '
                              'browser; fallback mode opens a real browser window. You pause until '
                              'they confirm, then resume in the same session.',
               'parameters': {'type': 'object',
                              'properties': {'reason': {'type': 'string',
                                                        'description': 'Short message telling the '
                                                                       'user what to log into '
                                                                       "(e.g. 'Please log in to "
                                                                       "your Gmail account')."}},
                              'required': ['reason']}}},
 {'type': 'function',
  'function': {'name': 'browser_type',
               'description': 'Type text into an input element on the current browser page. Call '
                              'browser_navigate first.',
               'parameters': {'type': 'object',
                              'properties': {'selector': {'type': 'string',
                                                          'description': 'CSS selector for the '
                                                                         'input element.'},
                                             'text': {'type': 'string',
                                                      'description': 'The text to type.'},
                                             'submit': {'type': 'boolean',
                                                        'description': 'Press Enter after typing '
                                                                       'to submit the form.'}},
                              'required': ['selector', 'text']}}})
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
