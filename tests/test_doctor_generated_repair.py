from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.platform.doctor.agent_repair import RepairWorkspace, generate_repair
from cyrene.platform.doctor.repair_executor import capabilities, run_probe
from cyrene.platform.doctor.repair_service import plan_hash
from cyrene.platform.doctor.repair_workspace import (apply_changes, encode_snapshot, manifest, repair_lock, snapshot)
from cyrene.platform.doctor.service import DoctorService


@pytest.fixture
def doctor(tmp_path):
    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    (plugins / 'custom.py').write_text('def value():\n    return "wrong"\n')
    host = SimpleNamespace(plugin_directory=plugins, registry=SimpleNamespace(list_packs=lambda: [], list_plugins=lambda: []), load_failures=[], startup_failures={}, model_gateway=None,
                           service=lambda _: None, _stop_pack=AsyncMock(), reload_user_plugins=AsyncMock())
    return DoctorService(data=tmp_path / 'data', database=tmp_path / 'store' / 'runtime.db', plugins=plugins, host=host)


async def prepared(doctor, *, syntax=False):
    if syntax:
        (doctor.plugins / 'custom.py').write_text('def broken(:\n')
    files = snapshot(doctor.plugins, 'custom.py')
    workspace = RepairWorkspace(files)
    await workspace.call('stage_changes', {'changes': [{'path': 'custom.py', 'before': files['custom.py'].decode(), 'after': 'def value():\n    return "correct"\n'}]})
    await workspace.call('verify_candidate', {})
    await workspace.call('submit_repair', {'summary': 'Fix the return contract'})
    report = await doctor.diagnose()
    plan = {'id': 'repair_test', 'status': 'planned', 'action': {'kind': 'patch_plugin', 'target': 'custom.py'},
            'report_id': report['id'], 'scope': {}, 'created_at': '2026-09-08', 'original': encode_snapshot(files),
            'source_manifest': manifest(files), **workspace.result}
    plan['plan_hash'] = plan_hash(plan)
    doctor.repairs.save(plan)
    return plan


@pytest.mark.asyncio
async def test_third_party_exact_patch_review_apply_and_rollback(doctor):
    plan = await prepared(doctor, syntax=True)
    public = doctor.public_plan(doctor.get(plan['id']))
    assert '+    return "correct"' in public['diff']
    assert not {'original', 'changes', 'source_manifest'} & public.keys()
    with pytest.raises(ValueError, match='Review'):
        await doctor.apply_repair(plan['id'])
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['status'] == 'applied'
    assert result['outcome']['status'] == 'verified'
    assert result['outcome']['basis'] == 'syntax'
    assert 'correct' in (doctor.plugins / 'custom.py').read_text()
    # Lost-response retries never reload or execute twice.
    await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert doctor.host.reload_user_plugins.await_count == 1
    result = await doctor.rollback_repair(plan['id'])
    assert result['status'] == 'rolled_back'
    assert (doctor.plugins / 'custom.py').read_text() == 'def broken(:\n'


@pytest.mark.asyncio
async def test_static_success_does_not_claim_runtime_recovery(doctor):
    plan = await prepared(doctor)
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['outcome']['status'] == 'unverified'


@pytest.mark.asyncio
async def test_stale_and_tampered_plans_do_not_stop_plugin(doctor):
    plan = await prepared(doctor)
    (doctor.plugins / 'custom.py').write_text('user = "new edit"\n')
    with pytest.raises(ValueError, match='changed after planning'):
        await doctor.apply_repair(plan['id'], plan['plan_hash'])
    doctor.host._stop_pack.assert_not_awaited()
    plan['changes'][0]['after'] = 'tampered = True\n'
    doctor.repairs.save(plan)
    with pytest.raises(ValueError, match='Review'):
        await doctor.apply_repair(plan['id'], plan['plan_hash'])


@pytest.mark.asyncio
async def test_failed_reload_restores_original_and_keeps_evidence(doctor):
    plan = await prepared(doctor)
    doctor.host.reload_user_plugins.side_effect = [RuntimeError('load failed'), None]
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['status'] == 'rolled_back'
    assert result['error']['message'] == 'load failed'
    assert (doctor.plugins / 'custom.py').read_text() == plan['changes'][0]['before']


