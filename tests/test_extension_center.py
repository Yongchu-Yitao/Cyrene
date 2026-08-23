import json
import io
import os
import tarfile
from pathlib import Path

import pytest

from conftest import (
    workbench_i18n_source,
    workbench_settings_source,
    workbench_style_source,
)


@pytest.mark.asyncio
async def test_cli_search_falls_back_to_aqua_standard_registry(monkeypatch):
    from cyrene.extensions import service

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tree": [
                {"path": "pkgs/rtk-ai/rtk/registry.yaml"},
                {"path": "pkgs/example/unrelated/registry.yaml"},
                {"path": "docs/rtk/registry.yaml"},
            ]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url == service._AQUA_REGISTRY_TREE_URL
            return Response()

    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {"github_token": ""})
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(service, "_bundled_binary", lambda _name: None)

    extension_service = object.__new__(service.ExtensionService)
    result = await extension_service.search("cli", "rtk")

    assert [(item["id"], item["ref"]) for item in result["results"]] == [
        ("rtk", "aqua:rtk-ai/rtk"),
    ]


@pytest.mark.asyncio
async def test_advanced_cli_search_combines_npm_pypi_and_rubygems(monkeypatch):
    from cyrene.extensions import service

    extension_service = object.__new__(service.ExtensionService)
    monkeypatch.setattr(service, "_bundled_binary", lambda _name: None)
    monkeypatch.setattr(extension_service, "_search_aqua_registry", lambda _query: _async_result([]))
    monkeypatch.setattr(extension_service, "_search_npm_registry", lambda _query: _async_result([
        extension_service._ecosystem_cli_result(backend="npm", name="demo", version="1.0.0"),
    ]))
    monkeypatch.setattr(extension_service, "_search_pypi_cli", lambda _query: _async_result([
        extension_service._ecosystem_cli_result(backend="pipx", name="demo", version="2.0.0"),
    ]))
    monkeypatch.setattr(extension_service, "_search_rubygems", lambda _query: _async_result([
        extension_service._ecosystem_cli_result(backend="gem", name="demo", version="3.0.0"),
    ]))

    basic = await extension_service.search("cli", "demo", advanced=False)
    advanced = await extension_service.search("cli", "demo", advanced=True)

    assert basic["results"] == []
    assert [(item["backend"], item["ref"]) for item in advanced["results"]] == [
        ("npm", "npm:demo"), ("pipx", "pipx:demo"), ("gem", "gem:demo"),
    ]


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_toolchain_search_adds_only_deduplicated_mise_core_runtimes(monkeypatch, tmp_path):
    from cyrene.extensions import service

    registry = [
        {"short": "dotnet", "backends": ["core:dotnet"], "description": ".NET SDK"},
        {"short": "dotnet-core", "backends": ["core:dotnet"], "description": ".NET alias"},
        {"short": "python", "backends": ["core:python"], "description": "Python"},
        {"short": "terraform", "backends": ["aqua:hashicorp/terraform"], "description": "CLI"},
        {"short": "unsafe", "backends": ["core:../unsafe"], "description": "Invalid"},
        {"short": "future", "backends": ["core:future"], "description": "Not approved"},
    ]

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps(registry).encode(), b""

    async def create_process(*command, **_kwargs):
        assert command[-2:] == ("registry", "--json")
        return Process()

    monkeypatch.setattr(service, "_bundled_binary", lambda name: tmp_path / name if name == "mise" else None)
    monkeypatch.setattr(service, "extension_environment", lambda: {})
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", create_process)

    extension_service = object.__new__(service.ExtensionService)
    result = await extension_service.search("toolchain", "dotnet")

    assert result["source"] == "cyrene-catalog+mise-core"
    assert [(item["id"], item["ref"], item["backend"]) for item in result["results"]] == [
        ("dotnet", "core:dotnet", "core"),
    ]

    cli_only = await extension_service.search("toolchain", "terraform")
    assert cli_only["results"] == []
    unapproved_core = await extension_service.search("toolchain", "future")
    assert unapproved_core["results"] == []

    catalog = await extension_service.search("toolchain", "Node.js")
    assert catalog["results"][0]["ref"] == "node"
    assert catalog["results"][0]["backend"] == "core"


def test_dynamic_toolchain_install_accepts_only_matching_mise_core_refs(monkeypatch):
    from cyrene.extensions import service

    started = []

    class Tasks:
        def create(self, **kwargs):
            return {"id": "task-core", **kwargs}

        def start(self, task, manager, worker):
            started.append((task, manager, worker))

    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()
    spec = {
        "id": "dotnet", "name": "dotnet", "kind": "toolchain",
        "manager": "mise", "tool": "dotnet", "ref": "core:dotnet",
    }

    task = extension_service.start_install(
        "toolchain", "dotnet", {"ref": "core:dotnet", "spec": spec}
    )
    assert task["id"] == "task-core"
    assert started[0][1] == "mise"

    with pytest.raises(ValueError, match="unknown toolchain"):
        extension_service.start_install(
            "toolchain", "dotnet", {"ref": "aqua:example/dotnet", "spec": spec}
        )
    with pytest.raises(ValueError, match="unknown toolchain"):
        extension_service.start_install(
            "toolchain", "dotnet", {"ref": "core:ruby", "spec": spec}
        )


def _skill(tmp_path: Path, *, description: str = "Full private workflow") -> Path:
    root = tmp_path / "demo-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: Demo Skill\ndescription: {description}\n---\n\n# Instructions\nDo the complete workflow.\n",
        encoding="utf-8",
    )
    (root / "reference.md").write_text("Reference body", encoding="utf-8")
    return root


def test_skill_snapshot_progressive_load_and_resource_confinement(tmp_path, monkeypatch):
    from cyrene.learning import skills

    records = []
    monkeypatch.setattr(skills, "_SKILLS_DIR", tmp_path / "installed")
    monkeypatch.setattr(skills, "skill_settings_records", lambda: [dict(item) for item in records])
    monkeypatch.setattr(skills, "save_skill_settings_records", lambda value: records.__setitem__(slice(None), [dict(item) for item in value]))

    source = _skill(tmp_path)
    result = skills.install_skill_from_path(source)
    assert result["ok"] is True
    stored = Path(result["skill"]["stored_path"])
    assert stored != source
    assert stored.joinpath("SKILL.md").is_file()

    prompt = skills.build_skill_prompt_block()
    assert "Demo Skill" in prompt
    assert "Full private workflow" not in prompt
    assert "Do the complete workflow" not in prompt

    loaded = skills.load_skill(result["skill"]["id"])
    assert loaded is not None
    assert "Do the complete workflow" in loaded["instructions"]
    resource = skills.read_skill_resource(result["skill"]["id"], "reference.md")
    assert resource["content"] == "Reference body"
    assert skills.read_skill_resource(result["skill"]["id"], "../outside.md")["ok"] is False


