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
    async def prepare(*args, **kwargs):
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
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-09:08'
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-09:08'  # response retry
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
    async def unavailable(*args, **kwargs):
        raise HomeModelUnavailable('No configured model')
    r.home.prepare = unavailable
    for _ in range(3):
        assert (await r.home.refresh('assistant', {}))['status'] == 'model_unavailable'
        r.clock[0] += 30
    assert await r.home.saved('assistant') == before
    assert 'Background home preparation failed' not in caplog.text
    r.home.prepare = r.prepare
    assert (await r.home.refresh('assistant', {}))['status'] == 'updated'


@pytest.mark.parametrize(('stamp', 'expected'), [
    ('2026-11-09T07:29:59+08:00', '2026-11-08T22:00:00+08:00'),
    ('2026-11-09T07:30:00+08:00', '2026-11-09T08:00:00+08:00'),
    ('2026-11-09T11:29:59+08:00', '2026-11-09T08:00:00+08:00'),
    ('2026-11-09T11:30:00+08:00', '2026-11-09T12:00:00+08:00'),
    ('2026-11-09T17:30:00+08:00', '2026-11-09T18:00:00+08:00'),
    ('2026-11-09T21:30:00+08:00', '2026-11-09T22:00:00+08:00'),
    ('2026-11-09T23:30:00+08:00', '2026-11-09T22:00:00+08:00'),
    ('2026-11-10T00:10:00+08:00', '2026-11-09T22:00:00+08:00'),
])
def test_preparation_starts_half_an_hour_before_display(stamp, expected):
    from cyrene.plugins.builtin.cyrene_cards.home_freshness import preparation_target
    assert preparation_target(datetime.fromisoformat(stamp)).isoformat() == expected


@pytest.mark.asyncio
async def test_scheduled_copy_refresh_without_evidence_or_weather_change(home_rig):
    r = home_rig
    body = {'timezone': 'Asia/Shanghai'}
    r.clock[0] = datetime.fromisoformat('2026-11-09T11:29:00+08:00').timestamp()
    await add_overview(r)
    await r.home.refresh('assistant', body)
    count = len(r.calls)
    r.clock[0] += 60
    result = await r.home.refresh('assistant', body)
    assert result['status'] == 'updated'
    assert len(result['reasons']) == 1 and 'display period due' in result['reasons'][0]
    assert len(r.calls) == count + 1
    assert 'Target display time: 2026-11-09T12:00:00+08:00' in r.calls[-1][4]
    # A restart and the actual display boundary must not duplicate preparation.
    r.home = HomeCards(r.service, lambda: r.port, r.prepare)
    assert (await r.home.refresh('assistant', body))['status'] == 'current'
    r.clock[0] += 30 * 60
    assert (await r.home.refresh('assistant', body))['status'] == 'current'


@pytest.mark.asyncio
async def test_scheduled_failure_retries_and_missed_periods_are_not_replayed(home_rig):
    r = home_rig
    body = {'timezone': 'Asia/Shanghai'}
    r.clock[0] = datetime.fromisoformat('2026-11-09T11:29:00+08:00').timestamp()
    await add_overview(r)
    await r.home.refresh('assistant', body)
    r.clock[0] += 60
    r.checkpoint['status'] = 'failed'
    assert (await r.home.refresh('assistant', body))['status'] == 'retry_pending'
    r.clock[0] += 120
    r.checkpoint['status'] = 'completed'
    assert (await r.home.refresh('assistant', body))['status'] == 'updated'
    assert 'Target display time: 2026-11-09T12:00:00+08:00' in r.calls[-1][4]
    count = len(r.calls)
    r.clock[0] = datetime.fromisoformat('2026-11-10T19:00:00+08:00').timestamp()
    assert (await r.home.refresh('assistant', body))['status'] == 'updated'
    assert len(r.calls) == count + 1
    assert 'Target display time: 2026-11-10T19:00:00+08:00' in r.calls[-1][4]


@pytest.mark.asyncio
async def test_early_start_does_not_claim_morning_display_when_clock_reaches_eight(home_rig):
    r = home_rig
    body = {'timezone': 'Asia/Shanghai', 'client_id': 'morning-window'}
    await add_overview(r)
    for stamp in ('2026-11-09T00:00:00+08:00', '2026-11-09T07:59:59+08:00'):
        r.clock[0] = datetime.fromisoformat(stamp).timestamp()
        assert (await r.home.claim_display('assistant', body))['slot'] is None
    with r.service.store.connect() as db:
        assert db.execute('SELECT * FROM home_display_receipts').fetchone() is None
        assert db.execute('SELECT timezone FROM home_preferences').fetchone()[0] == 'Asia/Shanghai'
    r.clock[0] += 1
    assert (await r.home.claim_display('assistant', body))['slot'] is None
    r.home = HomeCards(r.service, lambda: r.port, r.prepare)
    body['client_id'] = 'reopened'
    assert (await r.home.claim_display('assistant', body))['slot'] is None
    r.clock[0] = datetime.fromisoformat('2026-11-10T07:59:59+08:00').timestamp()
    assert (await r.home.claim_display('assistant', body))['slot'] is None
    r.clock[0] += 1
    assert (await r.home.claim_display('assistant', body))['slot'] is None

    r.clock[0] += 4 * 3600
    assert (await r.home.claim_display('assistant', body))['slot'] == '2026-11-10:12'


