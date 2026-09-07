from types import SimpleNamespace

import pytest

from cyrene.platform import windows_process


@pytest.mark.parametrize("flags", [0, 0x200, 0x208, 0x10])
def test_hidden_subprocess_default_preserves_explicit_modes(monkeypatch, flags):
    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(windows_process, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows_process, "subprocess", SimpleNamespace(
        Popen=FakePopen, STARTUPINFO=SimpleNamespace, STARTF_USESHOWWINDOW=1,
    ))
    windows_process.hide_background_console_windows()
    patched = FakePopen.__init__
    windows_process.hide_background_console_windows()
    assert FakePopen.__init__ is patched
    FakePopen(["git", "--version"], creationflags=flags, stdout="pipe")
    args, options = calls.pop()
    assert args == (["git", "--version"],)
    assert options["creationflags"] == (flags if flags & 0x18 else flags | 0x08000000)
    assert options["startupinfo"].wShowWindow == 0
    assert options["startupinfo"].dwFlags == 1
    assert options["stdout"] == "pipe"
    supplied = object()
    FakePopen(["child"], startupinfo=supplied)
    assert calls[0][1]["startupinfo"] is supplied


def test_non_windows_does_not_patch_subprocess(monkeypatch):
    monkeypatch.setattr(windows_process, "sys", SimpleNamespace(platform="darwin"))
    original = windows_process.subprocess.Popen.__init__
    windows_process.hide_background_console_windows()
    assert windows_process.subprocess.Popen.__init__ is original
