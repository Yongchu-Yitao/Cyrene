"""Delivery regressions: producer metadata, duplicate/racing insert, context repair."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.workbench.chat import background_projection as projection
from cyrene.plugins.builtin.cyrene_proactive.projection import create_proactive_chat
from cyrene.plugins.builtin.cyrene_schedule.projection import create_scheduled_chat


@pytest.fixture
def delivery(monkeypatch):
    payload = {'chats': []}
    def find(data, chat_id):
        return next((chat for chat in data['chats'] if chat['id'] == chat_id), None)
    repository = SimpleNamespace(get=lambda chat_id: find(payload, chat_id), find=find, mutate=lambda fn: fn(payload))
    service = SimpleNamespace(repository=repository,
        create_chat=lambda project, title, model: {'projectId': project, 'title': title, 'model': model},
        public_chat_light=lambda chat: dict(chat))
    monkeypatch.setattr(projection, 'ChatService', lambda db: service)
    monkeypatch.setattr(projection, 'application_plugin_service', lambda name: None)
    repair = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(projection, '_ensure_proactive_context', repair)
    monkeypatch.setattr(projection, 'publish_chat_changed', publish)
    from cyrene.observability import debug
    monkeypatch.setattr(debug, 'publish_event', AsyncMock())
    return payload, repository, repair, publish


@pytest.mark.parametrize('producer,title', [(create_scheduled_chat, 'Scheduled task'), (create_proactive_chat, 'Proactive work')])
async def test_background_producers_repair_context_without_duplicate_delivery(delivery, producer, title):
    payload, _, repair, publish = delivery
    result = await producer('db', 'project', ' first ', chat_id='stable', model='model', source_chat_id='source', lang='en')
    original = deepcopy(payload)
    repeated = await producer('db', 'project', 'replacement must not win', chat_id='stable', lang='en')
    assert result == repeated == {'chat_id': 'stable', 'project_id': 'project', 'title': title}
    assert payload == original
    chat = payload['chats'][0]
    assert chat['completedTurnCount'] == 0
    assert chat['sourceChatId'] == 'source'
    assert chat['messages'][0]['content'] == 'first'
    assert chat['messages'][0]['systemInitiated'] is True
    assert repair.await_count == 2
    publish.assert_awaited_once()


async def test_racing_insert_projects_the_winner_and_does_not_publish(delivery):
    payload, repository, repair, publish = delivery
    winner = {'id': 'stable', 'projectId': 'other', 'title': 'winner', 'messages': [{'role': 'assistant', 'content': 'winner text'}]}
    def mutate(fn):
        payload['chats'].append(winner)
        return fn(payload)
    repository.mutate = mutate
    result = await create_scheduled_chat('db', 'project', 'loser text', chat_id='stable', lang='en')
    assert result == {'chat_id': 'stable', 'project_id': 'other', 'title': 'winner'}
    assert payload['chats'] == [winner]
    assert repair.await_args.args[2]['content'] == 'winner text'
    publish.assert_not_awaited()


async def test_invalid_or_already_delivered_input_does_not_evaluate_usage(delivery, monkeypatch):
    from cyrene.plugins.builtin.cyrene_proactive import projection as proactive
    def usage(*_):
        raise AssertionError('usage should not be evaluated')
    monkeypatch.setattr(proactive, 'runtime_usage_message_fields', usage)
    assert await create_proactive_chat('db', '', 'text', chat_id='stable') is None
    delivery[0]['chats'].append({'id': 'stable', 'title': 'existing', 'messages': []})
    assert await create_proactive_chat('db', 'project', 'text', chat_id='stable')


async def test_completed_context_is_not_rewritten_and_missing_context_retains_durable_ids(monkeypatch):
    from cyrene.workbench.core_adapter import conversation_runtime
    checkpoint = {'status': 'completed'}
    monkeypatch.setattr(conversation_runtime, 'ConversationRuntime', lambda db: SimpleNamespace(context_checkpoint=lambda chat_id: checkpoint))
    writes = []
    monkeypatch.setattr(projection, 'append_context_record', lambda *args, **kwargs: writes.append((args, kwargs)))
    message = {'id': 'msg', 'content': 'result', 'model': 'model'}
    await projection._ensure_proactive_context('db', 'stable', message)
    assert writes == []
    checkpoint.clear()
    await projection._ensure_proactive_context('db', 'stable', message)
    args, kwargs = writes[0]
    assert args[:2] == ('db', 'stable')
    assert args[2]['run_id'] == 'proactive_stable'
    assert args[2]['public_message_id'] == 'msg'
    assert kwargs == {'node_id': 'context_proactive_stable', 'create_tree': True, 'require_idle': True}
