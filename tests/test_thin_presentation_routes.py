from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginApplicationHost, PluginRegistry
from agent.plugin.plugin_impl.cyrene_map import plugin_pack as map_plugin_pack
from cyrene.runtime.backup import BackupDownloadError, BackupRepository
from route.backup import register_backup_routes
from agent.plugin.plugin_impl.cyrene_memory.routes_overview import register_memory_routes
from route.search import register_search_routes
from route.system.shell import register_shell_routes


class FakePresentationQueries:
    def __init__(self) -> None:
        self.search_args = None

    @property
    def search_types(self):
        return frozenset({"project"})

    async def search_workbench(self, query, types, per_type_limit):
        self.search_args = (query, types, per_type_limit)
        return {"project": [{"id": "project_1"}]}

    async def overview(self):
        return {"memories": [{"id": "memory_1"}]}

    async def ui_data(self, timezone_name=""):
        return {"timezone": timezone_name}

    async def dashboard(self, timezone_name=""):
        return {"dashboardTimezone": timezone_name}


def test_presentation_routes_delegate_to_explicit_query_service(tmp_path: Path):
    queries = FakePresentationQueries()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<main>Cyrene</main>", encoding="utf-8")
    app = FastAPI()
    register_shell_routes(app, queries, app_dir)
    register_search_routes(app, queries)
    register_memory_routes(app, queries)
    client = TestClient(app)

    search = client.get(
        "/api/workbench/search",
        params={"q": " Alpha ", "types": "project,unknown", "limit": 101},
    )

    assert search.json() == {
        "ok": True,
        "groups": {"project": [{"id": "project_1"}]},
    }
    assert queries.search_args == ("Alpha", {"project"}, 100)
    assert client.get("/api/memory").json()["memories"][0]["id"] == "memory_1"
    assert client.get("/api/ui-data", params={"tz": "Asia/Shanghai"}).json() == {
        "timezone": "Asia/Shanghai"
    }
    assert client.get("/api/dashboard", params={"tz": "UTC"}).json() == {
        "dashboardTimezone": "UTC"
    }
    root = client.get("/")
    assert root.text == "<main>Cyrene</main>"
    assert root.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_map_route_is_owned_by_the_map_plugin(tmp_path: Path):
    app = FastAPI()
    registry = PluginRegistry(include_core=False)
    registry.register_pack(map_plugin_pack, source="test")
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "cyrene.sqlite3"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    host.attach(router)
    app.include_router(router)
    host.service("maps").add_pin(
        "chat_1",
        lat=39.9,
        lng=116.4,
        name="Beijing",
    )

    response = TestClient(app).get("/api/map/pins", params={"session_id": " chat_1 "})

    assert response.status_code == 200
    assert response.json()["pins"][0]["name"] == "Beijing"
    assert response.json()["routes"] == []


def test_backup_repository_owns_download_boundary(tmp_path: Path):
    archive = tmp_path / "cyrene_backup_20260823.zip"
    archive.write_bytes(b"archive")
    backups = BackupRepository(tmp_path)
    app = FastAPI()
    register_backup_routes(app, backups)

    response = TestClient(app).get(f"/api/backup/download/{archive.name}")

    assert response.status_code == 200
    assert response.content == b"archive"
    assert backups.list()[0]["name"] == archive.name
    with pytest.raises(BackupDownloadError) as exc_info:
        backups.download("../outside.zip")
    assert exc_info.value.status_code == 400
