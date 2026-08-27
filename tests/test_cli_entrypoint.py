import asyncio
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
CHECKOUT_VENV_PYTHON = (
    PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else PROJECT_DIR / ".venv" / "bin" / "python3"
)
CHECKOUT_CYRENE_COMMAND = (
    PROJECT_DIR / ".venv" / "Scripts" / "cyrene.exe"
    if os.name == "nt"
    else PROJECT_DIR / ".venv" / "bin" / "cyrene"
)


def test_uv_console_script_and_electron_use_runtime_entrypoint():
    project = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["cyrene"] == "cyrene.__main__:main"

    source = (PROJECT_DIR / "electron" / "main.js").read_text(encoding="utf-8")
    development_binary = source.split(
        "function getPythonBinaryPath()", 1
    )[1].split("function getPythonArgs()", 1)[0]
    development_args = source.split("function getPythonArgs()", 1)[1].split(
        "function getCurrentAppExecutablePath()", 1
    )[0]

    assert "return 'uv'" in development_binary
    assert "'run'" in development_args
    assert "'cyrene'" in development_args
    assert "'local_cli.py'" not in development_args


@pytest.mark.skipif(
    not CHECKOUT_CYRENE_COMMAND.is_file(),
    reason="checkout console command is not available",
)
def test_checkout_console_command_loads_runtime_and_new_agent_package(tmp_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    help_result = subprocess.run(
        [str(CHECKOUT_CYRENE_COMMAND), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "Cyrene runtime entry point" in help_result.stdout

    import_result = subprocess.run(
        [
            str(CHECKOUT_VENV_PYTHON),
            "-I",
            "-c",
            "import agent, agent.plugin; print(agent.__file__)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert import_result.returncode == 0, import_result.stderr
    expected_agent = PROJECT_DIR / "src" / "agent" / "__init__.py"
    assert Path(import_result.stdout.strip()).resolve() == expected_agent.resolve()


def test_direct_local_cli_file_bootstraps_source_imports(tmp_path):
    entrypoint = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "cyrene"
        / "local_cli.py"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["CYRENE_LOCAL_CLI_BOOTSTRAPPED"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(entrypoint)!r}, run_name='direct_import')"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    not CHECKOUT_VENV_PYTHON.is_file(),
    reason="checkout virtual environment is not available",
)
def test_direct_local_cli_prefers_checkout_virtualenv(tmp_path):
    entrypoint = PROJECT_DIR / "src" / "cyrene" / "local_cli.py"

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("CYRENE_LOCAL_CLI_BOOTSTRAPPED", None)
    result = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Cyrene AI Agent CLI" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_module_help_has_no_runtime_side_effects(tmp_path):
    runtime_dir = tmp_path / "runtime"
    env = os.environ.copy()
    env.update(
        {
            "CYRENE_BASE_DIR": str(runtime_dir),
            "CYRENE_USER_DATA_DIR": str(runtime_dir / "data"),
            "CYRENE_CACHE_DIR": str(runtime_dir / "cache"),
            "CYRENE_TEMP_DIR": str(runtime_dir / "temp"),
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "cyrene", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "usage: python -m cyrene" in result.stdout
    assert "Database initialized" not in result.stderr
    assert "SimpleXNG" not in result.stderr
    assert not runtime_dir.exists()


def test_module_defaults_to_workbench(monkeypatch):
    import cyrene.__main__ as module_entry
    from cyrene.runtime import host

    calls = []
    monkeypatch.setattr(sys, "argv", ["cyrene"])
    monkeypatch.setattr(
        host,
        "run_web_mode",
        lambda *, ui_mode: calls.append(ui_mode),
    )

    module_entry.main()

    assert calls == ["workbench"]


@pytest.mark.asyncio
async def test_interactive_cli_drains_background_work_before_loop_closes(monkeypatch):
    from cyrene.runtime import host as local_cli
    from cyrene.runtime import lifecycle as runtime_lifecycle

    events = []

    async def fake_cli_loop():
        loop = asyncio.get_running_loop()
        events.append(("cli", loop, loop.is_closed()))

    async def fake_shutdown():
        loop = asyncio.get_running_loop()
        events.append(("shutdown", loop, loop.is_closed()))

    monkeypatch.setattr(local_cli, "_cli_loop", fake_cli_loop)
    monkeypatch.setattr(runtime_lifecycle, "shutdown_background_work", fake_shutdown)

    await local_cli._run_cli_loop_with_shutdown()

    assert [event[0] for event in events] == ["cli", "shutdown"]
    assert events[0][1] is events[1][1]
    assert events[1][2] is False
