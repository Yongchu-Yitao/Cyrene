"""Regression tests for Workbench memory workspace resolution.

The memory page sends a project's ``dataKey`` as the ``workspace`` query param,
while memories are stored under the project's id-derived key. For the legacy
default project these differ (dataKey == "default", id == "project_…"), which
used to make ``_resolve_workspace_id`` miss the project and read an empty
"default" store — surfacing as a memory count of 0 for the default project.
"""

import pytest

from webui import routes as R
from webui import routes_workbench_memory as memory


@pytest.fixture
def default_project_store(monkeypatch):
    """Single legacy default project whose dataKey ("default") != id."""
    payload = {
        "projects": [
            {"id": "project_abc123", "dataKey": "default", "name": "Cyrene"}
        ]
    }
    monkeypatch.setattr(R, "_read_workbench_store", lambda: payload)
    return payload


def test_resolve_default_data_key_maps_to_project_memory_key(default_project_store):
    # Frontend sends the dataKey "default"; it must resolve to the project's
    # id-based memory key, not the literal (empty) "default" store.
    assert memory._resolve_workspace_id("default") == "project_abc123"


def test_resolve_project_id_still_maps_to_memory_key(default_project_store):
    # Passing the id directly keeps working (matched before the dataKey fallback).
    assert memory._resolve_workspace_id("project_abc123") == "project_abc123"


def test_resolve_unknown_workspace_falls_back_to_sanitized_id(default_project_store):
    # An identifier matching neither id nor dataKey falls back to its safe form.
    assert memory._resolve_workspace_id("project_other") == "project_other"


def test_default_project_memory_visible_via_data_key(monkeypatch, tmp_path, default_project_store):
    # End-to-end: a memory written under the project id is counted when the page
    # lists by dataKey "default".
    monkeypatch.setattr(memory, "STORE_DIR", tmp_path)
    monkeypatch.setattr(memory, "_STORE_DB_PATH", "")

    saved = memory.add_agent_memory("project_abc123", "你偏好简洁的回答", category="preference")
    assert saved is not None

    payload = memory._build_payload("default")
    assert payload["overview"]["total"] == 1
    cat_counts = {c["id"]: c["count"] for c in payload["categories"]}
    assert cat_counts["all"] == 1
    assert cat_counts["preference"] == 1
