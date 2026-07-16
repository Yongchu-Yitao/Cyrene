import asyncio
import os
import subprocess
import sys

import pytest


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


@pytest.mark.asyncio
async def test_interactive_cli_drains_background_work_before_loop_closes(monkeypatch):
    from cyrene import local_cli
    from cyrene import runtime_lifecycle

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