def test_extension_skill_card_includes_full_markdown_and_file_directory(tmp_path, monkeypatch):
    from cyrene.extensions import service

    monkeypatch.setattr(service, "build_skills", lambda: [{
        "id": "demo-skill",
        "name": "Demo Skill",
        "desc": "Full private workflow",
        "stored_path": str(tmp_path / "demo-skill"),
        "entrypoint_name": "SKILL.md",
        "preview": "# Instructions\n\nDo the **complete** workflow.\n",
        "files": [
            {"path": "SKILL.md", "name": "SKILL.md", "size": 48},
            {"path": "references/guide.md", "name": "guide.md", "size": 24},
        ],
    }])
    monkeypatch.setattr(service.ExtensionService, "_system_observation", lambda *_args: None)
    from cyrene.tooling.backends import mcp_manager
    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [])
    monkeypatch.setattr(mcp_manager, "get_manager", lambda: type("Manager", (), {"get_server_status": lambda self: []})())

    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = type("Tasks", (), {"list": lambda self: []})()
    card = extension_service.list_extensions()["skills"][0]

    assert card["preview"] == "# Instructions\n\nDo the **complete** workflow.\n"
    assert card["entrypoint_name"] == "SKILL.md"
    assert [item["path"] for item in card["files"]] == ["SKILL.md", "references/guide.md"]


def test_extension_service_installs_local_skill_through_canonical_service(tmp_path, monkeypatch):
    from cyrene.extensions import service

    source = tmp_path / "demo-skill"
    source.mkdir()
    installed = []
    audits = []

    def install(path):
        installed.append(path)
        return {"ok": True, "skill": {"id": "demo-skill"}}

    monkeypatch.setattr(service, "install_skill_from_path", install)
    monkeypatch.setattr(service, "_audit", lambda *args, **kwargs: audits.append((args, kwargs)))

    extension_service = object.__new__(service.ExtensionService)
    result = extension_service.install_local_skill(source, actor="cli")

    assert result == {"ok": True, "skill": {"id": "demo-skill"}}
    assert installed == [source]
    assert audits[0][0][:3] == ("cli", "install.finish", "skill:demo-skill")
    with pytest.raises(ValueError, match="source path is required"):
        extension_service.install_local_skill("")


def test_extension_routes_cover_local_skill_path_upload_and_enabled_state(tmp_path, monkeypatch):
    from cyrene.extensions.application_service import (
        ExtensionApplicationService,
        ExtensionInstallInputService,
    )
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from route import extensions

    calls = []

    class Service:
        def install_local_skill(self, source_path, *, actor):
            path = Path(source_path)
            calls.append(("install", path, actor, path.exists()))
            return {"ok": True, "skill": {"id": "demo-skill"}}

        async def set_extension_enabled(self, kind, extension_id, enabled, *, actor):
            calls.append(("enabled", kind, extension_id, enabled, actor))
            return {"ok": True, "enabled": enabled}

    service = Service()
    application_service = ExtensionApplicationService(
        service,
        ExtensionInstallInputService(service, tmp_path),
        source_get=lambda **_kwargs: {},
        source_update=lambda body: body,
        audit_get=lambda _limit: [],
    )
    app = FastAPI()
    router = APIRouter()
    extensions.register_extension_routes(router, application_service)
    app.include_router(router)
    client = TestClient(app)

    local_path = tmp_path / "local-skill"
    local_path.mkdir()
    response = client.post(
        "/api/extensions/skills/install",
        json={"path": str(local_path)},
    )
    assert response.status_code == 200
    response = client.post(
        "/api/extensions/skills/install-upload",
        files={"file": ("SKILL.md", b"# Demo Skill", "text/markdown")},
    )
    assert response.status_code == 200
    uploaded_path = calls[1][1]
    assert calls[1][3] is True
    assert not uploaded_path.exists()
    response = client.post(
        "/api/extensions/skill/demo-skill/enabled",
        json={"enabled": False},
    )
    assert response.json() == {"ok": True, "enabled": False}
    assert calls[2] == ("enabled", "skill", "demo-skill", False, "user")


def test_legacy_skill_panel_and_api_are_removed_from_active_sources():
    root = Path(__file__).resolve().parents[1]
    frontend = workbench_settings_source()
    cli = root.joinpath("src/cyrene/cli_chat.py").read_text(encoding="utf-8")
    registry = root.joinpath("src/route/registry.py").read_text(encoding="utf-8")

    assert not root.joinpath("src/route/skills.py").exists()
    assert "LegacySkillsPanel" not in frontend
    assert "/api/skills" not in frontend
    assert "/api/skills" not in cli
    assert "register_skill_routes" not in registry


def test_skill_directory_and_archive_reject_links_and_expansion(tmp_path, monkeypatch):
    from cyrene.learning import skills

    root = _skill(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    root.joinpath("escape").symlink_to(outside)
    assert "symbolic link" in str(skills.validate_skill_directory(root))

    archive = tmp_path / "oversized.zip"
    import zipfile
    monkeypatch.setattr(skills, "_MAX_SKILL_TREE_BYTES", 32)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("SKILL.md", "# Skill\n" + "x" * 16)
        handle.writestr("reference.md", "r" * 24)
    assert "expands beyond" in str(skills.validate_skill_archive(archive))


def test_extension_environment_is_isolated_and_system_path_wins(tmp_path, monkeypatch):
    from cyrene.extensions import service

    monkeypatch.setattr(service, "_ROOT", tmp_path / "extensions")
    monkeypatch.setattr(service, "_MISE_DATA", tmp_path / "extensions" / "mise")
    monkeypatch.setattr(service, "_MISE_CONFIG", tmp_path / "extensions" / "mise-config")
    monkeypatch.setattr(service, "_MISE_CACHE", tmp_path / "cache" / "mise")
    monkeypatch.setattr(service, "_UV_PYTHON_DIR", tmp_path / "extensions" / "python")
    monkeypatch.setattr(service, "_UV_BIN_DIR", tmp_path / "extensions" / "python-bin")
    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {"verify_signatures": True})
    env = service.extension_environment()
    assert Path(env["MISE_DATA_DIR"]).is_relative_to(tmp_path)
    assert Path(env["UV_PYTHON_INSTALL_DIR"]).is_relative_to(tmp_path)
    assert env["MISE_AQUA_SLSA"] == "true"
    assert env["MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES"] == "none"


