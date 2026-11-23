"""Malformed wire batches recover through the provider, router and session."""
import asyncio
import json

import pytest

from cyrene.core.plugin import Plugin
from cyrene.plugins import ensure_model_router
from cyrene.plugins import model_router
from cyrene.plugins.builtin.cyrene_model import _shared
from cyrene.plugins.builtin.cyrene_model.deepseek import DEEPSEEK_PROVIDER
from test_task_contexts import make_session


@pytest.mark.parametrize('persistent', [False, True])
def test_malformed_name_retries_entire_batch_without_fallback(tmp_path, monkeypatch, persistent):
    requests, executions, providers = [], [], []
    bad_name = 'cyrene_fn_243ec6bc1ea637b812e71044136c663d875445aa2ddf1a0f\n</parameter'

    async def wire(arguments, context, provider):
        requests.append(arguments)
        providers.append(context.data['model_candidate']['model'])
        attempt = len(requests)
        if attempt > 1:
            correction = json.loads(arguments['messages'][-1]['content']) if not executions else None
            if correction:
                assert correction['previous_attempt']['tool_calls_executed'] is False
                assert correction['previous_attempt']['tool_calls'][0]['name_validation'] == 'invalid_format'
                assert 'XML' in str(correction['required_action'])
                assert bad_name not in str(correction)
        if persistent or attempt == 1:
            assert executions == []
        elif attempt == 2:
            assert executions == []
        else:
            return {'content': 'done', 'tool_calls': []}
        return {'content': '', 'tool_calls': [
            {'id': 'probe', 'function': {'name': 'Probe', 'arguments': '{}'}},
            {'id': 'cards', 'function': {
                'name': bad_name if persistent or attempt == 1 else bad_name.split('\n')[0],
                'arguments': '{}',
            }},
        ]}

    async def provider(arguments, context):
        return await _shared.complete_model(arguments, context, DEEPSEEK_PROVIDER)

    def tool(name):
        def execute(arguments, context):
            executions.append(name)
            return {'ok': True}
        return Plugin(name, 'test tool', {'type': 'object'}, execute,
                      metadata={'read_only': True})

    provider_plugin = Plugin('WireProvider', 'test provider', {'type': 'object'}, provider, kind='model')
    candidates = [{'id': name, 'model': name, 'provider': 'openai_compatible'}
                  for name in ('primary', 'fallback')]
    monkeypatch.setattr(_shared, '_complete_model_wire', wire)
    monkeypatch.setattr(model_router, '_routed_candidates', lambda *a: (candidates, 'test'))
    monkeypatch.setattr(model_router, '_eligible_candidates', lambda candidates, *a, **kw: candidates)
    monkeypatch.setattr(model_router, 'resolve_registered_model_plugin', lambda *a: provider_plugin)
    monkeypatch.setattr(model_router, 'remember_model_success', lambda *a, **kw: None)
    async def ignore(*a, **kw):
        pass
    monkeypatch.setattr(model_router, '_persist_fallback_result', ignore)
    session = make_session(tmp_path, model_router.route_model_call,
                           tool_plugins=(provider_plugin, tool('Probe'), tool('card.list')),
                           extra_direct_tool_names=('Probe', 'card.list'))
    ensure_model_router(session.registry)
    try:
        session.submit('List cards and run the probe', run_id='incident')
        asyncio.run(session.drain())
        assert set(providers) == {'primary'}
        output = session.final_output('incident')
        if persistent:
            assert len(requests) == 4
            assert executions == []
            assert output['error'] is True
        else:
            assert len(requests) == 3
            assert sorted(executions) == ['Probe', 'card.list']
            assert output['content'] == 'done'
    finally:
        session.close()
