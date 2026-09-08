from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.platform.doctor.failure_workflow import failed_run_scope, implicated_targets
from cyrene.platform.doctor.service import DoctorService
from cyrene.platform.doctor.repository import ReportRepository


@pytest.fixture
def doctor(tmp_path):
    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    (plugins / 'custom.py').write_text('value = 1\n')
    host = SimpleNamespace(load_failures=[], startup_failures={}, model_gateway=None)
    return DoctorService(data=tmp_path / 'data', database=tmp_path / 'store/runtime.db', plugins=plugins,
                         host=host, analyzer=AsyncMock(return_value={'summary': 'Diagnosis'}))


def chat(doctor, nodes):
    doctor.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(doctor.database) as db:
        db.execute('CREATE TABLE workbench_chats (chat_id TEXT, payload_json TEXT)')
        db.execute('INSERT INTO workbench_chats VALUES (?, ?)', ('chat_1', json.dumps({'projectId': 'project_1',
            'lastRun': {'id': 'run_1', 'status': 'done', 'outcome': 'error', 'terminationReason': 'agent_error'}})))
    key = hashlib.sha256(b'chat_1').hexdigest()
    path = doctor.database.parent / 'agent-state/context/trees' / key[:2] / (key + '.sqlite3')
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE context_nodes (node_id TEXT, value_json TEXT, created_at INTEGER)')
        for index, value in enumerate(nodes):
            db.execute('INSERT INTO context_nodes VALUES (?, ?, ?)', (str(index), json.dumps({'run_id': 'run_1', **value}), index))


async def finish(doctor, scope):
    report = await doctor.diagnose_failure(scope)
    await doctor.failure_tasks[report['id']]
    return doctor.get(report['id'])


def incident(doctor, code='plugin_error'):
    ReportRepository(doctor.data / 'doctor/incidents').save({'id': 'incident_1', 'chat_id': 'chat_1', 'run_id': 'run_1',
        'code': code, 'frames': [{'plugin': 'custom.py', 'module': 'custom.py', 'line': 1}]})


def test_legacy_terminal_error_binds_run_and_respects_explicit_scope(doctor):
    chat(doctor, [])
    assert failed_run_scope(doctor.database, {'chat_id': 'chat_1'})['run_id'] == 'run_1'
    explicit = {'chat_id': 'chat_1', 'run_id': 'run_old'}
    assert failed_run_scope(doctor.database, explicit) == explicit
    with pytest.raises(ValueError, match='project'):
        failed_run_scope(doctor.database, {'chat_id': 'chat_1', 'project_id': 'other'})


@pytest.mark.asyncio
async def test_installed_chat_pattern_retains_pending_permission_without_replay(doctor):
    chat(doctor, [
        {'role': 'tool_results', 'results': [{'name': 'Write', 'success': False, 'failure': {'error_code': 'plugin_review_rejected', 'retry_scope': 'never'}}]},
        {'role': 'tool_results', 'results': [{'name': 'Bash', 'success': True, 'value': 'PRIVATE OUTPUT'}],
         'pending_question': {'status': 'awaiting_user', 'id': 'permission_1', 'kind': 'destructive_confirmation', 'text': 'PRIVATE COMMAND'}},
        {'run_id': 'other_run', 'role': 'assistant', 'failure_kind': 'model_timeout'},
    ])
    doctor.repairs.start = AsyncMock()
    report = await finish(doctor, {'chat_id': 'chat_1'})
    assert report['scope']['run_id'] == 'run_1'
    assert report['failure']['reason'] == 'pending_question_requires_answer'
    assert 'PRIVATE' not in json.dumps(report)
    doctor.repairs.start.assert_not_awaited()
    doctor.analyzer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('verified', [True, False])
async def test_targeted_candidate_only_auto_applies_after_recovery_check(doctor, verified):
    incident(doctor)
    plan = {'id': 'repair_fake', 'status': 'planned', 'action': {'kind': 'patch_plugin'}, 'plan_hash': 'a' * 64,
            'baseline': {'syntax': {'status': 'passed'}, 'probe': {'status': 'failed'}},
            'verification': {'syntax': {'status': 'passed'}, 'probe': {'status': 'passed' if verified else 'static_only'}}}
    doctor.repairs.save(plan)
    doctor.repairs.start = AsyncMock(return_value=plan)
    doctor.apply_repair = AsyncMock(return_value={'outcome': {'status': 'verified'}})
    report = await finish(doctor, {'chat_id': 'chat_1', 'run_id': 'run_1'})
    assert doctor.repairs.start.await_args.args[1] == 'custom.py'
    assert report['failure']['status'] == ('completed' if verified else 'needs_review')
    if verified:
        doctor.apply_repair.assert_awaited_once_with('repair_fake', 'a' * 64)
    else:
        doctor.apply_repair.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_terminal_evidence_takes_precedence_over_plugin_frame(doctor):
    incident(doctor, 'model_timeout')
    doctor.probe_model = AsyncMock(return_value={'model_probe': {'status': 'passed'}})
    doctor.repairs.start = AsyncMock()
    report = await finish(doctor, {'incident_id': 'incident_1'})
    assert report['failure']['reason'] == 'model_probe_passed'
    doctor.repairs.start.assert_not_awaited()


def test_historical_failures_do_not_choose_write_target(doctor):
    plugin = SimpleNamespace(name='Custom', handler=None)
    doctor.host.registry = SimpleNamespace(list_plugins=lambda: [SimpleNamespace(plugin=plugin, source=str(doctor.plugins / 'custom.py'))])
    def targets(terminal, code='broken', retry_scope='model'):
        return implicated_targets({'findings': [{'code': 'run_tool_failed', 'evidence': {'tool': 'Custom',
            'terminal_batch': terminal, 'code': code, 'retry_scope': retry_scope}}]}, doctor.host, doctor.plugins)
    assert targets(False) == []
    assert targets(True, 'plugin_review_rejected', 'never') == []
    assert targets(True) == ['custom.py']


@pytest.mark.asyncio
async def test_reopen_deduplicates_and_immediate_cancel_does_not_orphan(doctor):
    async def analyze(_):
        await asyncio.sleep(60)
    doctor.analyzer = analyze
    report = await doctor.diagnose_failure({'chat_id': 'chat_1', 'run_id': 'run_1'})
    attached = await doctor.diagnose_failure({'chat_id': 'chat_1', 'run_id': 'run_1'})
    assert attached['id'] == report['id']
    result = await doctor.cancel_failure(report['id'])
    assert result['failure']['status'] == 'cancelled'
    assert not doctor.failure_tasks and not doctor.tasks
