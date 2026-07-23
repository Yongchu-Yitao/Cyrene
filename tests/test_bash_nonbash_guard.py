"""Fail-closed guard for write/delete commands under a non-POSIX shell (P1)."""

import json

import pytest

from cyrene.tooling.runtime_support import (
    _guard_nonbash_shell_command,
    _guard_shell_command_workspace_write,
    _nonbash_command_writes,
)


def test_detects_powershell_writes():
    assert _nonbash_command_writes(r"Remove-Item C:\tmp\x")
    assert _nonbash_command_writes("Set-Content -Path a.txt -Value hi")
    assert _nonbash_command_writes("Out-File -FilePath a.txt")
    assert _nonbash_command_writes("Copy-Item a b")
    assert _nonbash_command_writes("Move-Item a b")
    assert _nonbash_command_writes("New-Item -ItemType Directory foo")
    assert _nonbash_command_writes("Get-Process | Export-Csv procs.csv")
    assert _nonbash_command_writes("Get-Data | Tee-Object -FilePath log.txt")


def test_detects_cmd_writes():
    assert _nonbash_command_writes("del foo.txt")
    assert _nonbash_command_writes("copy a b")
    assert _nonbash_command_writes("move a b")
    assert _nonbash_command_writes("rd /s /q foo")


def test_detects_redirect_and_posix_aliases():
    assert _nonbash_command_writes("echo hi > out.txt")
    assert _nonbash_command_writes("echo hi >> out.txt")
    assert _nonbash_command_writes("rm -rf foo")
    assert _nonbash_command_writes("cp a b")


def test_allows_read_only_commands():
    assert not _nonbash_command_writes("Get-ChildItem")
    assert not _nonbash_command_writes("dir")
    assert not _nonbash_command_writes("Get-Content a.txt")
    assert not _nonbash_command_writes("type a.txt")
    assert not _nonbash_command_writes("Select-String foo a.txt")
    # 2>&1 is a handle duplication, not a file write.
    assert not _nonbash_command_writes("some-tool 2>&1")
    assert not _nonbash_command_writes("")


def test_guard_refuses_writes_with_dialect_message():
    refusal = _guard_nonbash_shell_command("Remove-Item x", "powershell")
    assert refusal is not None
    payload = json.loads(refusal)
    assert payload["exit_code"] == -1
    assert "powershell" in payload["stderr"]


def test_guard_passes_read_only():
    assert _guard_nonbash_shell_command("Get-ChildItem", "powershell") is None
    assert _guard_nonbash_shell_command("dir", "cmd") is None


def test_posix_guard_allows_null_device_redirect(monkeypatch, tmp_path):
    from cyrene.agent import state as agent_state

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    token = agent_state._active_workspace_dir.set(str(workspace))
    try:
        _guard_shell_command_workspace_write("ls -lt ~/Desktop/*.pdf 2>/dev/null | head -20")
        with pytest.raises(ValueError):
            _guard_shell_command_workspace_write("echo hi > /tmp/outside.txt")
    finally:
        agent_state._active_workspace_dir.reset(token)