@pytest.mark.asyncio
async def test_recovery_never_overwrites_concurrent_edit(doctor):
    plan = await prepared(doctor)
    async def reload(*, seed=False):
        (doctor.plugins / 'custom.py').write_text('user = "edited during reload"\n')
    doctor.host.reload_user_plugins.side_effect = reload
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['status'] == 'failed'
    assert result['outcome']['status'] == 'partial'
    with pytest.raises(ValueError, match='changed after repair'):
        await doctor.rollback_repair(plan['id'])
    assert 'edited during reload' in (doctor.plugins / 'custom.py').read_text()


@pytest.mark.asyncio
async def test_interrupted_commit_can_restore_reviewed_partial_bytes(doctor):
    plan = await prepared(doctor)
    plan.update(status='applying', commit_started=True)
    doctor.repairs.save(plan)
    (doctor.plugins / 'custom.py').write_text(plan['changes'][0]['after'])
    assert doctor.get(plan['id'])['status'] == 'interrupted'
    result = await doctor.rollback_repair(plan['id'])
    assert result['status'] == 'rolled_back'
    assert (doctor.plugins / 'custom.py').read_text() == plan['changes'][0]['before']


@pytest.mark.asyncio
async def test_disconnected_request_does_not_interrupt_commit(doctor):
    plan = await prepared(doctor)
    entered, release = asyncio.Event(), asyncio.Event()
    async def reload(*, seed=False):
        entered.set()
        await release.wait()
    doctor.host.reload_user_plugins.side_effect = reload
    request = asyncio.create_task(doctor.apply_repair(plan['id'], plan['plan_hash']))
    await asyncio.wait_for(entered.wait(), 3)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    release.set()
    await asyncio.gather(*list(doctor.repairs.commits))
    assert doctor.get(plan['id'])['status'] == 'applied'


@pytest.mark.asyncio
async def test_busy_runtime_rejects_commit(doctor):
    from cyrene.platform.run_coordinator import run_coordinator_for
    plan = await prepared(doctor)
    with run_coordinator_for(str(doctor.database)).maintenance():
        with pytest.raises(RuntimeError, match='Active runs'):
            await doctor.apply_repair(plan['id'], plan['plan_hash'])
    doctor.host._stop_pack.assert_not_awaited()


def test_path_scope_symlinks_and_cross_process_lock(doctor, tmp_path):
    with pytest.raises(ValueError):
        snapshot(doctor.plugins, '../outside')
    (doctor.plugins / 'link.py').symlink_to(doctor.plugins / 'custom.py')
    with pytest.raises(ValueError, match='symbolic'):
        snapshot(doctor.plugins, 'link.py')
    files = snapshot(doctor.plugins, 'custom.py')
    with pytest.raises(ValueError):
        apply_changes(files, [{'path': '../outside.py', 'before': '', 'after': 'x'}])
    with repair_lock(tmp_path):
        with pytest.raises(ValueError, match='Another Doctor'):
            with repair_lock(tmp_path):
                pass


@pytest.mark.asyncio
async def test_reproduction_freezes_before_edits_and_failed_candidate_cannot_submit(monkeypatch):
    monkeypatch.setattr('cyrene.platform.doctor.agent_repair.run_probe', AsyncMock(return_value={'status': 'failed'}))
    workspace = RepairWorkspace({'p.py': b'x = 1\n'})
    await workspace.call('set_reproduction', {'python': 'assert False'})
    with pytest.raises(ValueError, match='frozen'):
        await workspace.call('set_reproduction', {'python': 'assert True'})
    await workspace.call('stage_changes', {'changes': [{'path': 'p.py', 'before': 'x = 1\n', 'after': 'x = 2\n'}]})
    await workspace.call('verify_candidate', {})
    with pytest.raises(ValueError, match='still fails'):
        await workspace.call('submit_repair', {'summary': 'Pretend it passed'})


@pytest.mark.asyncio
async def test_real_agent_investigates_stages_verifies_and_submits_without_host_tools(doctor, tmp_path):
    files = snapshot(doctor.plugins, 'custom.py')
    workspace = RepairWorkspace(files)
    actions = iter([
        ('read_plugin_file', {'path': 'custom.py'}),
        ('stage_changes', {'changes': [{'path': 'custom.py', 'before': files['custom.py'].decode(), 'after': 'def value():\n    return "correct"\n'}]}),
        ('verify_candidate', {}), ('submit_repair', {'summary': 'Fix return type'})])
    owner = asyncio.get_running_loop()
    class Gateway:
        async def complete(self, messages, **kwargs):
            assert asyncio.get_running_loop() is owner
            names = {tool['function']['name'] for tool in kwargs['tools']}
            assert 'stage_changes' in names
            assert not names & {'Bash', 'Write', 'Read', 'apply_approved_plan'}
            name, arguments = next(actions)
            return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': name, 'name': name, 'arguments': arguments}]}
    report = await doctor.diagnose()
    result = await asyncio.wait_for(generate_repair(report, Gateway(), tmp_path / 'agent', workspace), 15)
    assert result['verification']['syntax']['status'] == 'passed'
    assert result['changes'][0]['after'].endswith('"correct"\n')
    assert snapshot(doctor.plugins, 'custom.py') == files


