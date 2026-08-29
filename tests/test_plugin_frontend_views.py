from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from cyrene.core.plugin import (
    ExtensionContribution,
    Plugin,
    PluginContext,
    PluginPack,
    PluginRegistry,
    PluginSetupContext,
    resolve_resource_effect_values,
)
from cyrene.plugins import (
    WORKBENCH_SURFACE,
    WORKSPACE_ACTION,
    WORKSPACE_FILE_TYPE,
    PluginApplicationHost,
    WorkbenchSurfaceContribution,
    WorkbenchSurfaceRenderer,
    WorkspaceActionContribution,
    WorkspaceFileTypeContribution,
)
from cyrene.plugins.builtin.cyrene_plugin_development.tools import (
    SCAFFOLD_TYPES,
    scaffold,
    validate_pack_directory,
    validate_plugin_source,
)
from cyrene.workbench.http.plugins import plugin_registry_status, register_plugin_routes


ROOT = Path(__file__).resolve().parents[1]


def test_plugin_resource_effects_are_validated_and_resolved_without_path_access() -> None:
    async def handler(_arguments, _context):
        return None

    plugin = Plugin(
        name="ResourceTool",
        description="resource",
        input_schema={"type": "object"},
        handler=handler,
        metadata={
            "resource_effects": ({
                "argument_path": ("target", "path"),
                "kind": "file",
                "access": "write",
                "phase": "started",
            },),
        },
    )
    assert resolve_resource_effect_values(
        plugin.resource_effects,
        {"target": {"path": "src/app.py"}},
        phase="started",
    ) == ({
        "value": "src/app.py",
        "kind": "file",
        "access": "write",
        "phase": "started",
    },)
    assert resolve_resource_effect_values(
        plugin.resource_effects,
        {"target": {"path": "src/app.py"}},
        phase="completed",
    ) == ()

    with pytest.raises(ValueError, match="argument_path"):
        Plugin(
            name="InvalidResourceTool",
            description="invalid",
            input_schema={"type": "object"},
            handler=handler,
            metadata={
                "resource_effects": ({
                    "argument_path": ("..",),
                    "kind": "file",
                    "access": "write",
                },),
            },
        )


