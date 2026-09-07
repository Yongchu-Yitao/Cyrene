from dataclasses import FrozenInstanceError

import pytest

from cyrene.workbench.http.workbench.chat_routes.send_input import (
    SendInput, append_user_message, resolve_send_command, retry_send_input,
)
from cyrene.workbench.http.workbench.chat_routes.send_request import SendOrigin
from cyrene.workbench.chat.chat_usage import runtime_model_message_fields, generation_message_fields


@pytest.mark.asyncio
async def test_retry_uses_history_input_and_keeps_dynamic_descriptor_compatibility(monkeypatch):
    async def missing(_command, _project):
        return None

    monkeypatch.setattr('cyrene.workbench.chat.slash_commands.resolve_slash_command', missing)
    descriptor = {'id': 'workflow', 'system_prompt': 'context'}
    source = SendInput('new input', '/workflow new input', 'workflow', [{'id': 'new'}], [], descriptor, 'context')
    stored_files = [{'id': 'stored'}]
    entry = {'content': '/old historical text', 'command': 'old', 'agentAttachments': stored_files,
             'attachments': [{'id': 'public'}]}
    result = await retry_send_input(source, entry, {'projectId': 'p'}, False, lambda files: files)
    assert result.message == 'historical text'
    assert result.public_message == '/old historical text'
    assert result.command == ''
    assert result.normalized is stored_files
    assert result.public_attachments is entry['attachments']
    assert result.dynamic_command is descriptor
    assert source.message == 'new input'
    with pytest.raises(FrozenInstanceError):
        result.message = 'changed'


@pytest.mark.asyncio
async def test_external_command_resolution_preserves_public_text_and_rejects_undeclared_commands():
    source = SendInput('/inspect details', '/inspect details', '', [], [])
    result, error = await resolve_send_command(source, {'agentCommands': ['inspect']}, True, 'en')
    assert error is None
    assert (result.message, result.public_message, result.command) == ('details', '/inspect details', 'inspect')
    result, error = await resolve_send_command(SendInput('', '', 'other', [], []), {'agentCommands': ['inspect']}, True, 'en')
    assert result is None
    assert error.status_code == 400
    assert b'agent_command_unavailable' in error.body


def test_new_turn_retains_attachment_identity_and_locks_binding_without_overwriting_locked_title():
    source = SendInput('hello', 'hello', '', [{'id': 'internal'}], [{'id': 'public'}])
    chat = {'messages': [], 'title': 'Chosen title', 'titleLocked': True, 'agent': {'id': 'agent'}}
    turn = append_user_message(chat, chat['messages'], source, SendOrigin.parse({'clientRequestId': 'request'}), 'now', lambda _: 'msg-id')
    assert turn.user_entry is chat['messages'][0]
    assert turn.user_entry['agentAttachments'] is source.normalized
    assert turn.user_entry['clientRequestId'] == 'request'
    assert chat['agent']['bindingLocked'] is True
    assert chat['title'] == 'Chosen title'
    assert turn.should_generate_title is False


def test_shared_projection_copies_usage_and_identity_without_conflating_latest_request():
    usage = {'total_tokens': 30}
    latest = {'total_tokens': 4}
    identity = {'provider': 'local'}
    fields = runtime_model_message_fields(usage, latest, identity)
    assert fields == {'usage': usage, 'latestRequestUsage': latest, 'modelIdentity': identity}
    assert fields['usage'] is not usage
    assert fields['modelIdentity'] is not identity
    assert generation_message_fields(0, -1.23456) == {'modelGenerationDurationMs': 0, 'outputTokensPerSecond': -1.235}
    assert generation_message_fields() == {}