@pytest.mark.asyncio
async def test_generation_persists_and_cancel_releases_budget(doctor, monkeypatch):
    entered = asyncio.Event()
    async def wait(*args):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr('cyrene.platform.doctor.repair_service.generate_repair', wait)
    report = await doctor.diagnose()
    plan = await doctor.repairs.start(report['id'], 'custom.py', 'Wrong return type')
    await entered.wait()
    with pytest.raises(ValueError, match='current Doctor'):
        await doctor.repairs.start(report['id'], 'custom.py', '')
    result = await doctor.repairs.cancel(plan['id'])
    assert result['status'] == 'cancelled'
    assert not doctor.tasks
    assert (await doctor.diagnose())['repair_sessions'][0]['id'] == plan['id']
    assert doctor.repairs.repository.retain is None


@pytest.mark.asyncio
async def test_real_sandbox_runs_unknown_contract_repair_and_blocks_host_access(tmp_path, monkeypatch):
    if capabilities()['mode'] != 'isolated_python':
        pytest.skip('macOS sandbox-exec is unavailable')
    files = {'custom.py': b'def result():\n    return "7"\n'}
    probe = 'from custom import result\nassert isinstance(result(), int), "return must be integer"'
    before = await run_probe(files, probe)
    assert before['status'] == 'failed', before
    after = await run_probe({'custom.py': b'def result():\n    return 7\n'}, probe)
    assert after['status'] == 'passed', after
    sentinel = tmp_path / 'private.txt'
    sentinel.write_text('private host data')
    monkeypatch.setenv('DOCTOR_TEST_SECRET', 'must-not-inherit')
    guard = f'''import os, socket
from pathlib import Path
assert 'DOCTOR_TEST_SECRET' not in os.environ
try:
    Path({str(sentinel)!r}).read_text()
except PermissionError:
    pass
else:
    raise AssertionError('host read escaped')
try:
    Path('custom.py').write_text('tampered')
except PermissionError:
    pass
else:
    raise AssertionError('source write escaped')
try:
    socket.socket().connect(('127.0.0.1', 9))
except PermissionError:
    pass
else:
    raise AssertionError('network escaped')
'''
    guarded = await run_probe(files, guard)
    assert guarded['status'] == 'passed', guarded
    assert sentinel.read_text() == 'private host data'


@pytest.mark.asyncio
async def test_unavailable_executor_never_runs_probe_on_host(monkeypatch, tmp_path):
    monkeypatch.setattr('cyrene.platform.doctor.repair_executor.capabilities', lambda: {'mode': 'static_only'})
    path = tmp_path / 'escaped'
    result = await run_probe({'p.py': b''}, f'open({str(path)!r}, "w").write("bad")')
    assert result['status'] == 'unavailable'
    assert not path.exists()


