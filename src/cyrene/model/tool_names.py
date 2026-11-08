"""Stable wire names for function APIs, with canonical names kept in storage.

Use the common 64-character ASCII subset accepted by provider APIs. Aliases
are independent of tool order and activation, preserving prompt cache prefixes.
"""
from copy import deepcopy
import hashlib
import re

_PREFIX = 'cyrene_fn_'
_VALID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class ToolNames:
    def __init__(self):
        self.originals = {}

    def encode(self, name):
        if not isinstance(name, str) or not name:
            return name
        if _VALID.fullmatch(name) and not name.startswith(_PREFIX):
            return name
        alias = _PREFIX + hashlib.sha256(name.encode('utf-8')).hexdigest()[:48]
        if alias in self.originals and self.originals[alias] != name:
            raise ValueError('Tool wire-name collision')
        self.originals[alias] = name
        return alias

    def _call(self, call, transform):
        if not isinstance(call, dict):
            return
        function = call.get('function')
        target = function if isinstance(function, dict) else call
        if 'name' in target:
            target['name'] = transform(target['name'])

    def request(self, arguments):
        result = deepcopy(arguments)
        for tool in result.get('tools') or []:
            if isinstance(tool, dict) and tool.get('type', 'function') == 'function':
                self._call(tool, self.encode)
        choice = result.get('tool_choice')
        if isinstance(choice, dict):
            self._call(choice, self.encode)
        for message in result.get('messages') or []:
            for call in message.get('tool_calls') or []:
                self._call(call, self.encode)
            if message.get('role') in {'tool', 'function'} and 'name' in message:
                message['name'] = self.encode(message['name'])
            if isinstance(message.get('function_call'), dict):
                self._call(message['function_call'], self.encode)
        return result

    def response(self, result):
        result = deepcopy(result)
        for call in result.get('tool_calls') or []:
            self._call(call, lambda name: self.originals.get(name, name))
        return result
