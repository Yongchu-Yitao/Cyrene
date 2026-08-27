import json
import io
import os
import tarfile
from pathlib import Path

import pytest

from agent.plugin import PluginContext

from conftest import (
    workbench_i18n_source,
    workbench_settings_source,
    workbench_style_source,
)


@pytest.mark.asyncio
async def test_cli_search_falls_back_to_aqua_standard_registry(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_skills import skills

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


def test_extension_service_installs_local_skill_through_canonical_service(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

    source = tmp_path / "demo-skill"
    source.mkdir()
    installed = []
    audits = []

    def install(path):
        installed.append(path)
        return {"ok": True, "skill": {"id": "demo-skill"}}

    skills_service = type(
        "SkillsService",
        (),
        {"install_skill": staticmethod(install)},
    )()
    monkeypatch.setattr(
        service,
        "_active_skills_service",
        lambda **_kwargs: skills_service,
    )
    monkeypatch.setattr(service, "_audit", lambda *args, **kwargs: audits.append((args, kwargs)))

    extension_service = object.__new__(service.ExtensionService)
    result = extension_service.install_local_skill(source, actor="cli")

    assert result == {"ok": True, "skill": {"id": "demo-skill"}}
    assert installed == [source]
    assert audits[0][0][:3] == ("cli", "install.finish", "skill:demo-skill")
    with pytest.raises(ValueError, match="source path is required"):
        extension_service.install_local_skill("")




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
    from agent.plugin.plugin_impl.cyrene_skills import skills

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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


def test_disabled_managed_mise_extensions_are_hidden_from_the_agent_environment(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
        assert initial["icon"] == ("ripgrep" if kind == "cli" else "nodejs")
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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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

    assert card["icon"] == "tex"
    assert "bind_system" not in card["capabilities"]
    assert "install" not in card["capabilities"]
    assert {"enable", "disable"}.issubset(card["capabilities"])


@pytest.mark.asyncio
async def test_agent_cli_activation_delegates_to_the_cli_plugin_service():
    from agent.plugin.plugin_impl.cyrene_cli import tools

    calls = []

    class FakeService:
        async def set_extension_enabled(self, kind, extension_id, enabled, *, actor):
            calls.append((kind, extension_id, enabled, actor))
            return {"ok": True, "enabled": enabled}

    result = json.loads(await tools.manage_cli_plugins({
        "action": "disable",
        "plugin_id": "ripgrep",
    }, PluginContext(services={"cli": FakeService()})))

    assert result == {"ok": True, "enabled": False}
    assert calls == [("cli", "ripgrep", False, "agent")]


@pytest.mark.asyncio
async def test_cli_mise_install_keeps_old_activation_flow_and_schedules_new_hook(
    tmp_path,
    monkeypatch,
):
    import agent.plugin as plugin_api
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

    settings = {}
    manager_commands = []
    scheduled = []

    class Tasks:
        def update(self, *_args, **_kwargs):
            return None

    class WhereProcess:
        returncode = 0

        async def communicate(self):
            return str(tmp_path / "mise" / "ripgrep").encode(), b""

    class CliService:
        def schedule_hook_configuration(self, extension, *, trigger):
            scheduled.append((extension, trigger))

    async def exact_version(_mise, _ref, _requested):
        return "14.1.1"

    async def run_manager(_task_id, command, **_kwargs):
        manager_commands.append(command)

    monkeypatch.setattr(service, "_bundled_binary", lambda name: tmp_path / name)
    monkeypatch.setattr(service, "extension_environment", lambda: {})
    monkeypatch.setattr(
        service.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: _async_result(WhereProcess()),
    )
    monkeypatch.setattr(
        service,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )
    monkeypatch.setattr(
        service,
        "set_setting",
        lambda key, value: settings.__setitem__(key, value),
    )
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin_api, "active_plugin_service", lambda name: CliService() if name == "cli" else None)

    extension_service = object.__new__(service.ExtensionService)
    extension_service.tasks = Tasks()
    monkeypatch.setattr(extension_service, "_mise_exact_version", exact_version)
    monkeypatch.setattr(extension_service, "_run_manager", run_manager)

    record = await extension_service._install_mise(
        "task-cli",
        "cli",
        "ripgrep",
        {"version": "latest"},
        "user",
    )

    assert manager_commands == [
        [str(tmp_path / "mise"), "install", "github:BurntSushi/ripgrep@14.1.1"],
        [str(tmp_path / "mise"), "use", "--global", "--pin", "github:BurntSushi/ripgrep@14.1.1"],
    ]
    assert record["observed_state"] == "installed"
    assert record["source"] == {
        "type": "mise",
        "ref": "github:BurntSushi/ripgrep",
        "backend": "github",
    }
    assert scheduled == [({**record, "key": "cli:ripgrep"}, "install")]


def test_plugin_center_installed_actions_use_the_plugin_owned_endpoints():
    frontend = workbench_settings_source()
    styles = workbench_style_source()

    assert "function ExtensionCard(props)" in frontend
    assert "Toggle(item.enabled !== false" in frontend
    assert '"/api/plugin-center/" + encodeURIComponent(kind) + "/" + encodeURIComponent(id) + "/enabled"' in frontend
    assert 'jsonRequest("/api/plugin-center/" + encodeURIComponent(kind) + "/" + encodeURIComponent(id) + version, { method: "DELETE" })' in frontend
    assert "capabilities.some(function (value)" in frontend
    assert "canRemove" in frontend
    assert 'capabilities.indexOf("uninstall_managed") >= 0 ? item.managed_version : item.version' in frontend
    assert "item.managed_version, item.managed_path" in frontend
    assert "function extensionIconMarkup(name)" in frontend
    assert "assets && assets.extensions" in frontend
    assert "icon={item.icon}" in frontend
    assert 'String(pack && pack.id || "") === "cyrene_cli"' in frontend
    assert 'cliPack && cliPack.configured_enabled !== false' in frontend
    assert "pluginPacks: Array.isArray(c.registry.packs)" in frontend
    assert ".wb-extension-brand-icon.icon-python" in styles
    assert ".wb-extension-actions" in styles


def test_mcp_manual_fallback_ui_is_actionable_and_uses_structured_arguments():
    frontend = workbench_settings_source()

    assert "var fallback = item && item.fallback_request || {}" in frontend
    assert "function configureMcp(item)" in frontend
    assert 'manualMcp.args.split(/\\r?\\n/)' in frontend
    assert 'parseVariables(manualMcp.env' in frontend
    assert 'parseVariables(manualMcp.headers' in frontend
    assert 'startInstall({ id: name, name: name }, {' in frontend
    assert '<option value="streamable_http">Streamable HTTP</option>' in frontend
    assert '<option value="sse">SSE</option>' in frontend
    assert '<option value="stdio">stdio</option>' in frontend


def test_plugin_center_install_tasks_poll_cancel_and_refresh_runtime():
    frontend = workbench_settings_source()
    styles = workbench_style_source()

    assert "function TaskList(props)" in frontend
    assert 'timer = setTimeout(poll, 900)' in frontend
    assert 'timer = setTimeout(poll, 1800)' in frontend
    assert '"/tasks/" + encodeURIComponent(id) + "/cancel"' in frontend
    assert "Promise.all([loadKind(kind), refreshRuntime()])" in frontend
    assert 'status === "cancelled" || status === "canceled"' in frontend
    assert 'settings.pluginCenterInstallCancelled' in frontend
    assert ".wb-extension-task-progress" in styles
    assert ".wb-extension-task.failed" in styles


def test_extension_dependency_conflicts_have_a_stable_reason_code():
    from agent.plugin.plugin_impl.cyrene_extensions.extension_service import _extension_error_reason

    error = RuntimeError("package does not satisfy Python >=3.10 and requirements are unsatisfiable")
    assert _extension_error_reason(error) == "dependency_conflict"


def test_mcp_plugin_center_shows_dynamic_pack_intake_and_installed_servers():
    frontend = workbench_settings_source()

    assert 'selected === "recommended" ? "/api/plugin-center/overview"' in frontend
    assert 'kind === "mcp"' in frontend
    assert "McpToolDetails" in frontend
    assert "item.pack_id" in frontend
    assert "McpConfigurationDialog" in frontend
    assert '"/api/plugin-center/mcp/" + encodeURIComponent(editor.name) + "/configuration"' in frontend
    assert 'settings.pluginCenterManualMcp' in frontend


def test_cli_plugin_center_uses_advanced_search_and_exact_install_request():
    frontend = workbench_settings_source()
    styles = workbench_style_source()

    assert '"&advanced=" + (kind === "cli" && advanced ? "true" : "false")' in frontend
    assert "var request = item && item.install_request" in frontend
    assert "startInstall(item, request)" in frontend
    assert 'body: JSON.stringify({ extension_id: id, request: request })' in frontend
    assert 'settings.pluginCenterAdvancedCli' in frontend
    assert ".wb-plugin-center-advanced" in styles


def test_install_task_store_redacts_nested_secrets(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions.extension_service import _extract_verified_tar

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
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import list_environment

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
    payload = json.loads(await list_environment._tool_list_environment(
        {"kind": "all"}, PluginContext()
    ))

    assert payload["ok"] is True
    assert [item["id"] for item in payload["items"]] == ["python", "uv"]
    assert all("path" not in item for item in payload["items"])
    assert payload["items"][0]["source"] == {"type": "system", "binding": "detected"}

    uv_enabled = False
    hidden = json.loads(await list_environment._tool_list_environment(
        {"kind": "all"}, PluginContext()
    ))
    assert [item["id"] for item in hidden["items"]] == ["python"]

    excluded = json.loads(await list_environment._tool_list_environment(
        {"kind": "skill"}, PluginContext()
    ))
    assert excluded["ok"] is False
    assert excluded["code"] == "unsupported_environment_kind"
    assert "skill" in excluded["error"]


@pytest.mark.asyncio
async def test_environment_search_returns_review_ready_requests_and_partial_errors(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import search_environment

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
            return {"results": [{
                "id": "node", "name": "Node.js", "kind": "toolchain",
                "description": "JavaScript runtime", "version": "22.5.0",
                "ref": "core:node", "verified": True,
            }], "next_cursor": ""}

    monkeypatch.setattr(search_environment, "get_extension_service", lambda: FakeService())
    payload = json.loads(await search_environment._tool_search_environment(
        {"query": "search"}, PluginContext()
    ))

    assert payload["ok"] is True
    assert set(payload["source_errors"]) == {"mcp"}
    assert payload["source_errors"]["mcp"]
    assert [item["id"] for item in payload["results"]] == ["node"]
    result = payload["results"][0]
    assert result["id"] == "node"
    assert result["install_request"]["ref"] == "core:node"
    assert result["install_request"]["version"] == "22.5.0"
    assert "ManageExtensions" in payload["next_step"]
    assert "install_request" in payload["next_step"]
    assert searched_kinds == ["toolchain", "mcp"]


@pytest.mark.asyncio
async def test_environment_search_does_not_offer_reinstall_for_system_extension(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import search_environment

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
    payload = json.loads(await search_environment._tool_search_environment(
        {"kind": "toolchain", "query": "tex"}, PluginContext()
    ))

    assert payload["results"][0]["installed"] is True
    assert payload["results"][0]["installable"] is False
    assert payload["results"][0]["install_request"] is None

    excluded = json.loads(await search_environment._tool_search_environment({
        "query": "ocr",
        "kind": "skill",
    }, PluginContext()))
    assert excluded["ok"] is False
    assert excluded["code"] == "unsupported_environment_kind"
    assert "skill" in excluded["error"]


@pytest.mark.asyncio
async def test_mcp_registry_keeps_pypi_packages_and_refreshes_stale_version(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import extension_service as service

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
    from agent.plugin.plugin_impl.cyrene_extensions import search_environment

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
    payload = json.loads(await search_environment._tool_search_environment(
        {"kind": "mcp", "query": "demo"}, PluginContext()
    ))
    item = payload["results"][0]
    assert item["installable"] is False
    assert item["reason_code"] == "unsupported_registry_type"
    assert item["fallback_request"]["action"] == "install_local_mcp"
    assert "ManageExtensions" in payload["next_step"]
    assert "install_request" in payload["next_step"]


@pytest.mark.asyncio
async def test_manage_extensions_exposes_local_mcp_action(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import manage_extensions

    started = []

    class FakeService:
        def start_install(self, kind, extension_id, request, *, actor):
            started.append((kind, extension_id, request, actor))
            return {"id": "task"}

    monkeypatch.setattr(manage_extensions, "get_extension_service", lambda: FakeService())
    config = {"name": "demo", "transport": "stdio", "command": "/bin/demo", "args": [], "version": "1.0.0"}
    payload = json.loads(await manage_extensions._tool_manage_extensions({
        "action": "install_local_mcp", "kind": "mcp", "extension_id": "demo", "request": {"config": config},
    }, PluginContext()))
    assert payload["ok"] is True
    assert started == [("mcp", "demo", {"config": config}, "agent")]
