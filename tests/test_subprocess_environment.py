from __future__ import annotations

import importlib

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.platform import subprocess_environment


def test_external_process_environment_restores_original_linux_library_path(
    monkeypatch,
):
    monkeypatch.setattr(subprocess_environment.sys, "platform", "linux")
    monkeypatch.setattr(subprocess_environment.sys, "frozen", True, raising=False)
    source = {
        "PATH": "/usr/bin",
        "LD_LIBRARY_PATH": "/opt/Cyrene/resources/python-bundle/_internal",
        "LD_LIBRARY_PATH_ORIG": "/usr/local/lib:/usr/lib",
    }

    result = subprocess_environment.external_process_environment(source)

    assert result["LD_LIBRARY_PATH"] == "/usr/local/lib:/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in result
    assert source["LD_LIBRARY_PATH"].startswith("/opt/Cyrene/")


def test_external_process_environment_removes_injected_linux_library_path(
    monkeypatch,
):
    monkeypatch.setattr(subprocess_environment.sys, "platform", "linux")
    monkeypatch.setattr(subprocess_environment.sys, "frozen", True, raising=False)

    result = subprocess_environment.external_process_environment(
        {
            "PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/opt/Cyrene/resources/python-bundle/_internal",
        }
    )

    assert "LD_LIBRARY_PATH" not in result
    assert "LD_LIBRARY_PATH_ORIG" not in result


def test_external_process_environment_cleans_inherited_pyinstaller_environment(
    monkeypatch,
):
    monkeypatch.setattr(subprocess_environment.sys, "platform", "linux")
    monkeypatch.delattr(subprocess_environment.sys, "frozen", raising=False)

    result = subprocess_environment.external_process_environment(
        {
            "LD_LIBRARY_PATH": "/tmp/pyinstaller",
            "LD_LIBRARY_PATH_ORIG": "/host/lib",
        }
    )

    assert result["LD_LIBRARY_PATH"] == "/host/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in result


def test_external_process_environment_preserves_normal_development_environment(
    monkeypatch,
):
    monkeypatch.setattr(subprocess_environment.sys, "platform", "linux")
    monkeypatch.delattr(subprocess_environment.sys, "frozen", raising=False)
    source = {"LD_LIBRARY_PATH": "/developer/lib"}

    result = subprocess_environment.external_process_environment(source)

    assert result == source
    assert result is not source


@pytest.mark.asyncio
async def test_bash_removes_bundled_library_path_without_extension_service(
    tmp_path,
    monkeypatch,
):
    bash_module = importlib.import_module("cyrene.core.plugin.core_impl.bash")
    monkeypatch.setattr(subprocess_environment.sys, "platform", "linux")
    monkeypatch.setattr(subprocess_environment.sys, "frozen", True, raising=False)
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        "/opt/Cyrene/resources/python-bundle/_internal",
    )
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    captured = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def create_process(command, **kwargs):
        captured.update({"command": command, **kwargs})
        return Process()

    monkeypatch.setattr(
        bash_module.asyncio,
        "create_subprocess_shell",
        create_process,
    )

    result = await bash_module.bash(
        {"command": "/usr/bin/curl --version"},
        PluginContext(workspace=tmp_path),
    )

    assert result["exit_code"] == 0
    assert "LD_LIBRARY_PATH" not in captured["env"]
    assert "LD_LIBRARY_PATH_ORIG" not in captured["env"]
