import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import aiosqlite
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.plugins.builtin.cyrene_skills import orchestrator as learning
from cyrene.plugins.builtin.cyrene_skills.application_service import (
    LearningApplicationService, MediaRepository, ProjectResolver, ToolChainProjection,
)
from cyrene.plugins.builtin.cyrene_skills.artifacts import structured_paths
from cyrene.plugins.builtin.cyrene_skills.capture import CaptureService, chain_item_from_action
from cyrene.plugins.builtin.cyrene_skills.routes import register_learning_routes


def test_file_references_do_not_come_from_text():
    assert structured_paths({
        "stdout": '{"path":"/tmp/private.png"}',
        "command": "cat /tmp/private.png",
        "description": "/tmp/private.png",
        "nested": {"screenshot": {"path": "/tmp/actual image.png"}},
    }) == ["/tmp/actual image.png"]
    for path in ["https://host/a.png", "//host/a.png", "/tmp/" + "x" * 256,
                 "/tmp/bad\0.png", "relative.png"]:
        assert structured_paths({"path": path}) == []


def test_capture_manifest_survives_summary_truncation_and_temp_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "temporary screenshot.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    db_path = tmp_path / "capture.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE behavior_actions (
            action_id TEXT, turn_id TEXT, session_id TEXT, round_id TEXT,
            created_at TEXT, action_index INTEGER, action_type TEXT, action_subtype TEXT,
            tool_name TEXT, input_summary TEXT, output_summary TEXT, success INTEGER,
            error_summary TEXT, requires_llm INTEGER, risk_level TEXT, metadata_json TEXT
        )""")

    @asynccontextmanager
    async def connect():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    capture = CaptureService(SimpleNamespace(
        data_dir=tmp_path / "data", connect=connect,
        now_iso=lambda: "2026-09-05T00:00:00Z", new_id=lambda _: "action_1",
    ))
    asyncio.run(capture.record_action(
        "desktop.use", {"command": "echo /tmp/unrelated.png"}, "main_agent", "run_1", 1,
        result={"description": "long output " * 100, "image": {"path": str(source)}},
        session_id="chat_1", turn_id="turn_1",
    ))
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM behavior_actions").fetchone())
    assert len(row["output_summary"]) == 500
    assert str(source) not in row["output_summary"]
    row["metadata_json"] = json.loads(row["metadata_json"])
    step = chain_item_from_action(row)
    assert len(step["artifacts"]) == 1
    durable = Path(step["artifacts"][0]["path"])
    assert durable != source
    assert durable.read_bytes() == source.read_bytes()
    source.unlink()

    # Simulate serialized chain storage and a fresh HTTP read after cleanup.
    chains = json.loads(json.dumps([{"summary": {"total_steps": 1}, "chain": [step]}]))
    async def list_chains(*args):
        return chains
    monkeypatch.setattr(learning, "list_tool_chains", list_chains)
    client = _client(tmp_path)
    response = client.get("/api/tool-chains")
    assert response.status_code == 200
    screenshots = response.json()["tool_chains"][0]["screenshots"]
    assert len(screenshots) == 1
    image = client.get(screenshots[0]["url"])
    assert image.status_code == 200
    assert image.content == b"\x89PNG\r\n\x1a\n"


def _client(tmp_path):
    async def status():
        return {"phase": "evolve", "state": "ready"}
    media = MediaRepository(tmp_path / "data")
    router = APIRouter()
    register_learning_routes(router, LearningApplicationService(
        ProjectResolver(lambda _: None), media, ToolChainProjection(media), status,
    ))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_evolution_ignores_legacy_logs_and_media_authority_requires_manifest(tmp_path, monkeypatch):
    image = tmp_path / "private.png"
    image.write_bytes(b"image")
    log = ("{'stdout': '[log] https://phaser.io background: #ff0000 " * 20
           + r"\n[pageerror] F.body.refreshBody is not a function"
           + " at create (http://localhost:8899/tools/probe_size.html:26:42)'}")
    chains = [{"summary": {"total_steps": 3}, "chain": [
        {"tool": "Bash", "output_summary": log},
        {"tool": "Bash", "output_summary": str(image)},
        # An explicit empty manifest is authoritative even if summary has JSON.
        {"tool": "Bash", "artifacts": [], "output_summary": json.dumps({"path": str(image)})},
    ]}]
    async def list_chains(*args):
        return chains
    async def empty(*args):
        return []
    monkeypatch.setattr(learning, "list_tool_chains", list_chains)
    monkeypatch.setattr(learning, "list_learned_skills", empty)
    monkeypatch.setattr(learning, "list_skill_candidates", empty)
    client = _client(tmp_path)
    response = client.get("/api/evolution")
    assert response.status_code == 200
    chain = response.json()["tool_chains"][0]
    assert chain["screenshots"] == chain["files"] == []
    assert client.get("/api/tool-chain-media", params={"path": str(image)}).status_code == 404
    assert client.get("/api/tool-chain-media", params={"path": "/tmp/" + "x" * 300}).status_code == 400
