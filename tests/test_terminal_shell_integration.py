from __future__ import annotations

import asyncio

import pytest

from cyrene.terminal.manager import TerminalManager, TerminalSession, _now_iso
from cyrene.terminal.history import IncrementalPlainTextParser, plain_terminal_text
from cyrene.terminal.shell_integration import (
    OscMetadataParser,
    prepare_shell_integration,
    shell_kind,
)


def test_shell_kind_prefers_actual_executable() -> None:
    assert shell_kind("bash", ["/bin/zsh", "-i"]) == "zsh"
    assert shell_kind("shell", ["/usr/local/bin/fish", "-i"]) == "fish"
    assert shell_kind("powershell", [r"C:\Program Files\PowerShell\pwsh.exe"]) == "powershell"


def test_plain_terminal_text_applies_line_editor_backspaces() -> None:
    assert plain_terminal_text(b"c\bcd nested; false") == "cd nested; false"


def test_incremental_plain_text_matches_complete_parser_at_every_boundary() -> None:
    payload = (
        b"alpha \xe4\xb8\xad \x1b]2;ignored title\x1b\\cross"
        b"\x1b[31m boundary\x1b[0m\r\neditx\b\n"
    )
    expected = plain_terminal_text(payload)

    for chunk_size in range(1, len(payload) + 1):
        parser = IncrementalPlainTextParser({"nextSeq": 0, "lineStartSeq": 0})
        complete: list[str] = []
        for start in range(0, len(payload), chunk_size):
            complete.extend(
                line["text"]
                for line in parser.feed(
                    payload[start:start + chunk_size], start_seq=start
                )
            )
            parser = IncrementalPlainTextParser(parser.state())
        actual = "\n".join([*complete, parser.current_line()["text"]])
        assert actual == expected


def test_prepare_bash_integration_preserves_inputs_and_generates_wrapper(tmp_path) -> None:
    argv = ["/bin/bash", "-i"]
    env = {"HOME": "/home/user", "TERM": "xterm-256color"}

    launch = prepare_shell_integration(
        shell="bash", argv=argv, env=env, runtime_dir=tmp_path,
    )

    assert launch.integration_level == "basic"
    assert launch.argv[0] == "/bin/bash"
    assert launch.argv[1] == "--rcfile"
    assert launch.argv[-1] == "-i"
    assert "OSC" not in launch.argv[2]
    assert argv == ["/bin/bash", "-i"]
    assert env == {"HOME": "/home/user", "TERM": "xterm-256color"}
    wrapper = (tmp_path / "shell-integration" / "cyrene.bash").read_text()
    assert "${HOME}/.bashrc" in wrapper
    integration = launch.env["CYRENE_SHELL_INTEGRATION_SCRIPT"]
    integration_source = open(integration, encoding="utf-8").read()
    assert "BASH_VERSINFO" in integration_source
    assert "Integration=${__cyrene_integration_level}" in integration_source
    assert '__cyrene_emit_osc "133;A"' in integration_source
    assert '__cyrene_emit_osc "133;D;${__cyrene_status}"' in integration_source
    assert "export PROMPT_COMMAND PS0 PS1" in integration_source
    assert launch.env["BASH_ENV"] == integration
    assert "BASH_SOURCE[0]" in wrapper


