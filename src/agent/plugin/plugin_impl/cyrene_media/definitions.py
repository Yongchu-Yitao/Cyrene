"""Editable input schemas used by this Plugin pack."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_TOOL_DEFS: tuple[dict[str, Any], ...] = ({'type': 'function',
  'function': {'name': 'StartMediaGeneration',
               'description': 'Main agent only. Submit 1-8 image, video, or music generation jobs '
                              'to the independent media daemon and return immediately. Successful '
                              'outputs are attached directly to the current chat, then one '
                              'internal wake resumes the Agent after every job in the batch is '
                              'terminal. After submission, end the turn immediately and never '
                              'poll, wait, or start a terminal watcher.',
               'parameters': {'type': 'object',
                              'properties': {'requests': {'type': 'array',
                                                          'minItems': 1,
                                                          'maxItems': 8,
                                                          'description': 'Independent media jobs. '
                                                                         'Jobs in one batch may '
                                                                         'run in parallel.',
                                                          'items': {'type': 'object',
                                                                    'properties': {'kind': {'type': 'string',
                                                                                            'enum': ['image',
                                                                                                     'video',
                                                                                                     'music']},
                                                                                   'prompt': {'type': 'string',
                                                                                              'description': 'Generation '
                                                                                                             'prompt. '
                                                                                                             'May '
                                                                                                             'be '
                                                                                                             'empty '
                                                                                                             'only '
                                                                                                             'for '
                                                                                                             'music '
                                                                                                             'with '
                                                                                                             'lyrics.'},
                                                                                   'provider': {'type': 'string',
                                                                                                'enum': ['auto',
                                                                                                         'comfyui',
                                                                                                         'openai',
                                                                                                         'seedream',
                                                                                                         'seedance',
                                                                                                         'minimax',
                                                                                                         'google'],
                                                                                                'description': 'Provider '
                                                                                                               'override. '
                                                                                                               'Defaults '
                                                                                                               'to '
                                                                                                               'auto.'},
                                                                                   'model': {'type': 'string',
                                                                                             'description': 'Optional '
                                                                                                            'provider '
                                                                                                            'model '
                                                                                                            'override.'},
                                                                                   'name': {'type': 'string',
                                                                                            'description': 'Optional '
                                                                                                           'output '
                                                                                                           'filename.'},
                                                                                   'reference_paths': {'type': 'array',
                                                                                                       'maxItems': 30,
                                                                                                       'items': {'type': 'string'},
                                                                                                       'description': 'Workspace '
                                                                                                                      'or '
                                                                                                                      'already '
                                                                                                                      'managed '
                                                                                                                      'files '
                                                                                                                      'to '
                                                                                                                      'use '
                                                                                                                      'as '
                                                                                                                      'generation '
                                                                                                                      'references.'},
                                                                                   'reference_attachment_ids': {'type': 'array',
                                                                                                                'maxItems': 30,
                                                                                                                'items': {'type': 'string'},
                                                                                                                'description': 'Attachment '
                                                                                                                               'ids '
                                                                                                                               'explicitly '
                                                                                                                               'present '
                                                                                                                               'in '
                                                                                                                               'the '
                                                                                                                               'current '
                                                                                                                               'chat.'},
                                                                                   'reference_urls': {'type': 'array',
                                                                                                      'maxItems': 30,
                                                                                                      'items': {'type': 'string'},
                                                                                                      'description': 'Public '
                                                                                                                     'HTTPS '
                                                                                                                     'reference '
                                                                                                                     'URLs. '
                                                                                                                     'Use '
                                                                                                                     'this '
                                                                                                                     'for '
                                                                                                                     'provider-supported '
                                                                                                                     'remote '
                                                                                                                     'video '
                                                                                                                     'references; '
                                                                                                                     'prefer '
                                                                                                                     'chat '
                                                                                                                     'attachment '
                                                                                                                     'ids '
                                                                                                                     'for '
                                                                                                                     'local '
                                                                                                                     'images.'},
                                                                                   'reference_roles': {'type': 'array',
                                                                                                       'maxItems': 30,
                                                                                                       'items': {'type': 'string',
                                                                                                                 'enum': ['first_frame',
                                                                                                                          'last_frame',
                                                                                                                          'reference',
                                                                                                                          'subject',
                                                                                                                          'audio',
                                                                                                                          'reference_image',
                                                                                                                          'reference_video',
                                                                                                                          'reference_audio']},
                                                                                                       'description': 'Optional '
                                                                                                                      'role '
                                                                                                                      'for '
                                                                                                                      'each '
                                                                                                                      'resolved '
                                                                                                                      'reference, '
                                                                                                                      'ordered '
                                                                                                                      'as '
                                                                                                                      'reference_paths, '
                                                                                                                      'reference_attachment_ids, '
                                                                                                                      'then '
                                                                                                                      'reference_urls.'},
                                                                                   'mask_path': {'type': 'string',
                                                                                                 'description': 'Workspace '
                                                                                                                'or '
                                                                                                                'managed '
                                                                                                                'mask '
                                                                                                                'image '
                                                                                                                'for '
                                                                                                                'providers '
                                                                                                                'that '
                                                                                                                'support '
                                                                                                                'masked '
                                                                                                                'image '
                                                                                                                'edits.'},
                                                                                   'mask_attachment_id': {'type': 'string',
                                                                                                          'description': 'Mask '
                                                                                                                         'image '
                                                                                                                         'attachment '
                                                                                                                         'id '
                                                                                                                         'explicitly '
                                                                                                                         'present '
                                                                                                                         'in '
                                                                                                                         'the '
                                                                                                                         'current '
                                                                                                                         'chat.'},
                                                                                   'negative_prompt': {'type': 'string'},
                                                                                   'size': {'type': 'string',
                                                                                            'description': 'Provider-supported '
                                                                                                           'output '
                                                                                                           'dimensions, '
                                                                                                           'such '
                                                                                                           'as '
                                                                                                           '1024x1024.'},
                                                                                   'aspect_ratio': {'type': 'string',
                                                                                                    'description': 'Provider-supported '
                                                                                                                   'aspect '
                                                                                                                   'ratio, '
                                                                                                                   'such '
                                                                                                                   'as '
                                                                                                                   '16:9.'},
                                                                                   'resolution': {'type': 'string',
                                                                                                  'description': 'Provider-supported '
                                                                                                                 'resolution, '
                                                                                                                 'such '
                                                                                                                 'as '
                                                                                                                 '720p, '
                                                                                                                 '1080p, '
                                                                                                                 '2K, '
                                                                                                                 'or '
                                                                                                                 '4K.'},
                                                                                   'duration': {'anyOf': [{'type': 'number',
                                                                                                           'enum': [-1]},
                                                                                                          {'type': 'number',
                                                                                                           'minimum': 1,
                                                                                                           'maximum': 600}],
                                                                                                'description': 'Requested '
                                                                                                               'audio '
                                                                                                               'or '
                                                                                                               'video '
                                                                                                               'duration '
                                                                                                               'in '
                                                                                                               'seconds. '
                                                                                                               '-1 '
                                                                                                               'asks '
                                                                                                               'a '
                                                                                                               'provider '
                                                                                                               'that '
                                                                                                               'supports '
                                                                                                               'it '
                                                                                                               'to '
                                                                                                               'choose '
                                                                                                               'automatically.'},
                                                                                   'quality': {'type': 'string'},
                                                                                   'output_format': {'type': 'string'},
                                                                                   'number_of_outputs': {'type': 'integer',
                                                                                                         'minimum': 1,
                                                                                                         'maximum': 8},
                                                                                   'lyrics': {'type': 'string',
                                                                                              'description': 'Lyrics '
                                                                                                             'for '
                                                                                                             'music '
                                                                                                             'generation.'},
                                                                                   'is_instrumental': {'type': 'boolean'},
                                                                                   'generate_audio': {'type': 'boolean',
                                                                                                      'description': 'Whether '
                                                                                                                     'a '
                                                                                                                     'video '
                                                                                                                     'provider '
                                                                                                                     'should '
                                                                                                                     'generate '
                                                                                                                     'native '
                                                                                                                     'audio.'},
                                                                                   'seed': {'type': 'integer',
                                                                                            'minimum': 0},
                                                                                   'parameters': {'type': 'object',
                                                                                                  'description': 'Advanced '
                                                                                                                 'provider-specific '
                                                                                                                 'parameters.',
                                                                                                  'additionalProperties': True}},
                                                                    'required': ['kind'],
                                                                    'additionalProperties': False}},
                                             'idempotency_key': {'type': 'string',
                                                                 'minLength': 8,
                                                                 'maxLength': 160,
                                                                 'description': 'Stable key to '
                                                                                'reuse only when '
                                                                                'retrying this '
                                                                                'exact batch '
                                                                                'submission.'}},
                              'required': ['requests'],
                              'additionalProperties': False}}},)
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
