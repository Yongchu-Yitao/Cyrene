"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'GetLearnedSkill',
               'description': 'View the full details of an auto-learned skill by name. Returns the '
                              "skill's description, trigger pattern, steps, input schema, and run "
                              'statistics.',
               'parameters': {'type': 'object',
                              'properties': {'name': {'type': 'string',
                                                      'description': 'The name of the learned '
                                                                     'skill to inspect.'}},
                              'required': ['name']}}},
 {'type': 'function',
  'function': {'name': 'InstallSkill',
               'description': 'Install an external skill from a local path. Supports .md / .txt / '
                              '.prompt / .json / .yaml / .yml files, directories containing '
                              'SKILL.md, and .zip archives. The skill is copied into managed '
                              'storage, registered, enabled, and exposed to the agent catalog on '
                              'the next conversation turn. Use this after an agent finishes '
                              'generating a complete Skill directory; writing SKILL.md alone does '
                              'not register it.',
               'parameters': {'type': 'object',
                              'properties': {'path': {'type': 'string',
                                                      'description': 'Absolute or '
                                                                     'workspace-relative path to '
                                                                     'the skill file, directory, '
                                                                     'or zip archive.'}},
                              'required': ['path']}}},
 {'type': 'function',
  'function': {'name': 'ListSkills',
               'description': 'List all installed external skills with their ID, name, '
                              'description, and enabled status.',
               'parameters': {'type': 'object', 'properties': {}}}},
 {'type': 'function',
  'function': {'name': 'LoadSkill',
               'description': 'Load the complete SKILL.md and resource inventory for one enabled '
                              'external Skill. Loaded instructions apply only to the current agent '
                              'task.',
               'parameters': {'type': 'object',
                              'properties': {'skill_id': {'type': 'string',
                                                          'description': 'Exact Skill ID or name '
                                                                         'returned by '
                                                                         'SearchSkills.'}},
                              'required': ['skill_id']}}},
 {'type': 'function',
  'function': {'name': 'ReadSkillResource',
               'description': 'Read one text resource declared by a loaded external Skill. Paths '
                              'are confined to the Skill root; binary resources return metadata '
                              'only.',
               'parameters': {'type': 'object',
                              'properties': {'skill_id': {'type': 'string',
                                                          'description': 'Exact enabled Skill ID '
                                                                         'or name.'},
                                             'path': {'type': 'string',
                                                      'description': 'Relative resource path from '
                                                                     'the Skill resource '
                                                                     'inventory.'}},
                              'required': ['skill_id', 'path']}}},
 {'type': 'function',
  'function': {'name': 'RunLearnedSkill',
               'description': 'Execute an auto-learned skill by name. Runs all its steps (tool '
                              'calls) with optional parameter overrides and returns the results '
                              "from each step. Increments the skill's run counter. Only skills "
                              'without high-risk steps (shell commands, file writes) can be '
                              'executed.',
               'parameters': {'type': 'object',
                              'properties': {'name': {'type': 'string',
                                                      'description': 'The name of the learned '
                                                                     'skill to execute.'},
                                             'params': {'type': 'object',
                                                        'description': 'Optional parameter values '
                                                                       'to substitute into the '
                                                                       "skill's argument "
                                                                       'templates.',
                                                        'additionalProperties': True}},
                              'required': ['name']}}},
 {'type': 'function',
  'function': {'name': 'SearchSkills',
               'description': 'Search enabled external Skills by ID, name, full description, and '
                              'tags. Use this when the relevant Skill is not already obvious from '
                              'the injected catalog. Returns metadata only; call LoadSkill before '
                              'following a Skill.',
               'parameters': {'type': 'object',
                              'properties': {'query': {'type': 'string',
                                                       'description': 'Words describing the '
                                                                      'capability or workflow to '
                                                                      'find.'}}}}},
 {'type': 'function',
  'function': {'name': 'UninstallSkill',
               'description': 'Uninstall an external skill by its ID or name. Removes the skill '
                              'files and disables it.',
               'parameters': {'type': 'object',
                              'properties': {'skill_id': {'type': 'string',
                                                          'description': 'The ID or name of the '
                                                                         'skill to uninstall.'}},
                              'required': ['skill_id']}}})
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
