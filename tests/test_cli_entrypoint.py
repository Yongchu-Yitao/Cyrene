import os
import subprocess
import sys


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