@pytest.mark.asyncio
async def test_http_generation_review_and_commit_contract(doctor, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from httpx import ASGITransport, AsyncClient
    from cyrene.workbench.http.system.doctor import register_doctor_routes
    async def generate(report, gateway, directory, workspace):
        raw = workspace.original['custom.py'].decode()
        await workspace.call('stage_changes', {'changes': [{'path': 'custom.py', 'before': raw, 'after': raw.replace('wrong', 'correct')}]})
        await workspace.call('verify_candidate', {})
        await workspace.call('submit_repair', {'summary': 'Fix plugin return value'})
        return workspace.result
    monkeypatch.setattr('cyrene.platform.doctor.repair_service.generate_repair', generate)
    app, router = FastAPI(), APIRouter()
    register_doctor_routes(router, doctor)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://doctor.test') as client:
        report = (await client.post('/api/doctor/reports', json={})).json()
        base = '/api/doctor/reports/' + report['id'] + '/generate-repair'
        assert (await client.post(base, json={'target': '../outside'})).status_code == 409
        response = await client.post(base, json={'target': 'custom.py', 'description': 'Wrong return'})
        assert response.status_code == 200
        identifier = response.json()['id']
        await asyncio.gather(*list(doctor.tasks.values()))
        reviewed = (await client.get('/api/doctor/reports/' + identifier)).json()
        assert reviewed['status'] == 'planned'
        assert 'original' not in reviewed
        assert (await client.post('/api/doctor/repairs/' + identifier + '/apply')).status_code == 409
        response = await client.post('/api/doctor/repairs/' + identifier + '/apply', json={'expected_plan_hash': reviewed['plan_hash']})
        assert response.status_code == 200
        assert response.json()['outcome']['status'] == 'unverified'


@pytest.mark.asyncio
async def test_offline_inspection_does_not_rewrite_interrupted_plans(doctor):
    plan = await prepared(doctor)
    plan['status'] = 'generating'
    doctor.repairs.save(plan)
    path = doctor.repairs.repository.directory / (plan['id'] + '.json')
    before = path.read_bytes()
    await doctor.diagnose(persist=False)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_edit_during_verification_is_not_reported_as_repaired(doctor, monkeypatch):
    plan = await prepared(doctor)
    plan['probe'] = 'assert True'
    plan['baseline']['probe'] = {'status': 'failed'}
    plan['plan_hash'] = plan_hash(plan)
    doctor.repairs.save(plan)
    async def probe(*args):
        (doctor.plugins / 'custom.py').write_text('concurrent = True\n')
        return {'status': 'passed'}
    monkeypatch.setattr('cyrene.platform.doctor.repair_service.run_probe', probe)
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['outcome']['status'] == 'partial'
    assert (doctor.plugins / 'custom.py').read_text() == 'concurrent = True\n'


@pytest.mark.asyncio
async def test_source_read_and_review_diff_are_not_silently_truncated():
    before = '# explanation\n' * 500 + 'x = 1\n'
    after = '# replacement\n' * 500 + 'x = 2\n'
    workspace = RepairWorkspace({'custom.py': before.encode()})
    assert (await workspace.call('read_plugin_file', {'path': 'custom.py'}))['content'] == before
    from cyrene.platform.doctor.repair_service import RepairService
    plan = {'id': 'repair_long', 'status': 'planned', 'changes': [{'path': 'custom.py', 'before': before, 'after': after}]}
    assert '+x = 2' in RepairService.public(plan)['diff']


@pytest.mark.asyncio
async def test_source_identity_restart_and_post_restart_verification(doctor):
    plan = await prepared(doctor, syntax=True)
    pack = SimpleNamespace(id='different.id', plugins=(), has_application_contributions=False)
    doctor.host.registry = SimpleNamespace(list_packs=lambda: [pack],
        pack_source=lambda _: str(doctor.plugins / 'custom.py'), pack_enabled=lambda _: True,
        pack_configured_enabled=lambda _: True, plugin_enabled=lambda _: True)
    doctor.host.pack_running = lambda _: False
    doctor.host.pack_operational = lambda _: True
    doctor.host.restart_required_packs = ('different.id',)
    applied = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    doctor.host._stop_pack.assert_awaited_once_with('different.id')
    assert applied['outcome']['status'] == 'restart_required'
    result = await doctor.repairs.verify_applied(plan['id'])
    assert result['outcome']['status'] == 'restart_required'
    doctor.host.restart_required_packs = ()
    result = await doctor.repairs.verify_applied(plan['id'])
    assert result['outcome']['status'] == 'verified'
    assert doctor.host.reload_user_plugins.await_count == 1


@pytest.mark.asyncio
async def test_setup_failure_rolls_back_and_json_repair_is_supported(doctor):
    plan = await prepared(doctor, syntax=True)
    doctor.host.registry = SimpleNamespace(list_packs=lambda: [SimpleNamespace(id='different.id')],
        pack_source=lambda _: str(doctor.plugins / 'custom.py'))
    doctor.host.setup_failures = {'different.id': 'initialization failed'}
    result = await doctor.apply_repair(plan['id'], plan['plan_hash'])
    assert result['status'] == 'rolled_back'
    assert (doctor.plugins / 'custom.py').read_text() == 'def broken(:\n'
    workspace = RepairWorkspace({'example/settings.json': b'{invalid'})
    await workspace.call('stage_changes', {'changes': [{'path': 'example/settings.json', 'before': '{invalid', 'after': '{"enabled": true}'}]})
    await workspace.call('verify_candidate', {})
    assert workspace.baseline['syntax']['status'] == 'failed'
    assert workspace.verification['syntax']['status'] == 'passed'
