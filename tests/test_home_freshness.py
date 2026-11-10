"""Home display schedule is independent from durable data maintenance."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from copy import deepcopy

import pytest

from cyrene.plugins.builtin.cyrene_cards.home import HomeCards
from cyrene.plugins.builtin.cyrene_cards.home_freshness import evidence, weather_expired
from cyrene.plugins.builtin.cyrene_cards.service import CardService


@pytest.fixture
def home_rig(tmp_path, monkeypatch):
    clock = [datetime(2026, 11, 9, 1, tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr('cyrene.plugins.builtin.cyrene_cards.home.time.time', lambda: clock[0])
    chat = {'id': 'assistant', 'projectId': 'personal', 'createdAt': 'birth', 'status': 'idle', 'messages': []}
    siblings, calls = [], []
    checkpoint = {'status': 'completed'}
    async def prepare(*args):
        calls.append(args)
        if checkpoint['status'] != 'completed':
            raise RuntimeError('Background provider failed')
    def forbidden(*args, **kwargs):
        raise AssertionError('Maintenance must not enter the foreground run or message port')
    port = SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(
        get=lambda _: chat, read_summaries=lambda: {'chats': siblings})),
        run_manager=SimpleNamespace(get=forbidden), dispatch_agent_message=forbidden)
    service = CardService(tmp_path)
    return SimpleNamespace(clock=clock, chat=chat, siblings=siblings, calls=calls, checkpoint=checkpoint,
                           port=port, service=service, prepare=prepare, home=HomeCards(service, lambda: port, prepare))


async def add_overview(rig):
    return await rig.service.command('assistant', None, 'card.create', {
        'template': 'content', 'spec': {'title': '今日安排', 'home_kind': 'overview', 'blocks': [
            {'block_id': 'weather', 'type': 'weather', 'location': '广州', 'condition': '小雨',
             'temperature': '20°C', 'range': '18–23°C', 'advice': '带伞', 'source': 'test observation', 'updated_at': datetime.fromtimestamp(rig.clock[0], timezone.utc).isoformat()}]}}, effect='initial')


@pytest.mark.asyncio
async def test_daily_display_slots_restart_and_no_backlog(home_rig):
    r = home_rig
    body = {'timezone': 'Asia/Shanghai', 'client_id': 'window-1'}
    assert (await r.home.claim_display('assistant', body))['slot'] is None  # no empty display consumes a slot
    await add_overview(r)
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-09:00'
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-09:00'  # response retry
    r.home = HomeCards(r.service, lambda: r.port, r.prepare)
    body['client_id'] = 'reopened'
    assert (await r.home.claim_display('assistant', body))['slot'] is None
    for hour in (12, 18, 22):
        r.clock[0] = datetime(2026, 11, 9, hour - 8, tzinfo=timezone.utc).timestamp()
        assert (await r.home.claim_display('assistant', body))['slot'] == f'2026-11-09:{hour}'
        body['client_id'] += '-restart'
        assert (await r.home.claim_display('assistant', body))['slot'] is None
    r.clock[0] += 17 * 3600  # next day first opens at 15:00; no morning/noon backlog
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-10:12'
    assert not r.calls  # display never wakes the agent


@pytest.mark.asyncio
async def test_multiple_windows_only_one_new_display(home_rig):
    r = home_rig
    await add_overview(r)
    results = await asyncio.gather(*(r.home.claim_display('assistant', {
        'timezone': 'Asia/Shanghai', 'client_id': f'window-{i}'}) for i in range(5)))
    assert sum(bool(result['slot']) for result in results) == 1


@pytest.mark.asyncio
async def test_task_changes_weather_expiry_and_restart(home_rig):
    r = home_rig
    await add_overview(r)
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'
    request = r.calls[-1][4]
    assert '[Welcome cards refresh]' in request and 'Refresh reasons:' in request
    assert 'Local time:' in request and 'Existing slots:' in request
    assert 'Prepare relevant welcome cards' not in request
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'
    r.home = HomeCards(r.service, lambda: r.port, r.prepare)
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'
    r.chat['messages'].extend([{'role': 'user', 'id': 'confirm', 'content': '确认预算'},
                               {'role': 'assistant', 'id': 'done', 'content': '已完成', 'attachments': [{'id': 'report'}]}])
    changed = await r.home.refresh('assistant', {})
    assert changed['status'] == 'updated'
    assert not any('weather expired' in reason for reason in changed['reasons'])
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'
    r.siblings.append({'id': 'task1', 'projectId': 'personal', 'status': 'completed', 'updatedAt': 'new'})
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'
    r.clock[0] += 3600
    assert any('weather expired' in reason for reason in (await r.home.refresh('assistant', {}))['reasons'])
    # Successful processing without a fresh observation does not make old weather fresh.
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'
    r.clock[0] += 600
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'


@pytest.mark.asyncio
async def test_busy_failure_retry_and_maintenance_do_not_loop(home_rig):
    r = home_rig
    await add_overview(r)
    r.chat['status'] = 'running'
    r.chat['pendingQuestion'] = {'id': 'foreground-question'}
    before = deepcopy(r.chat)
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'
    assert r.chat == before
    r.chat['messages'].append({'role': 'user', 'content': 'new task evidence'})
    r.checkpoint['status'] = 'failed'
    assert (await r.home.refresh('assistant', {}))['status'] == 'retry_pending'
    count = len(r.calls)
    assert (await r.home.refresh('assistant', {}))['status'] == 'retry_pending'
    assert len(r.calls) == count
    r.clock[0] += 120
    r.checkpoint['status'] = 'completed'
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'
    assert (await r.home.refresh('assistant', {}))['status'] == 'current'


def test_weather_timestamps_and_fingerprint():
    now = datetime.now(timezone.utc).timestamp()
    cards = [{'spec': {'home_kind': 'overview', 'blocks': [{'type': 'weather', 'updated_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}]}}]
    assert not weather_expired(cards, now)
    assert weather_expired(cards, now + 3600)
    for stamp in ('not a date', '', '2026-11-09T12:00:00'):
        stale = deepcopy(cards)
        stale[0]['spec']['blocks'][0]['updated_at'] = stamp
        assert weather_expired(stale, now)
    chat = {'id': 'a', 'messages': [{'role': 'user', 'content': 'go'}]}
    before = evidence(chat, [], {'task': {'body': 'in progress'}})
    assert before != evidence(chat, [], {'task': {'body': 'done'}})
    chat['messages'].extend([{'role': 'user', 'agentMessageKind': 'welcome_refresh'}, {'role': 'assistant', 'content': 'refreshed'}])
    assert before == evidence(chat, [], {'task': {'body': 'in progress'}})


@pytest.mark.asyncio
async def test_missing_model_preserves_cards_and_recovers_without_backoff(home_rig, caplog):
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeModelUnavailable
    r = home_rig
    await add_overview(r)
    before = await r.home.saved('assistant')
    async def unavailable(*args):
        raise HomeModelUnavailable('No configured model')
    r.home.prepare = unavailable
    for _ in range(3):
        assert (await r.home.refresh('assistant', {}))['status'] == 'model_unavailable'
        r.clock[0] += 30
    assert await r.home.saved('assistant') == before
    assert 'Background home preparation failed' not in caplog.text
    r.home.prepare = r.prepare
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'
