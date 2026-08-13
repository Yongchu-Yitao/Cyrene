from test_playwright_packaging import _load_build_module


def test_electron_builder_retries_transient_packaging_failure(tmp_path, monkeypatch):
    build_module = _load_build_module()
    electron_dir = tmp_path / "electron"
    builder = electron_dir / "node_modules" / ".bin" / "electron-builder"
    builder.parent.mkdir(parents=True)
    builder.write_text("", encoding="utf-8")

    monkeypatch.setattr(build_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(build_module, "IS_MAC", False)
    monkeypatch.setattr(build_module, "IS_WIN", False)
    monkeypatch.setattr(build_module, "IS_LINUX", False)
    monkeypatch.setattr(build_module.shutil, "which", lambda _name: None)

    return_codes = iter((1, 0))
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result(next(return_codes))

    delays = []
    monkeypatch.setattr(build_module.subprocess, "run", fake_run)
    monkeypatch.setattr(build_module.time, "sleep", delays.append)

    build_module.run_electron_builder()

    assert len(calls) == 2
    assert calls[0][0] == [str(builder)]
    assert calls[0][1]["cwd"] == str(electron_dir)
    assert delays == [10]
