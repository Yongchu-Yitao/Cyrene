"""Focused tests for the adapter runtime-dependency install + PATH injection.

The pi-acp adapter spawns the ``pi`` coding agent executable at session/new
time, but the adapter package does not bundle it. The recommended-agent
catalog declares the runtime as a pinned ``dependency``; installs stage both
packages into the same prefix and the ACP process manager prepends the
installed ``node_modules/.bin`` (plus the managed Node bin dir) to the child
PATH. Managed executables are preferred, while globally installed components
on the user's PATH remain valid fallbacks.

Covers the catalog declaration, the npm install command composition and
idempotency short-circuit in ``_install_recommended_agent``, the dependency
missing error surfaced on the install task, and the child PATH injection in
``AcpProcessManager.get_transport``.
"""

from __future__ import annotations

import os

import pytest

from cyrene.agents import AcpProcessManager


# ---------------------------------------------------------------------------
# Catalog declaration
# ---------------------------------------------------------------------------

def test_pi_acp_catalog_declares_pinned_runtime_dependency():
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    profile = RECOMMENDED_AGENTS["pi-acp"]
    assert profile["distribution"] == {"kind": "npm", "package": "pi-acp@0.0.33"}
    dependency = profile["dependency"]
    assert dependency == {
        "kind": "npm",
        "package": "@earendil-works/pi-coding-agent@0.74.2",
        "bin": "pi",
    }
    # The catalog pins exact versions everywhere; a dependency must too.
    assert "@" in dependency["package"]
    assert dependency["bin"].strip()


def test_other_recommended_agents_do_not_declare_dependencies():
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    for agent_id in ("opencode", "codex-acp"):
        assert "dependency" not in RECOMMENDED_AGENTS[agent_id]


# ---------------------------------------------------------------------------
# _install_recommended_agent npm branch
# ---------------------------------------------------------------------------

def _service(tasks=None):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    extension_service = object.__new__(service_module.ExtensionService)
    extension_service.tasks = tasks or _FakeTasks()
    return extension_service


class _FakeTasks:
    def create(self, **kwargs):
        return {"id": "task-agent-1", **kwargs}

    def start(self, task, manager, worker):
        return None

    def update(self, task_id, **changes):
        return None

    def list(self):
        return []