def test_plugin_pack_frontend_view_rpc_asset_and_registry_routes(tmp_path) -> None:
    package = tmp_path / "plugin_impl" / "dashboard"
    (package / "ui").mkdir(parents=True)
    (package / "ui" / "index.html").write_text("<h1>dashboard</h1>", encoding="utf-8")

    async def ping(arguments, request_context):
        return {"echo": arguments, "project_id": request_context["project_id"]}

    def application_setup(context) -> None:
        context.provide_frontend_method("ping", ping)

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(
            id="dashboard",
            description="dashboard",
            plugins=(),
            application_setup=application_setup,
            contributions=(
                ExtensionContribution(
                    WORKBENCH_SURFACE,
                    WorkbenchSurfaceContribution(
                        id="main",
                        title="Dashboard Surface",
                        renderer=WorkbenchSurfaceRenderer("plugin_view", "main"),
                        accepted_activities=("read", "write"),
                        resource_kinds=("file",),
                        preferred_side="right",
                    ),
                ),
                ExtensionContribution(
                    WORKSPACE_FILE_TYPE,
                    WorkspaceFileTypeContribution(
                        id="dashboard-source",
                        extensions=(".dash",),
                        language_id="dashboard",
                        editable=True,
                        default_surface="dashboard/main",
                    ),
                ),
                ExtensionContribution(
                    WORKSPACE_ACTION,
                    WorkspaceActionContribution(
                        id="preview",
                        kind="preview",
                        method="ping",
                        extensions=(".dash",),
                        outputs=("endpoint",),
                        default_surface="dashboard/main",
                    ),
                ),
            ),
            metadata={
                "frontend_views": ({
                    "id": "main",
                    "entry": "ui/index.html",
                    "title": "Dashboard",
                    "i18n": {"zh": {"title": "仪表盘"}},
                },),
                "project_tools": ({
                    "id": "main",
                    "view": "main",
                    "title": "Dashboard",
                    "i18n": {"zh": {"title": "仪表盘"}},
                },),
            },
        ),
        source=str(package),
    )
    app = FastAPI()
    host = PluginApplicationHost(
        app=app,
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    application_router = APIRouter()
    host.attach(application_router)
    app.include_router(application_router)
    plugin_router = APIRouter()
    register_plugin_routes(plugin_router, host)
    app.include_router(plugin_router)
    asyncio.run(host.startup())
    try:
        contributions = host.frontend_contributions()
        assert contributions["views"][0]["pack_id"] == "dashboard"
        assert contributions["project_tools"][0]["view"] == "main"
        assert contributions["surfaces"][0]["id"] == "dashboard/main"
        assert contributions["file_types"][0]["extensions"] == [".dash"]
        assert contributions["actions"][0]["method"] == "ping"
        with TestClient(app) as client:
            status = client.get("/api/plugins").json()
            assert status["frontend_views"][0]["id"] == "main"
            assert status["project_tools"][0]["pack_id"] == "dashboard"
            assert status["workbench_surfaces"][0]["renderer"] == {
                "kind": "plugin_view",
                "id": "main",
            }
            assert status["workspace_file_types"][0]["id"] == "dashboard/dashboard-source"
            assert status["workspace_actions"][0]["id"] == "dashboard/preview"
            asset = client.get("/api/plugins/packs/dashboard/assets/ui/index.html")
            assert asset.status_code == 200
            assert "dashboard" in asset.text
            response = client.post(
                "/api/plugins/packs/dashboard/call",
                json={"method": "ping", "args": {"value": 1}, "project_id": "project-1"},
            )
            assert response.json() == {
                "ok": True,
                "result": {"echo": {"value": 1}, "project_id": "project-1"},
            }
    finally:
        asyncio.run(host.shutdown())


def test_plugin_authoring_example_uses_unified_pack_protocol() -> None:
    example = ROOT / "examples" / "plugins" / "model-usage"
    validation = validate_pack_directory(example)
    assert validation["ok"] is True
    assert validation["frontend_view_count"] == 1
    assert validation["project_tool_count"] == 1
    assert not (example / "plugin.json").exists()

    frontend = (ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend")
    plugin_service = (frontend / "platform" / "plugins.jsx").read_text(encoding="utf-8")
    page = (frontend / "features" / "chat" / "page.jsx").read_text(encoding="utf-8")
    rail = (frontend / "features" / "chat" / "rail.jsx").read_text(encoding="utf-8")
    detached = (frontend / "features" / "chat" / "context-panel.jsx").read_text(encoding="utf-8")
    assert "function PluginView" in plugin_service
    assert 'sandbox="allow-scripts allow-forms allow-modals allow-downloads allow-popups"' in plugin_service
    assert 'card.kind === "plugin-view"' in page
    assert 'openPaneContent("plugin-view"' in page
    assert 'className="wbc-side-agent-split wbc-plugin-view-pane"' in page
    assert 'className="wbc-plugin-view-host-strip"' in page
    assert 'className="wbc-plugin-view-content"' in page
    assert "snapshot.projectTools" in rail
    assert 'kind === "plugin-view"' in detached
    assert 'className="wbc-side-agent-split wbc-plugin-view-pane detached"' in detached
    assert 'className="wbc-plugin-view-host-strip"' not in detached
    assert 'className="wbc-plugin-view-content"' in detached
    workspace_styles = (frontend / "features" / "chat" / "workspace.css").read_text(encoding="utf-8")
    plugin_pane_styles = workspace_styles.split(".wbc-plugin-view-pane {", 1)[1].split("}", 1)[0]
    assert "background: inherit;" in plugin_pane_styles
    assert ".wbc-pane-card > .wbc-side-agent-split.wbc-plugin-view-pane" in workspace_styles
    assert ".wbc-detached-pane-content > .wbc-side-agent-split.wbc-plugin-view-pane" in workspace_styles
    docked_plugin_pane_styles = workspace_styles.split(
        ".wbc-pane-card > .wbc-side-agent-split.wbc-plugin-view-pane {", 1
    )[1].split("}", 1)[0]
    assert "grid-template-rows: 34px minmax(0, 1fr);" in docked_plugin_pane_styles
    assert "padding-top: 0;" in docked_plugin_pane_styles
    assert ".wbc-plugin-view-pane.detached .wbc-plugin-view-content" in workspace_styles
    assert "body.wbc-resizing-pane-column .wbc-plugin-view-frame" in workspace_styles


def test_plugin_scaffold_creates_every_unified_plugin_type(tmp_path) -> None:
    context = PluginContext(workspace=tmp_path)
    created: list[Path] = []
    for plugin_type in SCAFFOLD_TYPES:
        pack_id = f"sample_{plugin_type}"
        target = (
            tmp_path / f"{pack_id}.py"
            if plugin_type == "standalone_tool"
            else tmp_path / pack_id
        )
        result = json.loads(asyncio.run(scaffold({
            "path": str(target),
            "plugin_type": plugin_type,
            "pack_id": pack_id,
            "name": f"Sample {plugin_type}",
            "description": f"Generated {plugin_type}",
        }, context)))
        assert result["ok"] is True, result
        assert validate_plugin_source(target)["ok"] is True
        created.append(target)

    registry = PluginRegistry(include_core=False)
    failures = registry.load_directory(tmp_path)
    assert failures == ()
    assert registry.resolve("SampleStandaloneToolTool") is not None
    assert {pack.id for pack in registry.list_packs()} == {
        path.name for path in created if path.is_dir()
    }
    full_pack = next(
        pack for pack in registry.list_packs() if pack.id == "sample_full_pack"
    )
    assert {plugin.kind for plugin in full_pack.plugins} == {"tool", "model"}
    assert callable(full_pack.setup)
    assert callable(full_pack.application_setup)
    assert full_pack.metadata["frontend_views"][0]["entry"] == "ui/index.html"
    assert full_pack.metadata["project_tools"][0]["view"] == "main"
    assert full_pack.extensions.values(WORKBENCH_SURFACE)[0].renderer.id == "main"
    assert (tmp_path / "sample_full_pack" / "context.py").is_file()

    class Hooks:
        registered = []

        def list(self):
            return []

        def register(self, event, handler, **metadata):
            self.registered.append((event, handler, metadata))

    hooks = Hooks()
    full_pack.setup(PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path,
        workspace=tmp_path,
        tree=None,
        tree_id="tree-1",
        root_id="root-1",
        hooks=hooks,
        data={},
        services={},
    ))
    assert hooks.registered[0][0] == "SessionStart"
    assert hooks.registered[0][2]["hook_id"] == "sample_full_pack-session-start"


def test_plugin_center_marks_unmanaged_user_sources_for_top_section(tmp_path) -> None:
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    (plugin_directory / ".upstream-hashes.json").write_text(json.dumps({
        "version": 1,
        "files": {
            "builtin_pack/__init__.py": "0" * 64,
            "builtin_tool.py": "1" * 64,
        },
    }), encoding="utf-8")

    async def handler(_arguments, _context):
        return {"ok": True}

    registry = PluginRegistry(include_core=False)
    registry.register_pack(
        PluginPack(id="builtin_pack", description="builtin", plugins=()),
        source=str(plugin_directory / "builtin_pack"),
    )
    registry.register_pack(
        PluginPack(id="my_pack", description="mine", plugins=()),
        source=str(plugin_directory / "my_pack"),
    )
    registry.register_plugin(
        Plugin(name="BuiltinTool", description="builtin", input_schema={"type": "object"}, handler=handler),
        source=str(plugin_directory / "builtin_tool.py"),
    )
    registry.register_plugin(
        Plugin(name="MyTool", description="mine", input_schema={"type": "object"}, handler=handler),
        source=str(plugin_directory / "my_tool.py"),
    )
    host = PluginApplicationHost(
        app=FastAPI(), registry=registry, bot=None,
        db_path=str(tmp_path / "app.db"), data_directory=tmp_path / "data",
        plugin_directory=plugin_directory,
    )
    status = plugin_registry_status(host)
    packs = {item["id"]: item for item in status["packs"]}
    standalone = {item["name"]: item for item in status["standalone_plugins"]}
    assert packs["builtin_pack"]["user_created"] is False
    assert packs["my_pack"]["user_created"] is True
    assert standalone["BuiltinTool"]["user_created"] is False
    assert standalone["MyTool"]["user_created"] is True

    frontend = (ROOT / "src/cyrene/workbench/webui/frontend/features/settings/custom-plugins.jsx").read_text(encoding="utf-8")
    assert frontend.index("UserCreatedPluginsSection") < frontend.index("PluginPacksSection, { controller: c }")
    assert "item.user_created === true" in frontend
