import sys
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))




def test_behavior_media_route_rebases_restored_path_with_spaces(
    monkeypatch, tmp_path
):
    import cyrene.learning.orchestrator as learning
    from cyrene.learning.application_service import (
        LearningApplicationService,
        MediaRepository,
        ProjectResolver,
        ToolChainProjection,
    )
    from route import learning as learning_routes

    data_dir = tmp_path / "Application Support" / "Cyrene" / "data"
    target = data_dir / "behavior-media" / "turn_1" / "capture.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    old_path = (
        "/Users/old/Library/Application Support/Cyrene/"
        "data/behavior-media/turn_1/capture.png"
    )

    async def list_tool_chains(_project="", _limit=500):
        return [{
            "chain": [{
                "tool": "desktop.use",
                "output_summary": f'{{"path":"{old_path}"}}',
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
        "/api/tool-chain-media", params={"path": old_path}
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"


def test_installed_skill_path_rebases_after_portable_restore(
    monkeypatch, tmp_path
):
    from cyrene.learning import skills

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
        "stored_path": (
            "/Users/old/Library/Application Support/Cyrene/"
            "data/installed_skills/demo-skill"
        ),
    })

    assert payload is not None
    assert payload["name"] == "Demo Skill"
    assert Path(payload["stored_path"]) == skill_dir





























































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
    from agent.plugin import PluginContext
    from agent.plugin.plugin_impl.cyrene_skills import run_learned_skill as runner
    import cyrene.learning.orchestrator as bl

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
    from agent.plugin.plugin_impl.cyrene_skills import run_learned_skill as runner

    wrapper = {
        "enabled": True,
        "implementation_kind": "script",
        "implementation_reference": {"original_steps": [_make_step("Bash")]},
    }
    assert runner._has_unsafe_step([wrapper]) is True