def test_prepare_zsh_and_fish_preserve_user_startup(tmp_path) -> None:
    zsh = prepare_shell_integration(
        shell="bash",
        argv=["/bin/zsh", "-i"],
        env={"HOME": "/home/user", "ZDOTDIR": "/home/user/.config/zsh"},
        runtime_dir=tmp_path,
    )
    assert zsh.shell_kind == "zsh"
    assert zsh.integration_level == "full"
    assert zsh.env["CYRENE_ORIGINAL_ZDOTDIR"] == "/home/user/.config/zsh"
    assert zsh.env["ZDOTDIR"].endswith("shell-integration/zsh")
    assert ".zshrc" in (tmp_path / "shell-integration" / "zsh" / ".zshrc").read_text()
    assert zsh.env["CYRENE_SHELL_INTEGRATION_SCRIPT"].endswith(
        "cyrene.zsh.integration"
    )

    fish = prepare_shell_integration(
        shell="fish", argv=["/usr/bin/fish", "-i"], env={}, runtime_dir=tmp_path,
    )
    assert fish.argv[1] == "-C"
    assert fish.integration_level == "full"
    assert "fish_preexec" in (tmp_path / "shell-integration" / "cyrene.fish").read_text()
    assert fish.env["XDG_CONFIG_DIRS"].split(__import__("os").pathsep)[0].endswith(
        "shell-integration/xdg"
    )
    assert (
        tmp_path / "shell-integration" / "xdg" / "fish" / "conf.d" / "cyrene.fish"
    ).is_file()


def test_prepare_powershell_and_cmd_report_capability(tmp_path) -> None:
    powershell = prepare_shell_integration(
        shell="powershell", argv=["pwsh.exe"], env={}, runtime_dir=tmp_path,
    )
    assert powershell.integration_level == "full"
    assert powershell.argv[-2] == "-File"
    assert "PSConsoleHostReadLine" in (
        tmp_path / "shell-integration" / "cyrene.ps1"
    ).read_text()

    cmd = prepare_shell_integration(
        shell="cmd", argv=["cmd.exe", "/d", "/q"], env={}, runtime_dir=tmp_path,
    )
    assert cmd.integration_level == "basic"
    assert cmd.argv[:4] == ["cmd.exe", "/d", "/q", "/k"]


def test_prepare_does_not_touch_one_shot_launches(tmp_path) -> None:
    argv = ["/bin/bash", "-lc", "printf ok"]
    env = {"TERM": "xterm-256color"}

    launch = prepare_shell_integration(
        shell="bash",
        argv=argv,
        env=env,
        runtime_dir=tmp_path,
        launch_mode="one_shot",
    )

    assert launch.argv == argv
    assert launch.env == env
    assert launch.integration_level == "none"
    assert not (tmp_path / "shell-integration").exists()


def test_incremental_parser_handles_every_chunk_boundary() -> None:
    stream = (
        b"before\x1b]7;file://localhost/tmp/hello%20world\x1b\\"
        b"\x1b]2;hello\x07\x1b]133;A\x1b\\prompt"
        b"\x1b]133;B\x1b\\echo ok\r\n\x1b]133;C\x1b\\ok\r\n"
        b"\x1b]133;D;7\x1b\\after"
    )
    expected = None
    for chunk_size in range(1, len(stream) + 1):
        parser = OscMetadataParser()
        events = []
        for start in range(0, len(stream), chunk_size):
            events.extend(parser.feed(stream[start:start + chunk_size], start_seq=100 + start))
        normalized = [
            (event["kind"], event.get("value"), event.get("exitCode"), event["startSeq"], event["endSeq"])
            for event in events
        ]
        if expected is None:
            expected = normalized
        assert normalized == expected
    assert expected is not None
    assert [item[0] for item in expected] == [
        "cwd", "title", "prompt", "command", "output", "finished",
    ]
    assert expected[0][1] == "/tmp/hello world"
    assert expected[-1][2] == 7


def test_parser_ignores_non_metadata_and_resets_on_sequence_gap() -> None:
    parser = OscMetadataParser()
    assert parser.feed(b"\x1b]133;", start_seq=0) == []
    assert parser.feed(b"A\x07", start_seq=99) == []
    assert parser.feed(b"plain output", start_seq=101) == []


def test_parser_reports_runtime_integration_capability() -> None:
    parser = OscMetadataParser()
    data = b"\x1b]133;P;Integration=full\x1b\\"

    assert parser.feed(data, start_seq=12) == [{
        "kind": "integration",
        "value": "full",
        "startSeq": 12,
        "endSeq": 12 + len(data),
    }]


