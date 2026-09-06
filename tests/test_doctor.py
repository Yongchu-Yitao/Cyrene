from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from cyrene.platform.doctor.repository import record_incident
from cyrene.platform.doctor.service import DoctorService


@pytest.fixture
def setup(tmp_path):
    data, plugins, db = tmp_path / "data", tmp_path / "plugins", tmp_path / "store/runtime.db"
    data.mkdir()
    plugins.mkdir()
    db.parent.mkdir()
    key = Fernet.generate_key()
    (data / ".config_key").write_bytes(key)
    (data / "config.enc").write_bytes(Fernet(key).encrypt(json.dumps({"settings": {}}).encode()))
    (plugins / "sample.py").write_text("x = 1")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE workbench_state(key TEXT PRIMARY KEY, payload_json TEXT)")
        conn.execute("CREATE TABLE workbench_chats(chat_id TEXT PRIMARY KEY, payload_json TEXT)")
        conn.execute("INSERT INTO workbench_chats VALUES (?, ?)", ("chat", json.dumps({"id": "chat", "projectId": "project", "projectMemorySnapshot": {"hash": "old", "modifiedAt": "yesterday"}})))
        document = {"schemaVersion": 1, "current": {"hash": "new", "modifiedAt": "today"}, "jobs": []}
        conn.execute("INSERT INTO workbench_state VALUES (?, ?)", ("project_memory_prompt:project", json.dumps(document)))
    return SimpleNamespace(data=data, plugins=plugins, db=db)


def service(setup, **kwargs):
    return DoctorService(data=setup.data, database=setup.db, plugins=setup.plugins, **kwargs)


def codes(report):
    return {item["code"]: item for item in report["findings"]}


@pytest.mark.asyncio
async def test_offline_report_is_readonly_and_explains_old_snapshot(setup):
    before = {p: p.read_bytes() for root in (setup.data, setup.plugins, setup.db.parent) for p in root.rglob("*") if p.is_file()}
    report = await service(setup).diagnose({"chat_id": "chat"}, persist=False)
    assert codes(report)["memory_snapshot_older"]["status"] == "info"
    assert codes(report)["runtime_offline"]["status"] == "unknown"
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert not (setup.data / "doctor").exists()


@pytest.mark.asyncio
async def test_missing_key_preserves_encrypted_config(setup):
    raw = (setup.data / "config.enc").read_bytes()
    (setup.data / ".config_key").unlink()
    report = await service(setup).diagnose(persist=False)
    assert codes(report)["config_key_missing"]["status"] == "failed"
    assert (setup.data / "config.enc").read_bytes() == raw
    assert not (setup.data / ".config_key").exists()


@pytest.mark.asyncio
async def test_syntax_error_does_not_execute_plugin(setup):
    (setup.plugins / "sample.py").write_text("import pathlib\npathlib.Path('DO_NOT_CREATE').touch()\ndef broken(:")
    report = await service(setup).diagnose(persist=False)
    item = codes(report)["plugin_syntax_error"]
    assert item["evidence"]["line"] == 3
    assert item["actions"] == [{"kind": "restore_plugin", "target": "sample.py"}]
    assert not Path("DO_NOT_CREATE").exists()


def test_plugin_scan_accepts_a_symlinked_parent(tmp_path):
    from cyrene.platform.doctor.checks import plugin_checks
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "broken.py").write_text("def broken(:")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    assert plugin_checks(alias)[0]["code"] == "plugin_syntax_error"


@pytest.mark.asyncio
async def test_agent_failure_retains_report_with_direction(setup):
    from cyrene.model.error_details import ModelCallError, classify_model_error
    async def fail(_report):
        raise ModelCallError(classify_model_error("HTTP 401 Unauthorized"))
    doctor = service(setup, analyzer=fail)
    report = await doctor.diagnose()
    await doctor.start_analysis(report["id"])
    await asyncio.gather(*list(doctor.tasks.values()))
    result = doctor.get(report["id"])
    assert result["findings"] == report["findings"]
    assert result["analysis"]["status"] == "unavailable"
    assert result["analysis"]["code"] == "model_authentication_failed"


@pytest.mark.asyncio
async def test_analysis_can_be_cancelled_without_losing_findings(setup):
    started = asyncio.Event()
    async def wait(_report):
        started.set()
        await asyncio.Event().wait()
    doctor = service(setup, analyzer=wait)
    report = await doctor.diagnose()
    await doctor.start_analysis(report["id"])
    await started.wait()
    result = await doctor.cancel_analysis(report["id"])
    assert result["analysis"]["status"] == "cancelled"
    assert result["findings"] == report["findings"]