@pytest.mark.asyncio
async def test_morning_preparation_uses_timezone_saved_before_eight(home_rig):
    r = home_rig
    r.clock[0] = datetime.fromisoformat('2026-11-09T07:29:00+08:00').timestamp()
    await add_overview(r)
    await r.home.claim_display('assistant', {'timezone': 'Asia/Shanghai', 'client_id': 'early'})
    await r.home.refresh('assistant', {})
    r.clock[0] += 60
    result = await r.home.refresh('assistant', {})
    assert result['status'] == 'updated'
    assert len(result['reasons']) == 1 and 'display period due' in result['reasons'][0]
    assert 'Target display time: 2026-11-09T08:00:00+08:00' in r.calls[-1][4]


@pytest.mark.asyncio
async def test_prepared_copy_waits_for_display_time_and_survives_restart(home_rig, tmp_path):
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeDraft
    r = home_rig
    body = {'timezone': 'Asia/Shanghai'}
    r.clock[0] = datetime.fromisoformat('2026-11-09T11:29:00+08:00').timestamp()
    await add_overview(r)
    await r.home.refresh('assistant', body)
    before = (await r.home.saved('assistant'))['cards'][0]
    prepared = []

    async def prepare(service, port, owner, version, prompt, snapshot, *, publish_at):
        draft = HomeDraft(service, owner, tmp_path / f'draft-{len(prepared)}')
        await draft.command(owner, None, 'card.update', {
            'card_id': before['card_id'], 'expected_revision': before['revision'],
            'changes': {'title': '中午的新文案'}}, effect='scheduled')
        draft.publish(publish_at=publish_at, version=version)
        prepared.append(publish_at)

    r.home.prepare = prepare
    r.clock[0] += 60
    assert (await r.home.refresh('assistant', body))['status'] == 'updated'
    assert (await r.home.saved('assistant'))['cards'][0]['spec']['title'] == before['spec']['title']
    r.home = HomeCards(r.service, lambda: r.port, prepare)
    r.clock[0] += 30 * 60 - 1
    assert (await r.home.saved('assistant'))['cards'][0]['spec']['title'] == before['spec']['title']
    r.clock[0] += 1
    assert (await r.home.saved('assistant'))['cards'][0]['spec']['title'] == '中午的新文案'
    assert (await r.home.refresh('assistant', body))['status'] == 'current'
    assert len(prepared) == 1
    assert (await r.home.saved('assistant'))['cards'][0]['revision'] == before['revision'] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['foreground', 'cleared'])
async def test_due_draft_does_not_overwrite_newer_home_or_cleared_chat(home_rig, tmp_path, change):
    import json
    from cyrene.plugins.builtin.cyrene_cards.home_preparation import HomeDraft
    r = home_rig
    await add_overview(r)
    await r.home.refresh('assistant', {'timezone': 'Asia/Shanghai'})
    before = (await r.home.saved('assistant'))['cards'][0]
    draft = HomeDraft(r.service, 'assistant', tmp_path / 'private')
    args = {'card_id': before['card_id'], 'expected_revision': before['revision'],
            'changes': {'title': 'Future'}}
    await draft.command('assistant', None, 'card.update', args, effect='scheduled')
    draft.publish(publish_at=r.clock[0] + 1800, version=json.dumps(['birth', 0]))
    if change == 'foreground':
        await r.service.command('assistant', None, 'card.update', {
            **args, 'changes': {'title': 'User edit'}}, effect='foreground')
    else:
        r.chat['messageGeneration'] = 1
    r.clock[0] += 1800
    title = (await r.home.saved('assistant'))['cards'][0]['spec']['title']
    assert title == ('User edit' if change == 'foreground' else before['spec']['title'])
    with r.service.store.connect() as db:
        assert db.execute('SELECT 1 FROM home_prepared').fetchone() is None
        state = json.loads(db.execute('SELECT document FROM home_maintenance').fetchone()[0])
        assert 'prepared_slot' not in state


@pytest.mark.asyncio
@pytest.mark.parametrize(('started', 'checked', 'eligible'), [
    ('07:59:00', '08:01:00', False),
    ('08:00:00', '08:00:00', True),
    ('10:59:00', '10:59:00', True),
    ('11:00:00', '11:00:00', False),
    ('10:59:00', '11:00:00', False),
])
async def test_first_start_window_uses_startup_time_not_poll_time(home_rig, started, checked, eligible):
    r = home_rig

    def stamp(value):
        return datetime.fromisoformat(f'2026-11-09T{value}+08:00').timestamp()

    r.clock[0] = stamp(checked)
    await add_overview(r)
    result = await r.home.claim_display('assistant', {
        'timezone': 'Asia/Shanghai', 'client_id': 'first', 'started_at': stamp(started)})
    assert bool(result['slot']) is eligible
    # A later restart never becomes that day's first startup.
    result = await r.home.claim_display('assistant', {
        'timezone': 'Asia/Shanghai', 'client_id': 'second', 'started_at': r.clock[0]})
    assert result['slot'] is None