def test_agent_process_environment_appends_managed_paths_without_installer_token(tmp_path, monkeypatch):
    from cyrene.extensions import service

    mise_shims = tmp_path / "extensions" / "mise" / "shims"
    uv_bin = tmp_path / "extensions" / "python-bin"
    mise_shims.mkdir(parents=True)
    uv_bin.mkdir(parents=True)
    monkeypatch.setattr(service, "_ROOT", tmp_path / "extensions")
    monkeypatch.setattr(service, "_MISE_DATA", tmp_path / "extensions" / "mise")
    monkeypatch.setattr(service, "_MISE_CONFIG", tmp_path / "extensions" / "mise-config")
    monkeypatch.setattr(service, "_MISE_CACHE", tmp_path / "cache" / "mise")
    monkeypatch.setattr(service, "_UV_PYTHON_DIR", tmp_path / "extensions" / "python")
    monkeypatch.setattr(service, "_UV_BIN_DIR", uv_bin)
    monkeypatch.setattr(service, "_TEX_DIR", tmp_path / "extensions" / "tex")
    monkeypatch.setattr(service, "_AGENT_BIN_DIR", tmp_path / "extensions" / "agents" / "bin")
    monkeypatch.setattr(service, "_bundled_binary", lambda _name: None)
    settings = {
        "extension_clis": [{
            "id": "probe",
            "source": {"type": "mise", "ref": "probe"},
            "spec": {"tool": "probe"},
        }],
        "extension_toolchains": [{"id": "python", "source": {"type": "uv"}}],
    }
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {
        "verify_signatures": True,
        "github_token": "extension-center-secret",
    })

    base_path = os.pathsep.join([str(tmp_path / "system-bin"), str(tmp_path / "user-bin")])
    env = service.agent_process_environment({
        "PATH": base_path,
        "npm_config_prefix": "/electron/npm",
    })

    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / "system-bin"),
        str(tmp_path / "user-bin"),
        str(uv_bin),
        str(mise_shims),
    ]
    assert env["MISE_DATA_DIR"] == str(tmp_path / "extensions" / "mise")
    assert "GITHUB_TOKEN" not in env
    assert "npm_config_prefix" not in env


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="probe uses a POSIX executable shim")
async def test_bash_can_run_a_cyrene_managed_mise_shim(tmp_path, monkeypatch):
    from cyrene.agent.context import bind_run_context
    from cyrene.extensions import service
    from cyrene.tool_impl.core import bash as bash_tool

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mise_shims = tmp_path / "extensions" / "mise" / "shims"
    mise_shims.mkdir(parents=True)
    probe = mise_shims / "cyrene-managed-probe"
    probe.write_text("#!/bin/sh\nprintf managed-command-visible\n", encoding="utf-8")
    probe.chmod(0o755)

    monkeypatch.setattr(service, "_ROOT", tmp_path / "extensions")
    monkeypatch.setattr(service, "_MISE_DATA", tmp_path / "extensions" / "mise")
    monkeypatch.setattr(service, "_MISE_CONFIG", tmp_path / "extensions" / "mise-config")
    monkeypatch.setattr(service, "_MISE_CACHE", tmp_path / "cache" / "mise")
    monkeypatch.setattr(service, "_UV_PYTHON_DIR", tmp_path / "extensions" / "python")
    monkeypatch.setattr(service, "_UV_BIN_DIR", tmp_path / "extensions" / "python-bin")
    monkeypatch.setattr(service, "_TEX_DIR", tmp_path / "extensions" / "tex")
    monkeypatch.setattr(service, "_AGENT_BIN_DIR", tmp_path / "extensions" / "agents" / "bin")
    monkeypatch.setattr(service, "_bundled_binary", lambda _name: None)
    settings = {
        "extension_clis": [{
            "id": "cyrene-managed-probe",
            "source": {"type": "mise", "ref": "cyrene-managed-probe"},
            "spec": {"tool": "cyrene-managed-probe"},
        }],
    }
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {"verify_signatures": True})

    with bind_run_context(workspace_dir=str(workspace), temporary_full_access=True):
        result = await bash_tool._tool_bash(
            {"command": "cyrene-managed-probe", "timeout_ms": 5000},
            None,
            0,
            "",
            {},
        )

    payload = json.loads(result)
    assert payload["exit_code"] == 0
    assert payload["stdout"] == "managed-command-visible"


def test_disabled_managed_mise_extensions_are_hidden_from_the_agent_environment(tmp_path, monkeypatch):
    from cyrene.extensions import service

    mise_shims = tmp_path / "extensions" / "mise" / "shims"
    mise_shims.mkdir(parents=True)
    settings = {
        "extension_clis": [{
            "id": "ripgrep",
            "source": {"type": "mise", "ref": "github:BurntSushi/ripgrep"},
            "spec": {"tool": "ripgrep"},
        }],
        "extension_toolchains": [{
            "id": "node",
            "source": {"type": "mise", "ref": "node"},
            "spec": {"tool": "node"},
        }],
        "extension_enabled": {
            "cli:ripgrep": False,
            "toolchain:node": True,
        },
    }
    monkeypatch.setattr(service, "_ROOT", tmp_path / "extensions")
    monkeypatch.setattr(service, "_MISE_DATA", tmp_path / "extensions" / "mise")
    monkeypatch.setattr(service, "_MISE_CONFIG", tmp_path / "extensions" / "mise-config")
    monkeypatch.setattr(service, "_MISE_CACHE", tmp_path / "cache" / "mise")
    monkeypatch.setattr(service, "_UV_PYTHON_DIR", tmp_path / "extensions" / "python")
    monkeypatch.setattr(service, "_UV_BIN_DIR", tmp_path / "extensions" / "python-bin")
    monkeypatch.setattr(service, "_TEX_DIR", tmp_path / "extensions" / "tex")
    monkeypatch.setattr(service, "_AGENT_BIN_DIR", tmp_path / "extensions" / "agents" / "bin")
    monkeypatch.setattr(service, "_bundled_binary", lambda _name: None)
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {"verify_signatures": True})

    env = service.agent_process_environment({"PATH": str(tmp_path / "system-bin")})

    assert env["PATH"].split(os.pathsep)[-1] == str(mise_shims)
    assert env["MISE_DISABLE_TOOLS"] == "github:BurntSushi/ripgrep"


