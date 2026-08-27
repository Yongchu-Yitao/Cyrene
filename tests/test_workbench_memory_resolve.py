"""Canonical project-id tests for Plugin-owned workspace memory."""


from agent.plugin.plugin_impl.cyrene_memory import structured as memory


def test_default_workspace_keeps_its_canonical_key():
    assert memory._resolve_workspace_id("default") == "default"


def test_resolve_project_id_keeps_canonical_memory_key():
    assert memory._resolve_workspace_id("project_abc123") == "project_abc123"


def test_resolve_workspace_sanitizes_invalid_characters():
    assert memory._resolve_workspace_id("project / other") == "project_other"


def test_resolve_unknown_workspace_keeps_safe_id():
    assert memory._resolve_workspace_id("project_other") == "project_other"


def test_project_memory_visible_via_canonical_id(tmp_path):
    from cyrene.workbench.store import ensure_schema

    database = tmp_path / "memory.db"
    ensure_schema(database)
    memory.configure_store(str(database))

    saved = memory.add_agent_memory("project_abc123", "你偏好简洁的回答", category="preference")
    assert saved is not None

    payload = memory._build_payload("project_abc123")
    assert payload["overview"]["total"] == 1
    cat_counts = {c["id"]: c["count"] for c in payload["categories"]}
    assert cat_counts["all"] == 1
    assert cat_counts["preference"] == 1
