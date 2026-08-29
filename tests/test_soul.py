"""Plugin-owned SOUL storage has no direct dependency on the retired backend."""

from pathlib import Path

import pytest

from cyrene.plugins.builtin.cyrene_soul import store as soul_module
from cyrene.plugins.builtin.cyrene_soul.store import SoulApplication, ensure_soul

_LEGACY_SOUL = "# Old Soul\n\n## SELF:IDENTITY\n- I am the user's persona\n"


@pytest.fixture
def soul_env(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    cyrene = workspace / ".cyrene"
    cyrene.mkdir(parents=True)
    monkeypatch.setattr(soul_module, "WORKSPACE_DIR", workspace)
    return {"workspace": workspace, "cyrene": cyrene}


def _write_legacy(workspace: Path) -> Path:
    legacy = workspace / "SOUL.md"
    legacy.write_text(_LEGACY_SOUL, encoding="utf-8")
    return legacy


def test_ensure_soul_ignores_root_level_soul(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    legacy = _write_legacy(workspace)
    ensure_soul()
    target = cyrene / "SOUL.md"
    assert target.read_text(encoding="utf-8") != _LEGACY_SOUL
    assert "## SELF:IDENTITY" in target.read_text(encoding="utf-8")
    assert legacy.exists()


def test_ensure_soul_writes_default_when_no_legacy(soul_env):
    cyrene = soul_env["cyrene"]
    ensure_soul()
    target = cyrene / "SOUL.md"
    assert target.exists()
    assert "## SELF:IDENTITY" in target.read_text(encoding="utf-8")


def test_ensure_soul_skips_unrelated_root_file(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    legacy = workspace / "SOUL.md"
    legacy.write_text("# My own notes", encoding="utf-8")
    ensure_soul()
    assert legacy.exists()
    assert "## SELF:IDENTITY" in (cyrene / "SOUL.md").read_text(encoding="utf-8")


def test_ensure_soul_keeps_existing_cyrene_soul(soul_env):
    workspace, cyrene = soul_env["workspace"], soul_env["cyrene"]
    target = cyrene / "SOUL.md"
    target.write_text("existing", encoding="utf-8")
    _write_legacy(workspace)
    ensure_soul()
    assert target.read_text(encoding="utf-8") == "existing"
    assert (workspace / "SOUL.md").exists()


def test_soul_application_owns_edit_reset_and_backup(soul_env):
    cyrene = soul_env["cyrene"]
    application = SoulApplication()

    application.write("# Custom Soul\n\n## SELF:IDENTITY\n- independent\n")
    assert application.read().endswith("- independent\n")
    assert application.persona_context().endswith("- independent")
    assert application.storage_paths() == {"memory": (cyrene / "SOUL.md",)}
    assert application.backup_sources() == {
        "files": ((cyrene / "SOUL.md", "workspace/SOUL.md"),),
    }

    application.reset()
    assert "## SELF:IDENTITY" in application.read()


def test_soul_onboarding_status_is_owned_by_soul_pack(
    soul_env,
    monkeypatch,
    tmp_path,
):
    from cyrene.plugins.builtin.cyrene_soul import onboarding as soul_onboarding
    from cyrene.plugins.builtin.cyrene_soul.onboarding import (
        SoulOnboardingApplication,
    )

    monkeypatch.setattr(soul_onboarding, "DATA_DIR", tmp_path / "data")
    application = SoulApplication()
    onboarding = SoulOnboardingApplication(application)

    assert onboarding.status()["configured"] is False
    application.write("# Custom Soul\n\n## SELF:IDENTITY\n- plugin owned\n")
    assert onboarding.status()["configured"] is True


def test_soul_settings_api_edits_without_memory_service(soul_env, tmp_path):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from cyrene.plugins import PluginApplicationContext
    from cyrene.plugins.builtin.cyrene_soul import application_setup

    app = FastAPI()
    router = APIRouter()
    services = {}
    context = PluginApplicationContext(
        app=app,
        router=router,
        bot=None,
        db_path=str(tmp_path / "runtime.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins",
        services=services,
        frontend_modules=[],
        search_providers={},
        startup_handlers=[],
        shutdown_handlers=[],
    )
    application_setup(context)
    app.include_router(router)

    assert "memory" not in services
    assert services["soul_onboarding"] is not None
    assert "/api/onboarding/personality" in {route.path for route in router.routes}
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/soul",
            json={"content": "# API Soul\n\n## SELF:IDENTITY\n- editable\n"},
        )
        assert response.status_code == 200
        assert client.get("/api/settings/soul").json()["content"].startswith(
            "# API Soul"
        )