@pytest.mark.asyncio
async def test_cli_and_toolchain_activation_is_persisted_and_reflected_in_cards(tmp_path, monkeypatch):
    from cyrene.extensions import service

    records = {
        "extension_clis": [{
            "id": "ripgrep",
            "kind": "cli",
            "ownership": "cyrene",
            "observed_state": "installed",
            "version": "14.1.1",
            "path": "/managed/rg",
            "source": {"type": "mise", "ref": "github:BurntSushi/ripgrep"},
            "health": "healthy",
            "spec": dict(service.CURATED_CLIS["ripgrep"]),
        }],
        "extension_toolchains": [{
            "id": "node",
            "kind": "toolchain",
            "ownership": "cyrene",
            "observed_state": "installed",
            "version": "24.0.0",
            "path": "/managed/node",
            "source": {"type": "mise", "ref": "node"},
            "health": "healthy",
            "spec": dict(service.TOOLCHAINS["node"]),
        }],
    }
    saved = dict(records)
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.ExtensionService, "_system_observation", lambda *_args: None)
    extension_service = object.__new__(service.ExtensionService)

    for kind, extension_id, spec, record in (
        ("cli", "ripgrep", service.CURATED_CLIS["ripgrep"], records["extension_clis"][0]),
        ("toolchain", "node", service.TOOLCHAINS["node"], records["extension_toolchains"][0]),
    ):
        initial = extension_service._extension_card(extension_id, spec, record)
        assert initial["enabled"] is True
        assert {"enable", "disable"}.issubset(initial["capabilities"])

        result = await extension_service.set_extension_enabled(kind, extension_id, False)
        assert result == {"ok": True, "enabled": False}
        disabled = extension_service._extension_card(extension_id, spec, record)
        assert disabled["enabled"] is False
        assert disabled["desired_state"] == "disabled"

        await extension_service.set_extension_enabled(kind, extension_id, True)
        assert extension_service._extension_card(extension_id, spec, record)["enabled"] is True

    assert saved["extension_enabled"] == {
        "cli:ripgrep": True,
        "toolchain:node": True,
    }

    dynamic_record = {
        "id": "rtk", "kind": "cli", "ownership": "cyrene",
        "observed_state": "installed", "version": "0.28.2",
        "path": "/managed/rtk", "source": {"type": "mise", "ref": "aqua:rtk-ai/rtk"},
        "health": "healthy", "spec": {"name": "rtk", "kind": "cli", "tool": "rtk"},
    }
    dynamic_card = extension_service._extension_card("rtk", dynamic_record["spec"], dynamic_record)
    assert "bind_system" not in dynamic_card["capabilities"]
    assert "bind_system" in extension_service._extension_card(
        "ripgrep", service.CURATED_CLIS["ripgrep"], records["extension_clis"][0]
    )["capabilities"]


def test_detected_system_extension_hides_binding_and_install_actions(monkeypatch):
    from cyrene.extensions import service

    system = {
        "path": "/usr/local/texlive/bin/pdflatex",
        "version": "pdfTeX 3.14 (TeX Live 2025)",
        "ownership": "system",
        "observed_state": "installed",
        "health": "healthy",
        "source": {"type": "system", "binding": "detected"},
    }
    monkeypatch.setattr(service.ExtensionService, "_system_observation", lambda *_args: system)
    extension_service = object.__new__(service.ExtensionService)

    card = extension_service._extension_card("tex", service.TOOLCHAINS["tex"])

    assert "bind_system" not in card["capabilities"]
    assert "install" not in card["capabilities"]
    assert {"enable", "disable"}.issubset(card["capabilities"])


@pytest.mark.asyncio
async def test_bundled_uv_has_the_same_activation_control(tmp_path, monkeypatch):
    from cyrene.extensions import service

    uv = tmp_path / "runtime-tools" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\necho uv 0.11.28\n", encoding="utf-8")
    uv.chmod(0o755)
    saved = {}
    monkeypatch.setattr(service, "_bundled_binary", lambda name: uv if name == "uv" else None)
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = type("Tasks", (), {"list": lambda self: []})()

    result = await extension_service.set_extension_enabled("toolchain", "uv", False)

    assert result == {"ok": True, "enabled": False}
    assert saved["extension_enabled"] == {"toolchain:uv": False}
    assert str(uv.parent) not in service.agent_extension_paths()

    monkeypatch.setattr(service, "build_skills", lambda: [])
    from cyrene.tooling.backends import mcp_manager
    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [])
    monkeypatch.setattr(mcp_manager, "get_manager", lambda: type("Manager", (), {"get_server_status": lambda self: []})())
    monkeypatch.setattr(service.ExtensionService, "_system_observation", lambda *_args: None)
    uv_card = extension_service.list_extensions()["infrastructure"]["uv"]
    assert uv_card["enabled"] is False
    assert uv_card["desired_state"] == "disabled"
    assert {"enable", "disable"}.issubset(uv_card["capabilities"])


@pytest.mark.asyncio
async def test_skill_and_mcp_activation_use_their_native_lifecycle(monkeypatch):
    from cyrene.extensions import service
    from cyrene.tooling.backends import mcp_manager

    skill_calls = []
    servers = [{"name": "demo", "transport": "streamable_http", "url": "https://example.com/mcp", "enabled": True}]
    saved_servers = []
    restarts = []

    monkeypatch.setattr(service, "set_skill_enabled", lambda extension_id, enabled: skill_calls.append((extension_id, enabled)) or True)
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [dict(item) for item in servers])
    monkeypatch.setattr(mcp_manager, "save_mcp_servers", lambda value: saved_servers.append([dict(item) for item in value]))

    async def restart():
        restarts.append(True)

    monkeypatch.setattr(mcp_manager, "restart_mcp", restart)
    extension_service = object.__new__(service.ExtensionService)

    assert await extension_service.set_extension_enabled("skill", "demo-skill", False) == {"ok": True, "enabled": False}
    assert skill_calls == [("demo-skill", False)]
    assert await extension_service.set_extension_enabled("mcp", "demo", False) == {"ok": True, "enabled": False}
    assert saved_servers[-1][0]["enabled"] is False
    assert restarts == [True]


@pytest.mark.asyncio
async def test_agent_extension_activation_still_passes_through_the_reviewer(monkeypatch):
    from cyrene.tool_impl.extensions import manage_extensions

    reviews = []
    calls = []

    class FakeService:
        async def set_extension_enabled(self, kind, extension_id, enabled, *, actor):
            calls.append((kind, extension_id, enabled, actor))
            return {"ok": True, "enabled": enabled}

    async def review(operation, target, arguments):
        reviews.append((operation, target, dict(arguments)))
        return None

    monkeypatch.setattr(manage_extensions, "get_extension_service", lambda: FakeService())
    monkeypatch.setattr(manage_extensions, "_review", review)

    result = json.loads(await manage_extensions._tool_manage_extensions({
        "action": "disable",
        "kind": "cli",
        "extension_id": "ripgrep",
    }))

    assert result == {"ok": True, "enabled": False}
    assert reviews == [("disable", "cli:ripgrep", {
        "action": "disable",
        "kind": "cli",
        "extension_id": "ripgrep",
    })]
    assert calls == [("cli", "ripgrep", False, "agent")]