@pytest.mark.asyncio
async def test_memory_job_failure_and_disabled_are_distinct(setup):
    with sqlite3.connect(setup.db) as conn:
        document = {"schemaVersion": 1, "current": {}, "jobs": [{"id": "j1", "chatId": "chat", "status": "failed", "errorType": "model_authentication_failed", "error": "secret-token"}]}
        conn.execute("UPDATE workbench_state SET payload_json=?", (json.dumps(document),))
        conn.execute("UPDATE workbench_chats SET payload_json=?", (json.dumps({"id": "chat", "projectId": "project", "projectMemoryActive": False}),))
    report = await service(setup).diagnose({"chat_id": "chat"}, persist=False)
    assert codes(report)["memory_disabled"]["status"] == "skipped"
    assert codes(report)["model_authentication_failed"]["actions"][0]["target"] == "j1"
    assert "secret-token" not in json.dumps(report)


@pytest.mark.asyncio
async def test_incident_survives_service_restart_without_raw_exception(setup):
    identifier = record_incident(RuntimeError("Bearer private-secret"), chat_id="chat", project_id="project", directory=setup.data / "doctor/incidents")
    report = await service(setup).diagnose({"chat_id": "chat"})
    assert codes(report)["internal_error"]["evidence"]["id"] == identifier
    assert "private-secret" not in json.dumps(report)
    assert service(setup).get(report["id"])["findings"] == report["findings"]


@pytest.mark.asyncio
async def test_plugin_repair_rechecks_and_rolls_back(setup, monkeypatch):
    from cyrene.plugins import native_tools
    (setup.plugins / "sample.py").write_text("def broken(:")
    monkeypatch.setattr(native_tools, "_collect_canonical_files", lambda: {"sample.py": b"x = 1"})
    host = SimpleNamespace(load_failures=[], startup_failures={}, model_gateway=None,
                           _stop_pack=AsyncMock(), reload_user_plugins=AsyncMock(), service=lambda _: None)
    doctor = service(setup, host=host)
    report = await doctor.diagnose()
    plan = await doctor.plan_repair(report["id"], codes(report)["plugin_syntax_error"]["id"])
    result = await doctor.apply_repair(plan["id"])
    assert result["status"] == "applied"
    assert "plugin_syntax_error" not in codes(doctor.get(result["verification_report_id"]))
    assert (setup.plugins / "sample.py").read_text() == "x = 1"
    rollback = await doctor.rollback_repair(plan["id"])
    assert rollback["status"] == "rolled_back"
    assert (setup.plugins / "sample.py").read_text() == "def broken(:"
    assert codes(doctor.get(rollback["verification_report_id"]))["plugin_syntax_error"]["status"] == "failed"


@pytest.mark.asyncio
async def test_invalid_scope_and_missing_report_are_rejected(setup):
    doctor = service(setup)
    with pytest.raises(ValueError):
        await doctor.diagnose({"chat_id": "../private"})
    with pytest.raises(ValueError):
        doctor.get("../../private")


def test_cli_does_not_import_runtime_or_write_data(tmp_path):
    script = """import sys
from cyrene.platform.doctor.cli import main
main(['--offline', '--json', '--base-dir', sys.argv[1], '--plugin-dir', sys.argv[1] + '/plugins'])
assert 'cyrene.config' not in sys.modules
assert 'cyrene.core.session' not in sys.modules
"""
    root = tmp_path / "missing"
    result = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["online"] is False
    assert not root.exists()


def test_maintenance_blocks_new_runs_and_releases_after_error():
    from cyrene.platform.run_coordinator import RunCoordinator
    coordinator = RunCoordinator()
    with pytest.raises(ValueError):
        with coordinator.maintenance():
            with pytest.raises(RuntimeError, match="maintenance"):
                coordinator.try_acquire("chat", "c", "r")
            raise ValueError("repair failed")
    assert coordinator.try_acquire("chat", "c", "r") is not None
    with pytest.raises(RuntimeError, match="Active"):
        with coordinator.maintenance():
            pass


def test_http_report_and_repair_contracts(setup):
    from fastapi import FastAPI, APIRouter
    from fastapi.testclient import TestClient
    from cyrene.workbench.http.system.doctor import register_doctor_routes
    app, router = FastAPI(), APIRouter()
    register_doctor_routes(router, service(setup))
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/doctor/reports", json={"chat_id": "chat"})
        assert response.status_code == 200
        report = response.json()
        assert client.get("/api/doctor/reports/" + report["id"]).json()["scope"]["project_id"] == "project"
        assert client.post("/api/doctor/reports", json={"chat_id": "../outside"}).status_code == 409
        assert client.post("/api/doctor/reports/" + report["id"] + "/repair-plan", json={"finding_id": "fake"}).status_code == 409
        assert client.get("/api/doctor/reports/missing").status_code == 404


