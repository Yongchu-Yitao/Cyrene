"""Platform-specific parent watchdog regressions, without importing the app."""

import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from cyrene import simplexng_child as child


def test_windowed_child_restores_logging_before_upstream_start(monkeypatch, tmp_path):
    path = tmp_path / "search.log"
    monkeypatch.setenv("CYRENE_SIMPLEXNG_LOG_PATH", str(path))
    monkeypatch.delenv("CYRENE_SIMPLEXNG_PARENT_PID", raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(child, "_install_windows_compat_patches", lambda: None)
    monkeypatch.setattr(child.runpy, "run_module", lambda *a, **kw: print("startup failure"))
    try:
        child.main()
        assert path.read_text(encoding="utf-8") == "startup failure\n"
    finally:
        if sys.stdout is not None:
            sys.stdout.close()


def test_real_simplexng_child_serves_offline_json_search():
    script = Path(__file__).resolve().parents[1] / "build" / "simplexng_smoke.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=65,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CYRENE_SIMPLEXNG_SMOKE=ok" in result.stdout


@pytest.mark.parametrize("wait_result, alive", [(258, True), (0, False)])
def test_windows_probe_only_waits_and_closes_handle(monkeypatch, wait_result, alive):
    calls = []
    api = types.SimpleNamespace(
        OpenProcess=lambda *args: calls.append(("open", *args)) or 123,
        WaitForSingleObject=lambda *args: calls.append(("wait", *args)) or wait_result,
        CloseHandle=lambda handle: calls.append(("close", handle)),
        WAIT_OBJECT_0=0,
    )
    monkeypatch.setitem(sys.modules, "_winapi", api)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Windows probe must not signal"))

    assert child._pid_exists(4321) is alive
    assert calls == [("open", 0x00100000, False, 4321), ("wait", 123, 0), ("close", 123)]


@pytest.mark.parametrize("error_code, alive", [(87, False), (5, True), (8, True)])
def test_windows_probe_handles_missing_or_inaccessible_pid(monkeypatch, error_code, alive):
    def open_process(*args):
        error = OSError("OpenProcess failed")
        error.winerror = error_code
        raise error

    monkeypatch.setitem(sys.modules, "_winapi", types.SimpleNamespace(OpenProcess=open_process))
    monkeypatch.setattr(sys, "platform", "win32")
    assert child._pid_exists(4321) is alive


def test_windows_probe_closes_handle_after_wait_failure(monkeypatch):
    closed = []

    def wait(*args):
        raise OSError("wait failed")

    monkeypatch.setitem(sys.modules, "_winapi", types.SimpleNamespace(
        OpenProcess=lambda *args: 123,
        WaitForSingleObject=wait,
        CloseHandle=closed.append,
        WAIT_OBJECT_0=0,
    ))
    monkeypatch.setattr(sys, "platform", "win32")
    assert child._pid_exists(4321)
    assert closed == [123]


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows process handles")
def test_windows_probe_preserves_live_process_and_detects_exit():
    # Probe a disposable process, never the pytest runner or its parent: the old
    # implementation would terminate the target even with signal zero.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert child._pid_exists(process.pid)
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        process.terminate()
        process.wait(timeout=10)
        assert not child._pid_exists(process.pid)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def test_spawn_calculator_preserves_operations_and_timeout(tmp_path):
    import json

    settings = tmp_path / "settings.yml"
    settings.write_text('use_default_settings: true\nserver:\n  secret_key: "test-calculator-secret"\n')
    script = """
import json
from cyrene.platform.simplexng_calculator import evaluate
cases = ["2+2", "2**6", "1 < 3", "17 == 11+1+5 == 7+5+5", "pi", "1/0", "hello"]
print("RESULT=" + json.dumps([evaluate(expr, 0.05) for expr in cases]))
assert evaluate("9**999999999", 0.05) is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, timeout=90,
        env=dict(os.environ, SEARXNG_SETTINGS_PATH=str(settings)),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT="))
    assert json.loads(line.removeprefix("RESULT=")) == [
        [4, False], [64, False], [1, True], [1, True],
        [3.141592653589793, False], ["", False], ["", False],
    ]


def test_windows_watchdog_tracks_managed_parent_through_bootloader(monkeypatch):
    calls = []
    waits = iter([258, 0])

    def wait(handle, timeout):
        calls.append(("wait", handle, timeout))
        return next(waits)

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setitem(sys.modules, "_winapi", types.SimpleNamespace(
        OpenProcess=lambda *args: calls.append(("open", *args)) or 123,
        WaitForSingleObject=wait,
        CloseHandle=lambda handle: calls.append(("close", handle)),
        WAIT_OBJECT_0=0,
    ))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "getppid", lambda: 999)
    monkeypatch.setattr(os, "_exit", exit_process)
    with pytest.raises(SystemExit) as stopped:
        child._watch_parent(4321)
    assert stopped.value.code == 0
    assert calls == [
        ("open", 0x00100000, False, 4321),
        ("wait", 123, 1000), ("wait", 123, 1000), ("close", 123),
    ]