def test_extension_switch_is_rendered_only_in_expanded_details_and_uses_unified_endpoint():
    frontend = workbench_settings_source()
    styles = workbench_style_source()
    root = Path(__file__).resolve().parents[1]
    tool_definitions = root.joinpath("src/cyrene/tooling/native_definitions.py").read_text(encoding="utf-8")

    details_index = frontend.index('expanded && React.createElement("div", { className: "wb-extension-details" }')
    switch_index = frontend.index('canToggle && React.createElement("div", { className: "wb-extension-enabled-row" }')
    assert switch_index > details_index
    assert '"/api/extensions/" + encodeURIComponent(item.kind)' in frontend
    assert 'className: "wb-extension-enabled-row"' in frontend
    assert ".wb-extension-enabled-row" in styles
    assert "'enable', 'disable'" in tool_definitions
    assert 'canUseLocalProgram = (item.capabilities || []).indexOf("bind_system") >= 0' in frontend
    assert '(item.kind === "toolchain" || item.kind === "cli") && item.ownership !== "builtin"' not in frontend


def test_mcp_manual_fallback_ui_is_actionable_and_uses_structured_arguments():
    frontend = workbench_settings_source()
    assert 'if (item.installable === false) configureManualMcp(item); else installSearchResult(item);' in frontend
    assert 'disabled: remoteLoading || item.installable === false' not in frontend
    assert 'manualMcp.args.split(/\\r?\\n/)' in frontend
    assert 'setInstallOpen(false); tell(t("settings.extensionInstallStarted")' not in frontend


def test_extension_install_errors_are_localized_compact_and_expire():
    frontend = workbench_settings_source()
    styles = workbench_style_source()
    translations = workbench_i18n_source()

    assert "function extensionTaskErrorContent(task, t)" in frontend
    assert "function extensionTaskIsVisible(task, now)" in frontend
    assert "now - finishedAt < 30000" in frontend
    assert 'React.createElement("details", null' in frontend
    assert ".wb-extension-task-error pre" in styles
    assert translations.count('"settings.extensionTaskError.dependency_conflict.title"') == 2


def test_extension_dependency_conflicts_have_a_stable_reason_code():
    from cyrene.extensions.service import _extension_error_reason

    error = RuntimeError("package does not satisfy Python >=3.10 and requirements are unsatisfiable")
    assert _extension_error_reason(error) == "dependency_conflict"


def test_mcp_details_render_discovered_tool_names_and_descriptions():
    frontend = workbench_settings_source()
    translations = workbench_i18n_source()

    assert 'className: "wb-extension-mcp-tools"' in frontend
    assert "item.tools.map(function (tool)" in frontend
    assert "tool.description" in frontend
    assert translations.count('"settings.extensionMcpTools"') == 2


def test_cli_hook_integration_is_a_compact_localized_detail_action():
    frontend = workbench_settings_source()
    styles = workbench_style_source()
    translations = workbench_i18n_source()

    assert 'className: "wb-extension-hook-copy"' in frontend
    assert ".wb-extension-hook-action .wb-btn" in styles
    assert '"settings.extensionHookTitle": "Automatic integration"' in translations
    assert '"settings.extensionHookTitle": "自动接入"' in translations
    assert '"settings.extensionConfigureHook": "让 Agent 配置"' in translations