@pytest.mark.asyncio
async def test_report_survives_storage_failure(setup, monkeypatch):
    doctor = service(setup)
    def fail(_value):
        raise OSError("disk full")
    monkeypatch.setattr(doctor.repository, "save", fail)
    result = await doctor.diagnose()
    assert result["persistence_unavailable"] is True
    assert doctor.get(result["id"])["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_first", [False, True])
async def test_isolated_agent_can_use_evidence_but_has_no_work_tools(setup, tmp_path, invalid_first):
    from cyrene.platform.doctor.agent_analysis import analyze
    report = await service(setup).diagnose(persist=False)
    report["user_description"] = "The chat stops halfway despite passing checks."
    tools_seen = []
    calls = 0
    owner_loop = asyncio.get_running_loop()
    class Gateway:
        async def complete(self, messages, **options):
            nonlocal calls
            assert "The chat stops halfway despite passing checks." in json.dumps(messages)
            assert asyncio.get_running_loop() is owner_loop
            calls += 1
            tools_seen.extend(tool["function"]["name"] for tool in options.get("tools") or [])
            if calls == 1 or (invalid_first and calls == 2):
                message = {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "name": "submit_diagnosis", "arguments": {"summary": "基础存储正常，Agent 未验证。", "evidence_ids": [report["findings"][0]["id"]], "next_steps": ["点击数据库写入测试" if invalid_first and calls == 1 else "测试模型连接"]}}]}
            else:
                message = {"role": "assistant", "content": "Diagnosis complete"}
            return message
    result = await asyncio.wait_for(analyze(report, Gateway(), tmp_path / "analysis"), 10)
    assert result["evidence_ids"] == [report["findings"][0]["id"]]
    assert result["next_steps"] == ["测试模型连接"]
    assert "submit_diagnosis" in tools_seen
    assert set(tools_seen) <= {"get_evidence", "submit_diagnosis"}


@pytest.mark.asyncio
async def test_reload_after_repair_does_not_seed_other_plugins(tmp_path, monkeypatch):
    from cyrene.plugins.application import PluginApplicationHost
    def forbidden(_directory):
        raise AssertionError("Repair reload must not seed unrelated files")
    monkeypatch.setattr("cyrene.plugins.application.seed_builtin_plugin_directory", forbidden)
    host = SimpleNamespace(plugin_directory=tmp_path,
        registry=SimpleNamespace(refresh_directory=lambda _: ()),
        _reconcile_attachment_generations=lambda: None, reconcile_activation=AsyncMock())
    result, failures = await PluginApplicationHost.reload_user_plugins(host, seed=False)
    assert result.created == result.updated == ()
    assert failures == ()


@pytest.mark.asyncio
async def test_reset_tool_only_changes_one_override_and_can_rollback(setup, monkeypatch):
    from cyrene.platform import settings_store
    values = {"one": {"name": "custom"}, "two": {"description": "keep"}}
    revision = 3
    monkeypatch.setattr(settings_store, "get", lambda *_: values.copy())
    monkeypatch.setattr(settings_store, "get_revision", lambda: revision)
    def update(patch, *, expected_revision):
        nonlocal revision
        assert expected_revision == revision
        values.clear()
        values.update(patch["plugin_tool_customizations"])
        revision += 1
        return revision, {}
    monkeypatch.setattr(settings_store, "update_atomic", update)
    host = SimpleNamespace(load_failures=[], startup_failures={}, reload_user_plugins=AsyncMock(), service=lambda _: None)
    doctor = service(setup, host=host)
    report = await doctor.diagnose()
    report["findings"].append({"id": "reset", "actions": [{"kind": "reset_tool", "target": "one"}]})
    doctor.repository.save(report)
    plan = await doctor.plan_repair(report["id"], "reset")
    applied = await doctor.apply_repair(plan["id"])
    assert applied["status"] == "applied"
    assert values == {"two": {"description": "keep"}}
    await doctor.rollback_repair(plan["id"])
    assert values == {"one": {"name": "custom"}, "two": {"description": "keep"}}