def _patch_extension_dirs(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    monkeypatch.setattr(service_module, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(service_module, "_AGENT_DIR", tmp_path / "agents")
    monkeypatch.setattr(service_module, "_AGENT_BIN_DIR", tmp_path / "agents" / "bin")
    monkeypatch.setattr(service_module.shutil, "which", lambda *_args, **_kwargs: "npm")
    return service_module


def _stage_shims(tmp_path, task_id, *, with_dependency: bool):
    """Create the adapter shim (and optionally the runtime shim) under staging."""
    bin_dir = tmp_path / "staging" / task_id / "agent" / "npm" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "pi-acp").write_bytes(b"#!/usr/bin/env node\n")
    if with_dependency:
        (bin_dir / "pi").write_bytes(b"#!/usr/bin/env node\n")
    return bin_dir


@pytest.mark.asyncio
async def test_npm_install_command_bundles_dependency_package(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    _patch_extension_dirs(monkeypatch, tmp_path)
    install_root = tmp_path / "agents" / "pi-acp" / "0.0.33"
    calls = []

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        calls.append(command)
        _stage_shims(tmp_path, task_id, with_dependency=True)

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    path, checksum = await service._install_recommended_agent("task-1", "pi-acp", profile)

    assert checksum == ""
    assert len(calls) == 1
    command = calls[0]
    assert command[0] == "npm"
    assert "--prefix" in command
    assert command[-2:] == ["pi-acp@0.0.33", "@earendil-works/pi-coding-agent@0.74.2"]
    expected = install_root / "node_modules" / ".bin" / "pi-acp"
    assert path == str(expected)
    assert expected.is_file()
    assert (install_root / "node_modules" / ".bin" / "pi").is_file()


@pytest.mark.asyncio
async def test_npm_install_missing_dependency_raises_with_clear_message(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    _patch_extension_dirs(monkeypatch, tmp_path)

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        # npm installs only the adapter package; the runtime never materializes.
        _stage_shims(tmp_path, task_id, with_dependency=False)

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    with pytest.raises(RuntimeError) as excinfo:
        await service._install_recommended_agent("task-1", "pi-acp", profile)
    message = str(excinfo.value)
    assert "did not provide its pi executable" in message
    assert "本体未安装" in message
    # The missing dependency must surface as an executable_not_found task
    # error kind, matching the other missing-executable install failures.
    assert service_module._extension_error_reason(excinfo.value) == "executable_not_found"


@pytest.mark.asyncio
async def test_npm_install_idempotent_only_when_dependency_present(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    _patch_extension_dirs(monkeypatch, tmp_path)
    install_root = tmp_path / "agents" / "pi-acp" / "0.0.33"
    destination = install_root / "node_modules" / ".bin" / "pi-acp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"#!/usr/bin/env node\n")
    (install_root / "node_modules" / ".bin" / "pi").write_bytes(b"#!/usr/bin/env node\n")

    calls = []

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        calls.append(command)

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    path, checksum = await service._install_recommended_agent("task-1", "pi-acp", profile)
    assert path == str(destination)
    assert checksum == ""
    assert calls == []


@pytest.mark.asyncio
async def test_npm_install_reinstalls_when_dependency_shim_missing(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    _patch_extension_dirs(monkeypatch, tmp_path)
    install_root = tmp_path / "agents" / "pi-acp" / "0.0.33"
    destination = install_root / "node_modules" / ".bin" / "pi-acp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"#!/usr/bin/env node\n")
    # Adapter shim exists but the ``pi`` runtime shim does not: the previous
    # install is incomplete and must be redone.
    assert not (install_root / "node_modules" / ".bin" / "pi").exists()

    calls = []

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        calls.append(command)
        _stage_shims(tmp_path, task_id, with_dependency=True)

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    path, checksum = await service._install_recommended_agent("task-1", "pi-acp", profile)
    assert len(calls) == 1
    assert path == str(destination)
    assert (install_root / "node_modules" / ".bin" / "pi").is_file()


# ---------------------------------------------------------------------------
# ACP child PATH injection
# ---------------------------------------------------------------------------

def _installation(**overrides):
    record = {
        "installation_id": "agent_pi-acp_default",
        "agent_id": "pi-acp",
        "display_name": "Pi ACP",
        "version": "0.0.33",
        "driver": "acp_stdio",
        "protocol_version": 1,
        "command": "pi-acp",
        "runtime_dependencies": ["pi"],
        "enabled": True,
        "install_state": "installed",
        "runtime_state": "not_started",
        "auth_state": "not_configured",
        "model_access": {"mode": "agent_managed"},
        "capabilities": {"output": {"streaming": "supported"}},
    }
    record.update(overrides)
    return record


def test_prepend_path_dirs_prepends_dedupes_and_preserves_original():
    from cyrene.agents.process_manager import prepend_path_dirs

    original = f"/usr/bin{os.pathsep}/bin"
    merged = prepend_path_dirs(original, ["/opt/a", "/opt/b", "/usr/bin"])
    parts = merged.split(os.pathsep)
    assert parts == ["/opt/a", "/opt/b", "/usr/bin", "/bin"]
    # Every original component is still present, just possibly reordered.
    assert set(parts) >= {"/usr/bin", "/bin"}

    assert prepend_path_dirs("", ["/opt/a"]) == "/opt/a"
    assert prepend_path_dirs("", []) == ""


def test_agent_child_path_dirs_uses_install_shim_dir_and_managed_node_bin(monkeypatch, tmp_path):
    from cyrene.agents import process_manager

    monkeypatch.setattr(
        process_manager,
        "_managed_runtime_bin_dir",
        lambda: str(tmp_path / "node-bin"),
    )
    shim_dir = tmp_path / "agents" / "pi-acp" / "0.0.33" / "node_modules" / ".bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    install = _installation(managed_path=str(shim_dir / "pi-acp"))

    dirs = process_manager.agent_child_path_dirs(install)
    assert dirs == [str(shim_dir), str(tmp_path / "node-bin")]

    # The managed Node bin dir is deduped when it matches the shim dir.
    monkeypatch.setattr(process_manager, "_managed_runtime_bin_dir", lambda: str(shim_dir))
    assert process_manager.agent_child_path_dirs(install) == [str(shim_dir)]


def test_agent_child_path_dirs_skips_missing_install_dir(monkeypatch, tmp_path):
    from cyrene.agents import process_manager

    monkeypatch.setattr(process_manager, "_managed_runtime_bin_dir", lambda: None)
    # managed_path points at a directory that does not exist on disk.
    install = _installation(managed_path=str(tmp_path / "gone" / "node_modules" / ".bin" / "pi-acp"))
    assert process_manager.agent_child_path_dirs(install) == []
    # No managed_path at all (binary distributions) yields no extra dirs.
    assert process_manager.agent_child_path_dirs(_installation()) == []


def test_validation_accepts_global_agent_and_dependency_fallback(monkeypatch, tmp_path):
    from cyrene.agents import process_manager

    monkeypatch.setattr(process_manager, "_managed_runtime_bin_dir", lambda: None)
    global_bin = tmp_path / "global-bin"
    global_bin.mkdir()
    for name in ("pi-acp", "pi"):
        executable = global_bin / name
        executable.write_bytes(b"#!/bin/sh\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(global_bin))

    missing_managed = tmp_path / "gone" / "node_modules" / ".bin" / "pi-acp"
    AcpProcessManager().validate_installation(
        _installation(managed_path=str(missing_managed))
    )
    # The same policy applies to a fully system-provided Agent record.
    AcpProcessManager().validate_installation(_installation())


def test_validation_rejects_component_missing_from_managed_and_global_path(monkeypatch, tmp_path):
    from cyrene.agents import process_manager
    from cyrene.agents.errors import AgentRuntimeError

    monkeypatch.setattr(process_manager, "_managed_runtime_bin_dir", lambda: None)
    shim_dir = tmp_path / "agents" / "pi-acp" / "node_modules" / ".bin"
    shim_dir.mkdir(parents=True)
    adapter = shim_dir / "pi-acp"
    adapter.write_bytes(b"#!/bin/sh\n")
    adapter.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(AgentRuntimeError) as excinfo:
        AcpProcessManager().validate_installation(
            _installation(managed_path=str(adapter))
        )
    assert excinfo.value.kind == "dependency_missing"
    assert excinfo.value.detail["dependency"] == "pi"


def test_agent_install_completeness_accepts_global_dependency(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    monkeypatch.setattr(service_module, "_AGENT_DIR", tmp_path / "agents")
    install_bin = tmp_path / "agents" / "pi-acp" / "0.0.33" / "node_modules" / ".bin"
    install_bin.mkdir(parents=True)
    (install_bin / "pi-acp").write_bytes(b"#!/usr/bin/env node\n")
    monkeypatch.setattr(
        service_module.shutil,
        "which",
        lambda name: "/global/bin/pi" if name == "pi" else None,
    )

    assert _service()._agent_install_complete("pi-acp", "0.0.33") is True


class _CapturingTransport:
    """Transport stand-in that records the child env without spawning."""

    def __init__(self, command, args=(), **kwargs):
        self.command = command
        self.args = args
        self.env = dict(kwargs.get("env") or {})
        self.is_closed = False

    async def start(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_get_transport_prepends_dependency_path_to_child_env(monkeypatch, tmp_path):
    from cyrene.agents import process_manager

    monkeypatch.setattr(process_manager, "_managed_runtime_bin_dir", lambda: str(tmp_path / "node-bin"))
    shim_dir = tmp_path / "agents" / "pi-acp" / "0.0.33" / "node_modules" / ".bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "pi-acp"
    shim.write_bytes(b"#!/usr/bin/env node\n")
    # validate_installation requires the declared ``pi`` runtime dependency too.
    (shim_dir / "pi").write_bytes(b"#!/usr/bin/env node\n")

    captured: list[_CapturingTransport] = []
    manager = AcpProcessManager(transport_factory=lambda *args, **kwargs: (
        captured.append(_CapturingTransport(*args, **kwargs)) or captured[-1]
    ))
    install = _installation(managed_path=str(shim))

    transport = await manager.get_transport(install)
    child_path = transport.env["PATH"]
    parts = child_path.split(os.pathsep)
    assert parts[0] == str(shim_dir)
    assert parts[1] == str(tmp_path / "node-bin")
    # The original base PATH is preserved in full after the prepended dirs.
    base_parts = os.environ.get("PATH", "").split(os.pathsep)
    assert set(parts) >= set(base_parts)

    await manager.close_all()