def test_install_task_store_redacts_nested_secrets(tmp_path, monkeypatch):
    from cyrene.extensions import service

    monkeypatch.setattr(service, "_TASK_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(service, "_STAGING_DIR", tmp_path / "staging")
    store = service.InstallTaskStore()
    task = store.create(
        kind="mcp",
        extension_id="example",
        action="install",
        actor="agent",
        request={"config": {"headers": {"Authorization": "Bearer secret"}, "env": {"API_KEY": "secret"}}},
    )
    encoded = json.dumps(task)
    assert "Bearer secret" not in encoded
    assert '"API_KEY": "[redacted]"' in encoded
    store.update(task["id"], result={"config": {"Authorization": "Bearer returned-secret"}})
    assert "returned-secret" not in json.dumps(store.get(task["id"]))


def test_install_task_store_recovers_interrupted_tasks_and_staging(tmp_path, monkeypatch):
    from cyrene.extensions import service

    task_file = tmp_path / "tasks.json"
    staging = tmp_path / "staging"
    staging.joinpath("abandoned").mkdir(parents=True)
    staging.joinpath("abandoned", "partial.bin").write_bytes(b"partial")
    task_file.write_text(json.dumps({"task-1": {"id": "task-1", "status": "running"}}), encoding="utf-8")
    monkeypatch.setattr(service, "_TASK_FILE", task_file)
    monkeypatch.setattr(service, "_STAGING_DIR", staging)

    store = service.InstallTaskStore()

    assert store.get("task-1")["status"] == "interrupted"
    assert not any(staging.iterdir())


def test_verified_tar_allows_internal_links_and_rejects_escaping_links(tmp_path):
    from cyrene.extensions.service import _extract_verified_tar

    safe_archive = tmp_path / "safe.tar.xz"
    with tarfile.open(safe_archive, "w:xz") as handle:
        payload = b"binary"
        target = tarfile.TarInfo("TinyTeX/bin/pdftex")
        target.size = len(payload)
        handle.addfile(target, io.BytesIO(payload))
        link = tarfile.TarInfo("TinyTeX/bin/pdflatex")
        link.type = tarfile.SYMTYPE
        link.linkname = "pdftex"
        handle.addfile(link)
    destination = tmp_path / "safe"
    destination.mkdir()
    _extract_verified_tar(safe_archive, destination)
    assert destination.joinpath("TinyTeX/bin/pdflatex").resolve().read_bytes() == b"binary"

    unsafe_archive = tmp_path / "unsafe.tar.xz"
    with tarfile.open(unsafe_archive, "w:xz") as handle:
        link = tarfile.TarInfo("TinyTeX/bin/pdflatex")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../outside"
        handle.addfile(link)
    unsafe_destination = tmp_path / "unsafe"
    unsafe_destination.mkdir()
    with pytest.raises(RuntimeError, match="unsafe link"):
        _extract_verified_tar(unsafe_archive, unsafe_destination)


def test_manual_binding_records_but_never_mutates_executable(tmp_path, monkeypatch):
    from cyrene.extensions import service

    executable = tmp_path / "node"
    executable.write_text("#!/bin/sh\necho v22.0.0\n", encoding="utf-8")
    executable.chmod(0o755)
    saved = {}
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(service, "_AUDIT_FILE", tmp_path / "audit.jsonl")
    extension_service = object.__new__(service.ExtensionService)
    result = extension_service.bind_system_executable("node", str(executable))
    assert result["ok"] is True
    assert executable.is_file()
    assert saved["extension_system_bindings"]["node"] == str(executable.resolve())
    extension_service.unbind_system_executable("node")
    assert executable.is_file()
    assert "node" not in saved["extension_system_bindings"]


@pytest.mark.asyncio
async def test_environment_list_returns_only_installed_compact_metadata(monkeypatch):
    from cyrene.tool_impl.extensions import list_environment

    uv_enabled = True

    class FakeService:
        def list_extensions(self):
            return {
                "skills": [{
                    "id": "ocr", "kind": "skill", "name": "OCR",
                    "description": "Read images", "observed_state": "installed",
                    "desired_state": "enabled", "version": "snapshot",
                    "ownership": "cyrene", "health": "healthy",
                    "path": "/private/skill/path",
                }],
                "mcp": [{
                    "id": "disabled-server", "kind": "mcp", "name": "Disabled MCP",
                    "description": "Hidden server", "observed_state": "installed",
                    "desired_state": "disabled", "enabled": False,
                    "ownership": "cyrene", "health": "disconnected",
                }],
                "cli": [{
                    "id": "ripgrep", "kind": "cli", "name": "ripgrep",
                    "description": "Fast search", "observed_state": "missing",
                    "ownership": "none", "health": "missing",
                }],
                "toolchains": [
                    {
                        "id": "python", "kind": "toolchain", "name": "Python",
                        "description": "Runtime", "observed_state": "installed",
                        "ownership": "system", "health": "healthy", "version": "3.14",
                        "source": {"type": "system", "binding": "detected"},
                    },
                    {
                        "id": "node", "kind": "toolchain", "name": "Node.js",
                        "description": "Disabled runtime", "observed_state": "installed",
                        "desired_state": "disabled", "enabled": False,
                        "ownership": "system", "health": "healthy", "version": "24",
                    },
                ],
                "infrastructure": {
                    "uv": {
                        "id": "uv", "kind": "toolchain", "name": "uv",
                        "description": "Python package manager",
                        "observed_state": "installed",
                        "desired_state": "enabled" if uv_enabled else "disabled",
                        "enabled": uv_enabled, "ownership": "builtin",
                        "health": "healthy", "version": "0.11.28",
                    },
                },
            }

    monkeypatch.setattr(list_environment, "get_extension_service", lambda: FakeService())
    payload = json.loads(await list_environment._tool_list_environment({"kind": "all"}))

    assert payload["ok"] is True
    assert [item["id"] for item in payload["items"]] == ["python", "uv"]
    assert all("path" not in item for item in payload["items"])
    assert payload["items"][0]["source"] == {"type": "system", "binding": "detected"}

    uv_enabled = False
    hidden = json.loads(await list_environment._tool_list_environment({"kind": "all"}))
    assert [item["id"] for item in hidden["items"]] == ["python"]

    excluded = json.loads(await list_environment._tool_list_environment({"kind": "skill"}))
    assert excluded == {"ok": False, "error": "unsupported environment kind: skill"}


@pytest.mark.asyncio
async def test_environment_search_returns_review_ready_requests_and_partial_errors(monkeypatch):
    from cyrene.tool_impl.extensions import search_environment

    searched_kinds = []

    class FakeService:
        def list_extensions(self):
            return {
                "skills": [], "mcp": [], "toolchains": [],
                "cli": [{
                    "id": "fd", "kind": "cli", "name": "fd",
                    "observed_state": "installed", "desired_state": "disabled",
                    "enabled": False, "ownership": "cyrene",
                }],
                "infrastructure": {"uv": {
                    "id": "uv", "kind": "toolchain", "name": "uv",
                    "observed_state": "installed", "desired_state": "disabled",
                    "enabled": False, "ownership": "builtin",
                }},
            }

        async def search(self, kind, query, **_kwargs):
            searched_kinds.append(kind)
            assert query == "search"
            if kind == "mcp":
                raise RuntimeError("registry unavailable")
            if kind == "cli":
                return {"results": [
                    {
                        "id": "ripgrep", "name": "ripgrep", "kind": "cli",
                        "description": "Fast search", "ref": "aqua:BurntSushi/ripgrep",
                        "version": "latest", "source": "aqua:BurntSushi/ripgrep",
                        "backend": "aqua", "verified": True,
                    },
                    {
                        "id": "fd", "name": "fd", "kind": "cli",
                        "description": "Disabled search tool", "ref": "aqua:sharkdp/fd",
                        "version": "latest", "source": "aqua:sharkdp/fd",
                        "backend": "aqua", "verified": True,
                    },
                ], "next_cursor": ""}
            return {"results": [{
                "id": "UV", "name": "uv", "kind": "toolchain",
                "description": "Disabled built-in", "version": "latest",
                "ref": "uv", "verified": True,
            }], "next_cursor": ""}

    monkeypatch.setattr(search_environment, "get_extension_service", lambda: FakeService())
    payload = json.loads(await search_environment._tool_search_environment({"query": "search"}))

    assert payload["ok"] is True
    assert payload["source_errors"] == {"mcp": "registry unavailable"}
    assert [item["id"] for item in payload["results"]] == ["ripgrep"]
    result = payload["results"][0]
    assert result["id"] == "ripgrep"
    assert result["install_request"]["ref"] == "aqua:BurntSushi/ripgrep"
    assert result["install_request"]["version"] == "latest"
    assert "action=install" in payload["next_step"]
    assert searched_kinds == ["toolchain", "cli", "mcp"]


@pytest.mark.asyncio
async def test_environment_search_does_not_offer_reinstall_for_system_extension(monkeypatch):
    from cyrene.tool_impl.extensions import search_environment

    class FakeService:
        def list_extensions(self):
            return {"skills": [], "mcp": [], "cli": [], "toolchains": [{
                "id": "tex", "kind": "toolchain", "name": "TeX",
                "observed_state": "installed", "desired_state": "enabled",
                "enabled": True, "ownership": "system", "health": "healthy",
                "source": {"type": "system", "binding": "detected"},
            }]}

        async def search(self, kind, _query, **_kwargs):
            assert kind == "toolchain"
            return {"results": [{
                "id": "tex", "name": "TeX", "kind": "toolchain",
                "manager": "tinytex", "version": "latest", "verified": True,
            }], "next_cursor": ""}

    monkeypatch.setattr(search_environment, "get_extension_service", lambda: FakeService())
    payload = json.loads(await search_environment._tool_search_environment({"kind": "toolchain", "query": "tex"}))

    assert payload["results"][0]["installed"] is True
    assert payload["results"][0]["installable"] is False
    assert payload["results"][0]["install_request"] is None

    excluded = json.loads(await search_environment._tool_search_environment({
        "query": "ocr",
        "kind": "skill",
    }))
    assert excluded == {"ok": False, "error": "unsupported environment kind: skill"}


@pytest.mark.asyncio
async def test_mcp_registry_keeps_pypi_packages_and_refreshes_stale_version(monkeypatch):
    from cyrene.extensions import service

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            if "pypi.org" in url:
                return Response({"info": {"version": "0.5.0"}})
            return Response({"servers": [{"server": {
                "name": "io.demo/pypi-mcp", "version": "0.3.1",
                "packages": [{"registryType": "pypi", "identifier": "pypi-mcp", "version": "0.3.1"}],
            }}]})

    monkeypatch.setattr(service, "source_settings", lambda **_kwargs: {"mcp_registry_url": "https://registry.example"})
    monkeypatch.setattr(service.httpx, "AsyncClient", Client)
    extension_service = object.__new__(service.ExtensionService)

    result = await extension_service._search_mcp("pypi")
    item = result["results"][0]
    assert item["installable"] is True
    assert item["installable_packages"][0]["registryType"] == "pypi"
    assert item["installable_packages"][0]["version"] == "0.5.0"
    assert item["registry_version"] == "0.3.1"
    assert item["package_latest_version"] == "0.5.0"
    assert item["resolved_version"] == "0.5.0"
    assert item["version_status"] == "registry_stale"


@pytest.mark.asyncio
async def test_environment_search_returns_machine_readable_mcp_fallback(monkeypatch):
    from cyrene.tool_impl.extensions import search_environment

    class FakeService:
        def list_extensions(self):
            return {"skills": [], "mcp": [], "cli": [], "toolchains": []}

        async def search(self, _kind, _query, **_kwargs):
            return {"results": [{
                "id": "demo", "name": "Demo", "kind": "mcp", "version": "1.0.0",
                "installable": False, "reason_code": "unsupported_registry_type",
                "fallback_request": {"action": "install_local_mcp", "kind": "mcp", "extension_id": "demo", "request": {"config": {"name": "demo", "transport": "stdio", "command": "", "args": [], "version": "1.0.0", "enabled": True}}},
            }], "next_cursor": ""}

    monkeypatch.setattr(search_environment, "get_extension_service", lambda: FakeService())
    payload = json.loads(await search_environment._tool_search_environment({"kind": "mcp", "query": "demo"}))
    item = payload["results"][0]
    assert item["installable"] is False
    assert item["reason_code"] == "unsupported_registry_type"
    assert item["fallback_request"]["action"] == "install_local_mcp"
    assert "Never guess" in payload["next_step"]


@pytest.mark.asyncio
async def test_pypi_mcp_install_uses_bundled_uv_and_fixed_version(tmp_path, monkeypatch):
    from cyrene.extensions import service
    from cyrene.tooling.backends import mcp_manager

    uv = tmp_path / "uv"
    uv.write_text("uv", encoding="utf-8")
    uv.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    commands = []
    saved = []

    class Tasks:
        def update(self, *_args, **_kwargs):
            return None

    class Manager:
        def get_server_status(self):
            return [{"name": "demo", "status": "connected", "tool_count": 1}]

    async def run_manager(_self, _task_id, command, **_kwargs):
        commands.append(command)
        if command[1:3] == ["tool", "install"]:
            executable = bin_dir / "demo-mcp"
            executable.write_text("#!/bin/sh", encoding="utf-8")
            executable.chmod(0o755)
        return "", ""

    monkeypatch.setattr(service, "_UV_BIN_DIR", bin_dir)
    monkeypatch.setattr(service, "_bundled_binary", lambda name: uv if name == "uv" else None)
    monkeypatch.setattr(service, "extension_environment", lambda: {})
    monkeypatch.setattr(service.ExtensionService, "_run_manager", run_manager)
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [])
    monkeypatch.setattr(mcp_manager, "save_mcp_servers", lambda value: saved.append(value))
    monkeypatch.setattr(mcp_manager, "restart_mcp", lambda: _async_result(None))
    monkeypatch.setattr(mcp_manager, "get_manager", lambda: Manager())
    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()

    config = await extension_service._install_mcp("task", "demo", {
        "version": "1.2.3",
        "package": {"registryType": "pypi", "identifier": "demo-mcp", "version": "1.2.3"},
    }, "agent")

    assert commands[0][-1] == "demo-mcp==1.2.3"
    assert config["command"] == str((bin_dir / "demo-mcp").resolve())
    assert config["source"]["registry"] == "pypi"
    assert saved[-1][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_manage_extensions_exposes_local_mcp_action(monkeypatch):
    from cyrene.tool_impl.extensions import manage_extensions

    started = []

    class FakeService:
        def start_install(self, kind, extension_id, request, *, actor):
            started.append((kind, extension_id, request, actor))
            return {"id": "task"}

    monkeypatch.setattr(manage_extensions, "get_extension_service", lambda: FakeService())
    monkeypatch.setattr(manage_extensions, "_review", lambda *_args, **_kwargs: _async_result(None))
    config = {"name": "demo", "transport": "stdio", "command": "/bin/demo", "args": [], "version": "1.0.0"}
    payload = json.loads(await manage_extensions._tool_manage_extensions({
        "action": "install_local_mcp", "kind": "mcp", "extension_id": "demo", "request": {"config": config},
    }))
    assert payload["ok"] is True
    assert started == [("mcp", "demo", {"config": config}, "agent")]


@pytest.mark.asyncio
async def test_environment_discovery_follows_real_extension_activation_state(tmp_path, monkeypatch):
    from cyrene.extensions import service
    from cyrene.tool_impl.extensions import list_environment, search_environment
    from cyrene.tooling.backends import mcp_manager

    uv = tmp_path / "runtime-tools" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\necho uv 0.11.28\n", encoding="utf-8")
    uv.chmod(0o755)
    settings = {
        "extension_clis": [{
            "id": "ripgrep", "kind": "cli", "ownership": "cyrene",
            "observed_state": "installed", "version": "14.1.1",
            "path": "/managed/rg", "source": {"type": "mise", "ref": "github:BurntSushi/ripgrep"},
            "health": "healthy", "spec": dict(service.CURATED_CLIS["ripgrep"]),
        }],
        "extension_toolchains": [{
            "id": "node", "kind": "toolchain", "ownership": "cyrene",
            "observed_state": "installed", "version": "24.0.0",
            "path": "/managed/node", "source": {"type": "mise", "ref": "node"},
            "health": "healthy", "spec": dict(service.TOOLCHAINS["node"]),
        }],
    }
    servers = [{
        "name": "demo-mcp", "transport": "streamable_http",
        "url": "https://example.com/mcp", "enabled": True,
        "source": {"type": "mcp-registry", "id": "demo-mcp"},
    }]

    monkeypatch.setattr(service, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: settings.__setitem__(key, value))
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "build_skills", lambda: [])
    monkeypatch.setattr(service, "_bundled_binary", lambda name: uv if name == "uv" else None)
    monkeypatch.setattr(service.ExtensionService, "_system_observation", lambda *_args: None)
    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [dict(item) for item in servers])

    def save_servers(value):
        servers[:] = [dict(item) for item in value]

    monkeypatch.setattr(mcp_manager, "save_mcp_servers", save_servers)
    monkeypatch.setattr(
        mcp_manager,
        "get_manager",
        lambda: type("Manager", (), {"get_server_status": lambda self: []})(),
    )

    async def restart_mcp():
        return None

    monkeypatch.setattr(mcp_manager, "restart_mcp", restart_mcp)
    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = type("Tasks", (), {"list": lambda self: []})()

    async def search_catalog(kind, _query, **_kwargs):
        candidates = {
            "cli": [{"id": "RIPGREP", "name": "ripgrep", "ref": "github:BurntSushi/ripgrep"}],
            "toolchain": [{"id": "Node", "name": "Node.js", "ref": "node"}],
            "mcp": [{
                "id": "DEMO-MCP", "name": "Demo MCP", "version": "1.0.0",
                "installable_remotes": [{"url": "https://example.com/mcp"}],
            }],
        }
        return {"results": candidates[kind], "next_cursor": ""}

    extension_service.search = search_catalog
    monkeypatch.setattr(list_environment, "get_extension_service", lambda: extension_service)
    monkeypatch.setattr(search_environment, "get_extension_service", lambda: extension_service)

    initial = json.loads(await list_environment._tool_list_environment({"kind": "all"}))
    assert {item["id"] for item in initial["items"]} == {"ripgrep", "node", "uv", "demo-mcp"}

    for kind, extension_id in (
        ("cli", "ripgrep"),
        ("toolchain", "node"),
        ("toolchain", "uv"),
        ("mcp", "demo-mcp"),
    ):
        await extension_service.set_extension_enabled(kind, extension_id, False)

    hidden_list = json.loads(await list_environment._tool_list_environment({"kind": "all"}))
    hidden_search = json.loads(await search_environment._tool_search_environment({"query": "demo"}))
    assert hidden_list["items"] == []
    assert hidden_search["results"] == []

    for kind, extension_id in (
        ("cli", "ripgrep"),
        ("toolchain", "node"),
        ("toolchain", "uv"),
        ("mcp", "demo-mcp"),
    ):
        await extension_service.set_extension_enabled(kind, extension_id, True)

    restored_list = json.loads(await list_environment._tool_list_environment({"kind": "all"}))
    restored_search = json.loads(await search_environment._tool_search_environment({"query": "demo"}))
    assert {item["id"] for item in restored_list["items"]} == {"ripgrep", "node", "uv", "demo-mcp"}
    assert {item["id"] for item in restored_search["results"]} == {"RIPGREP", "Node", "DEMO-MCP"}