def test_parser_normalizes_windows_file_uri() -> None:
    parser = OscMetadataParser()
    data = b"\x1b]7;file://localhost/C:/Users/me/project\x07"
    events = parser.feed(data, start_seq=50)

    assert events == [{
        "kind": "cwd",
        "value": r"C:\Users\me\project",
        "uri": "file://localhost/C:/Users/me/project",
        "host": "localhost",
        "startSeq": 50,
        "endSeq": 50 + len(data),
    }]


@pytest.mark.asyncio
async def test_manager_publishes_dynamic_shell_metadata_without_renaming(
    tmp_path,
) -> None:
    manager = TerminalManager(state_dir=tmp_path / "state")
    cwd = tmp_path / "nested"
    cwd.mkdir()
    now = _now_iso()
    session = TerminalSession(
        id="term_metadata",
        project_id="project-1",
        title="Terminal 1",
        cwd=str(tmp_path),
        shell="bash",
        argv=["/bin/bash", "-i"],
        created_at=now,
        updated_at=now,
        status="running",
        integration_level="full",
    )
    manager._sessions[session.id] = session
    manager._reset_screen(session)
    manager._persist_session(session)
    queue = manager.subscribe(session.id)
    stream = (
        f"\x1b]7;file://localhost{cwd}\x1b\\"
        "\x1b]2;pytest — nested\x1b\\"
        "\x1b]133;A\x1b\\\x1b]133;B\x1b\\"
        "echo ok\r\n\x1b]133;C\x1b\\ok\r\n\x1b]133;D;7\x1b\\"
    ).encode()

    for offset in range(0, len(stream), 3):
        manager._append_output(session, stream[offset:offset + 3])
    await manager.screen_snapshot_async(session.id)
    await asyncio.sleep(0)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    states = [event["terminal"] for event in events if event["type"] == "state"]
    assert states
    assert events[0]["type"] == "output"
    assert any(event.get("reason") == "metadata" for event in events)
    assert session.title == "Terminal 1"
    assert session.cwd == str(cwd)
    assert session.shell_title == "pytest — nested"
    assert session.command_state == "finished"
    assert session.last_command_exit_code == 7
    assert session.public()["displayTitle"] == "pytest — nested"
    renamed = manager.rename(session.id, "Pinned shell")
    assert renamed["title"] == "Pinned shell"
    assert renamed["displayTitle"] == "Pinned shell"
    assert renamed["shellTitle"] == "pytest — nested"

    manager.flush()
    restored = TerminalManager(state_dir=tmp_path / "state")
    metadata = restored.get(session.id).public()
    assert metadata["cwd"] == str(cwd)
    assert metadata["shellTitle"] == "pytest — nested"
    assert metadata["lastCommandExitCode"] == 7
    manager._drain_screen_now(session)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_new_terminal_inherits_active_shell_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    first_cwd = tmp_path / "first"
    inherited_cwd = tmp_path / "inherited"
    first_cwd.mkdir()
    inherited_cwd.mkdir()
    monkeypatch.setattr(
        "cyrene.workbench.app_services.read_project",
        lambda project_id: {"id": project_id, "workspacePath": str(tmp_path)},
    )
    monkeypatch.setattr(
        "cyrene.tooling.backends.shell_runtime.interactive_argv",
        lambda: ("sh", ["/bin/sh"]),
    )
    manager = TerminalManager(state_dir=tmp_path / "state")

    async def fake_spawn(session):
        session.status = "running"

    monkeypatch.setattr(manager, "_spawn_posix", fake_spawn)
    first = await manager.create("project-1", cwd="first")
    manager.set_active("project-1", first["id"])
    active = manager.get(first["id"])
    manager._append_output(
        active,
        f"\x1b]7;file://localhost{inherited_cwd}\x1b\\".encode(),
    )

    second = await manager.create("project-1")

    assert active.public()["cwd"] == str(inherited_cwd)
    assert second["cwd"] == str(inherited_cwd)
    manager._drain_screen_now(active)
    await asyncio.sleep(0)