@pytest.mark.asyncio
async def test_incident_scope_selects_exact_failure(setup):
    first = record_incident(RuntimeError('private first'), directory=setup.data / 'doctor/incidents')
    record_incident(RuntimeError('private second'), directory=setup.data / 'doctor/incidents')
    report = await service(setup).diagnose({'incident_id': first, 'client_code': 'http_500'})
    incidents = [f['evidence']['id'] for f in report['findings'] if f.get('evidence', {}).get('id')]
    assert incidents == [first]
    assert 'private' not in json.dumps(report)
    assert 'http_500' in codes(report)
    missing = await service(setup).diagnose({'incident_id': 'incident_missing'})
    assert codes(missing)['incident_missing']['status'] == 'unknown'


def test_http_failures_link_incidents_without_changing_body(setup):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from cyrene.platform.doctor.http import DoctorIncidentMiddleware
    from cyrene.platform.doctor.repository import ReportRepository
    app = FastAPI()
    app.add_middleware(DoctorIncidentMiddleware, service=service(setup))

    @app.get('/api/fail')
    def fail():
        raise HTTPException(409, 'original detail')

    @app.get('/api/crash')
    def crash():
        raise RuntimeError('password=private')

    @app.get('/api/doctor/fail')
    def doctor_fail():
        raise HTTPException(409, 'diagnostic error')

    client = TestClient(app)
    response = client.get('/api/fail?token=private')
    assert response.status_code == 409
    assert response.json() == {'detail': 'original detail'}
    repo = ReportRepository(setup.data / 'doctor/incidents')
    evidence = repo.get(response.headers['x-cyrene-incident-id'])
    assert evidence['code'] == 'http_409'
    assert 'private' not in json.dumps(evidence)
    crashed = client.get('/api/crash')
    assert crashed.status_code == 500
    assert crashed.json()['incidentId'] == crashed.headers['x-cyrene-incident-id']
    assert 'private' not in crashed.text
    assert 'x-cyrene-incident-id' not in client.get('/api/doctor/fail').headers

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,code', [
    ('invalid_tool_arguments','model_response_invalid'),
    ('protocol_invalid_json','model_response_invalid'),
    ('transport_interrupted','model_connection_failed'),
    ('upstream_incomplete','model_response_incomplete'),
    ('output_limit','model_output_truncated'),
])
async def test_chat_protocol_failures_keep_evidence_and_retry_scope(setup, kind, code):
    from cyrene.model.protocol_adapters import ModelStreamError
    from cyrene.model.error_details import ModelCallError, classify_model_error
    from cyrene.workbench.chat.chat_application import chat_error_metadata
    diagnostics = {'termination_reason':kind, 'stream_completed':False, 'authorization':'private',
                   'tool_calls':[{'name':'edit_file','arguments_validation':'invalid_json','arguments':'private'}]}
    raw = ModelStreamError(kind, 'private', diagnostics)
    error = ModelCallError(classify_model_error(raw), diagnostics=diagnostics)
    wrapped = RuntimeError('wrapper private')
    wrapped.__cause__ = error
    identifier = record_incident(wrapped, chat_id='chat', directory=setup.data/'doctor/incidents')
    report = await service(setup).diagnose({'incident_id':identifier,'chat_id':'chat','project_id':'project'})
    evidence = codes(report)[code]['evidence']
    assert evidence['model_error']['stream_diagnostics']['termination_reason'] == kind
    assert evidence['model_error']['retry_scope'] == chat_error_metadata(wrapped)['retry_scope']
    assert 'private' not in json.dumps(report)


def test_user_cancellation_is_not_a_failure_incident(setup):
    directory = setup.data/'doctor/incidents'
    assert record_incident(asyncio.CancelledError(), directory=directory) == ''
    assert not directory.exists()

@pytest.mark.asyncio
async def test_analysis_uses_redacted_problem_description(setup):
    seen = []
    async def analyzer(report):
        seen.append(report.get('user_description'))
        return {'summary':'Investigate the reported symptom', 'evidence_ids':['e1'], 'next_steps':[]}
    doctor = service(setup, analyzer=analyzer)
    report = await doctor.diagnose()
    await doctor.start_analysis(report['id'], description='Replies stop halfway. api_key=private-value')
    await asyncio.gather(*doctor.tasks.values())
    result = doctor.get(report['id'])
    assert seen == ['Replies stop halfway. [REDACTED]']
    assert result['user_description'] == seen[0]
    assert result['analysis']['status'] == 'completed'
    with pytest.raises(ValueError):
        await doctor.start_analysis(report['id'], description='x' * 4001)
    await doctor.start_analysis(report['id'], description='')
    await asyncio.gather(*doctor.tasks.values())
    assert seen[-1] == ''