@pytest.mark.asyncio
async def test_mcp_install_rolls_back_configuration_when_connection_fails(tmp_path, monkeypatch):
    from cyrene.extensions import service
    from cyrene.tooling.backends import mcp_manager

    previous = [{
        "name": "existing",
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "enabled": False,
    }]
    saved = []
    restarts = []

    class Tasks:
        def update(self, *_args, **_kwargs):
            return None

    class Manager:
        def get_server_status(self):
            return [{"name": "new-server", "status": "disconnected"}]

    async def restart():
        restarts.append(True)

    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [dict(item) for item in previous])
    monkeypatch.setattr(mcp_manager, "save_mcp_servers", lambda value: saved.append([dict(item) for item in value]))
    monkeypatch.setattr(mcp_manager, "restart_mcp", restart)
    monkeypatch.setattr(mcp_manager, "get_manager", lambda: Manager())
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)

    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()
    with pytest.raises(RuntimeError, match="could not be connected"):
        await extension_service._install_mcp(
            "task-1",
            "new-server",
            {
                "config": {
                    "name": "new-server",
                    "transport": "streamable_http",
                    "url": "https://example.net/mcp",
                    "enabled": True,
                }
            },
            "user",
        )

    assert saved[0][-1]["name"] == "new-server"
    assert saved[-1] == previous
    assert len(restarts) == 2


@pytest.mark.asyncio
async def test_mcp_install_cancellation_rolls_back_before_propagating(monkeypatch):
    import asyncio

    from cyrene.extensions import service
    from cyrene.tooling.backends import mcp_manager

    previous = [{"name": "existing", "transport": "streamable_http", "url": "https://example.com/mcp", "enabled": False}]
    saved = []
    entered = asyncio.Event()
    restart_count = 0

    class Tasks:
        def update(self, *_args, **_kwargs):
            return None

    async def restart():
        nonlocal restart_count
        restart_count += 1
        if restart_count == 1:
            entered.set()
            await asyncio.Future()

    monkeypatch.setattr(mcp_manager, "get_mcp_servers", lambda: [dict(item) for item in previous])
    monkeypatch.setattr(mcp_manager, "save_mcp_servers", lambda value: saved.append([dict(item) for item in value]))
    monkeypatch.setattr(mcp_manager, "restart_mcp", restart)
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)

    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()
    task = asyncio.create_task(extension_service._install_mcp(
        "task-1",
        "new-server",
        {"config": {"name": "new-server", "transport": "streamable_http", "url": "https://example.net/mcp", "enabled": True}},
        "user",
    ))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert saved[-1] == previous
    assert restart_count == 2
