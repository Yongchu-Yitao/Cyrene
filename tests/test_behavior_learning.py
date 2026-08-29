import sys
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))




def test_behavior_media_route_serves_plugin_owned_path_with_spaces(
    monkeypatch, tmp_path
):
    from cyrene.plugins.builtin.cyrene_skills import orchestrator as learning
    from cyrene.plugins.builtin.cyrene_skills.application_service import (
        LearningApplicationService,
        MediaRepository,
        ProjectResolver,
        ToolChainProjection,
    )
    from cyrene.plugins.builtin.cyrene_skills import routes as learning_routes

    data_dir = tmp_path / "Application Support" / "Cyrene" / "data"
    target = data_dir / "behavior-media" / "turn_1" / "capture.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    stored_path = str(target)

    async def list_tool_chains(_project="", _limit=500):
        return [{
            "chain": [{
                "tool": "desktop.use",
                "output_summary": f'{{"path":"{stored_path}"}}',
            }],
        }]

    monkeypatch.setattr(learning, "list_tool_chains", list_tool_chains)
    app = FastAPI()
    router = APIRouter()
    media = MediaRepository(data_dir)

    async def status():
        return {"phase": "evolve", "state": "进化"}

    learning_routes.register_learning_routes(
        router,
        LearningApplicationService(
            ProjectResolver(lambda _project: None),
            media,
            ToolChainProjection(media),
            status,
        ),
    )
    app.include_router(router)

    response = TestClient(app).get(
        "/api/tool-chain-media", params={"path": stored_path}
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"


def test_installed_skill_uses_migrated_plugin_path(
    monkeypatch, tmp_path
):
    from cyrene.plugins.builtin.cyrene_skills import skills

    installed_root = tmp_path / "current" / "data" / "installed_skills"
    skill_dir = installed_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\ndescription: Restored skill\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills, "_SKILLS_DIR", installed_root)

    payload = skills.skill_payload_from_record({
        "id": "demo-skill",
        "enabled": True,
        "stored_path": str(skill_dir),
    })

    assert payload is not None
    assert payload["name"] == "Demo Skill"
    assert Path(payload["stored_path"]) == skill_dir


def test_legacy_learning_data_migrates_once_and_rewrites_paths(
    monkeypatch,
    tmp_path,
):
    from cyrene.plugins.builtin.cyrene_skills.application import (
        migrate_legacy_learning_data,
    )
    from cyrene.plugins.builtin.cyrene_skills import skills

    legacy_root = tmp_path / "data"
    plugin_root = legacy_root / "plugin_data" / "cyrene_skills"
    legacy_skill = legacy_root / "installed_skills" / "demo"
    legacy_skill.mkdir(parents=True)
    (legacy_skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    legacy_script = legacy_root / "learned_skill_scripts" / "demo" / "run.py"
    legacy_script.parent.mkdir(parents=True)
    legacy_script.write_text("print('demo')\n", encoding="utf-8")
    database = legacy_root / "behavior-learning.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (payload TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO sample(payload) VALUES (?)",
            (json.dumps({"script_path": str(legacy_script)}),),
        )

    records = [{
        "id": "demo",
        "enabled": True,
        "stored_path": str(legacy_skill),
    }]
    saved: list[list[dict]] = []
    monkeypatch.setattr(skills, "skill_settings_records", lambda: records)
    monkeypatch.setattr(
        skills,
        "save_skill_settings_records",
        lambda value: saved.append(value),
    )

    migrate_legacy_learning_data(legacy_root, plugin_root)

    migrated_skill = plugin_root / "installed_skills" / "demo"
    migrated_script = plugin_root / "learned_skill_scripts" / "demo" / "run.py"
    assert migrated_skill.is_dir()
    assert migrated_script.is_file()
    assert records[0]["stored_path"] == str(migrated_skill)
    assert saved == [records]
    with sqlite3.connect(plugin_root / "behavior-learning.db") as connection:
        payload = connection.execute("SELECT payload FROM sample").fetchone()[0]
    assert json.loads(payload)["script_path"] == str(migrated_script)

    migrate_legacy_learning_data(legacy_root, plugin_root)
    assert saved == [records]





























































# ---------------------------------------------------------------------------
# Issue #46 regression: high-risk skill replay must not execute silently
# ---------------------------------------------------------------------------

def _make_skill_with_steps(steps: list[dict], *, risk_level: str = "none") -> dict:
    """Build a minimal skill dict for execution testing."""
    return {
        "skill_id": "test-skill-001",
        "name": "Test Skill",
        "description": "test",
        "version": 1,
        "status": "active",
        "skill_type": "deterministic",
        "risk_level": risk_level,
        "requires_llm": False,
        "trigger": {"positive_examples": []},
        "input_schema": [],
        "parameter_extractor": {"mode": "hybrid", "llm_fallback": False},
        "steps": steps,
        "guards": {"risk_level": risk_level, "required_context": [], "forbidden_conditions": [], "confidence_threshold": 0.75},
        "fallback_policy": {},
        "tests": [],
        "editable_fields": [],
        "created_from": {},
        "run_statistics": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _make_step(tool_name: str) -> dict:
    return {
        "enabled": True,
        "implementation_kind": "tool_call",
        "implementation_reference": {"tool_name": tool_name, "args_template": {}},
    }

async def test_parameterized_runner_applies_typed_defaults(tmp_path, monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_skills import run_learned_skill as runner
    from cyrene.plugins.builtin.cyrene_skills import orchestrator as bl

    skill = _make_skill_with_steps([{
        "enabled": True,
        "implementation_kind": "tool_call",
        "implementation_reference": {
            "tool_name": "read_file",
            "args_template": {"path": "{{input_path}}", "limit": "{{line_limit}}"},
        },
    }])
    skill["input_schema"] = [
        {"parameter_name": "input_path", "type": "path", "required": False, "default_value": "src/app.py"},
        {"parameter_name": "line_limit", "type": "number", "required": False, "default_value": 20},
    ]
    monkeypatch.setattr(bl, "get_learned_skill_by_name", AsyncMock(return_value=skill))
    execute = AsyncMock(return_value="file content")
    monkeypatch.setattr(runner, "invoke_plugin", execute)
    monkeypatch.setattr(bl, "record_manual_skill_run", AsyncMock())

    result = await runner._tool_run_learned_skill(
        {"name": skill["name"], "params": {}}, PluginContext(),
    )

    assert runner.json.loads(result)["ok"] is True
    called_args = execute.await_args.args[1]
    assert called_args == {"path": "src/app.py", "limit": 20}
    assert execute.await_args.kwargs == {"review": True}


def test_parameterized_runner_detects_unsafe_script_wrapper():
    from cyrene.plugins.builtin.cyrene_skills import run_learned_skill as runner

    wrapper = {
        "enabled": True,
        "implementation_kind": "script",
        "implementation_reference": {"original_steps": [_make_step("Bash")]},
    }
    assert runner._has_unsafe_step([wrapper]) is True
