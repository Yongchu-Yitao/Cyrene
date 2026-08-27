from agent.plugin.plugin_impl.cyrene_code.terminal import shell_runtime


def _set_windows(monkeypatch) -> None:
    monkeypatch.setattr(shell_runtime.sys, "platform", "win32")
    monkeypatch.setattr(shell_runtime, "_existing_file", lambda value: str(value) if value else None)
    # Treat any discovered bash as launchable unless a test overrides this.
    monkeypatch.setattr(shell_runtime, "_can_launch", lambda executable: True)
    shell_runtime._launch_cache.clear()


def test_windows_prefers_bash(monkeypatch):
    _set_windows(monkeypatch)
    monkeypatch.setenv("SHELL", r"C:\Git\bin\bash.exe")
    monkeypatch.setattr(shell_runtime, "_which", lambda name: None)

    assert shell_runtime.command_argv("echo ok") == [
        r"C:\Git\bin\bash.exe", "-lc", "echo ok",
    ]


def test_windows_honors_explicit_powershell_before_discovered_bash(monkeypatch):
    _set_windows(monkeypatch)
    configured = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    monkeypatch.setenv("SHELL", configured)
    monkeypatch.setattr(
        shell_runtime,
        "_windows_bash_candidates",
        lambda: [r"C:\Program Files\Git\bin\bash.exe"],
    )

    kind, executable = shell_runtime.resolve_shell()

    assert kind == "powershell"
    assert executable == configured


def test_windows_falls_back_to_powershell(monkeypatch):
    _set_windows(monkeypatch)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(
        shell_runtime,
        "_windows_bash_candidates",
        lambda: [],
    )
    monkeypatch.setattr(
        shell_runtime,
        "_which",
        lambda name: r"C:\Program Files\PowerShell\7\pwsh.exe" if name == "pwsh.exe" else None,
    )

    assert shell_runtime.command_argv("Get-ChildItem") == [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    ]


def test_windows_falls_back_to_cmd(monkeypatch):
    _set_windows(monkeypatch)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(shell_runtime, "_windows_bash_candidates", lambda: [])
    monkeypatch.setattr(shell_runtime, "_which", lambda name: None)

    assert shell_runtime.command_argv("dir") == [
        r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c", "dir",
    ]


def test_unix_keeps_login_shell_behavior(monkeypatch):
    monkeypatch.setattr(shell_runtime.sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    assert shell_runtime.command_argv("pwd") == ["/bin/zsh", "-lc", "pwd"]


def test_windows_interactive_powershell(monkeypatch):
    _set_windows(monkeypatch)
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(shell_runtime, "_windows_bash_candidates", lambda: [])
    monkeypatch.setattr(
        shell_runtime,
        "_which",
        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        if name == "powershell.exe"
        else None,
    )

    kind, argv = shell_runtime.interactive_argv()
    assert kind == "powershell"
    assert argv[-3:] == ["-NoExit", "-Command", "-"]


# --- P2: Unix fallback regression -------------------------------------------

def test_unix_interactive_defaults_to_bash(monkeypatch):
    """Persistent shells must fall back to /bin/bash (not /bin/sh) when SHELL is unset."""
    monkeypatch.setattr(shell_runtime.sys, "platform", "linux")
    monkeypatch.delenv("SHELL", raising=False)

    kind, argv = shell_runtime.interactive_argv()
    assert kind == "bash"
    assert argv[0] == "/bin/bash"


def test_unix_oneshot_keeps_sh_default(monkeypatch):
    """One-shot Bash tool keeps its historical /bin/sh fallback."""
    monkeypatch.setattr(shell_runtime.sys, "platform", "linux")
    monkeypatch.delenv("SHELL", raising=False)

    assert shell_runtime.command_argv("pwd") == ["/bin/sh", "-lc", "pwd"]


# --- P4: bash discovery reliability -----------------------------------------

def test_is_wsl_launcher():
    assert shell_runtime._is_wsl_launcher(r"C:\Windows\System32\bash.exe")
    assert shell_runtime._is_wsl_launcher(r"C:\Windows\Sysnative\bash.exe")
    assert not shell_runtime._is_wsl_launcher(r"C:\Program Files\Git\bin\bash.exe")


def test_windows_bash_candidates_excludes_wsl(monkeypatch):
    """A WSL launcher stub on PATH must not be offered as a bash candidate."""
    monkeypatch.setattr(shell_runtime.sys, "platform", "win32")
    monkeypatch.delenv("SHELL", raising=False)
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        shell_runtime,
        "_which",
        lambda name: r"C:\Windows\System32\bash.exe" if name in ("bash.exe", "bash") else None,
    )

    candidates = shell_runtime._windows_bash_candidates()
    assert all(not shell_runtime._is_wsl_launcher(c) for c in candidates)
    assert candidates == []


def test_windows_bash_candidates_includes_per_user_git(monkeypatch):
    """The per-user install path %LOCALAPPDATA%\\Programs\\Git must be probed."""
    monkeypatch.setattr(shell_runtime.sys, "platform", "win32")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setattr(shell_runtime, "_which", lambda name: None)
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")

    candidates = shell_runtime._windows_bash_candidates()
    assert any("Programs" in c and c.endswith("bash.exe") for c in candidates)


def test_windows_skips_unlaunchable_bash_then_powershell(monkeypatch):
    """A bash that exists but won't launch must fall through to the next shell."""
    _set_windows(monkeypatch)
    monkeypatch.setenv("SHELL", r"C:\Git\bin\bash.exe")
    # Override the default: this bash exists but fails the launch probe.
    monkeypatch.setattr(shell_runtime, "_can_launch", lambda executable: False)
    monkeypatch.setattr(
        shell_runtime,
        "_which",
        lambda name: r"C:\Program Files\PowerShell\7\pwsh.exe" if name == "pwsh.exe" else None,
    )

    kind, executable = shell_runtime.resolve_shell()
    assert kind == "powershell"
    assert executable.endswith("pwsh.exe")
