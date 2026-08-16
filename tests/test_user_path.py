import os
import subprocess

import pytest

from cyrene.runtime import user_path


def test_merge_path_entries_dedupes_preserving_order():
    merged = user_path.merge_path_entries("/a:/b", "/b:/c", "/d")
    assert merged == os.pathsep.join(["/a", "/b", "/c", "/d"])


def test_merge_path_entries_skips_blanks():
    merged = user_path.merge_path_entries("", " /a ::/b ", "/a")
    assert merged == os.pathsep.join(["/a", "/b"])


def test_probe_login_shell_path_returns_path():
    shell = user_path._select_login_shell()
    if not shell:
        pytest.skip("no login shell available")
    path = user_path._probe_login_shell_path(shell)
    assert path
    assert os.pathsep in path
    assert path.startswith("/")


def test_probe_login_shell_path_tolerates_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="shell", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert user_path._probe_login_shell_path("/bin/zsh") == ""


def test_probe_login_shell_path_rejects_garbage_output(monkeypatch):
    class Result:
        stdout = "not-a-path"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    assert user_path._probe_login_shell_path("/bin/zsh") == ""


def test_select_login_shell_falls_back_to_zsh_or_bash():
    shell = user_path._select_login_shell()
    assert shell in (None, "/bin/zsh", "/bin/bash") or shell.endswith(("/zsh", "/bash"))


def test_ensure_user_path_merges_and_is_idempotent(monkeypatch):
    original = os.environ.get("PATH", "")
    monkeypatch.setattr(user_path, "_done", False)
    path = user_path.ensure_user_path()
    assert path
    for entry in original.split(os.pathsep):
        if entry:
            assert entry in path.split(os.pathsep)
    monkeypatch.setattr(user_path, "_done", False)
    again = user_path.ensure_user_path()
    assert again == path


def test_common_install_dirs_are_absolute():
    dirs = user_path.common_install_dirs()
    assert all(d.startswith("/") for d in dirs)
