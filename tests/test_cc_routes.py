import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from route.registry import register_routes


def test_cc_status_route_exposes_expected_session(monkeypatch):
    from cyrene import workbench_runtime as routes

    monkeypatch.setattr(
        routes,
        "get_cc_status",
        lambda cwd=None: {
            "available": False,
            "can_launch": True,
            "tmux_available": True,
            "expected_session": "claude-workspace",
            "tmux_session": "",
            "reason": "missing",
            "project_dir": "",
            "latest_jsonl": "",
            "latest_updated_at": "",
            "session_count": 0,
            "sessions": [],
        },
    )

    app = FastAPI()
    register_routes(app, bot=None, db_path="db.sqlite3")
    client = TestClient(app)

    response = client.get("/api/cc/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["expected_session"] == "claude-workspace"
    assert payload["available"] is False
