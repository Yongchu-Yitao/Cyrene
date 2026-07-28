from __future__ import annotations

import argparse
import json

import httpx
import pytest


def test_api_client_never_uses_environment_proxy(monkeypatch):
    from cyrene import cli

    observed = {}

    class FakeClient:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, url, **kwargs):
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(cli.httpx, "Client", FakeClient)

    assert cli._api_json("/api/status") == {"ok": True}
    assert observed["trust_env"] is False


def test_start_readiness_checks_never_use_environment_proxy(monkeypatch):
    from cyrene import cli

    calls = []
    launch = {}

    def local_get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            json={"sessions": []},
            request=httpx.Request("GET", url),
        )

    class FakeProcess:
        def kill(self):
            raise AssertionError("ready daemon must not be killed")

    monkeypatch.setattr(cli.httpx, "get", local_get)
    monkeypatch.setattr(cli, "_discover_daemon_url", lambda: "")
    monkeypatch.setattr(cli, "_allocate_daemon_port", lambda: 4242)
    def popen(*args, **kwargs):
        launch.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    cli.cmd_start(argparse.Namespace())

    assert len(calls) == 1
    assert all(kwargs["trust_env"] is False for _, kwargs in calls)
    assert launch["start_new_session"] is (cli.sys.platform != "win32")


def test_start_uses_an_alternate_port_when_default_port_is_unavailable(monkeypatch):
    from cyrene import cli

    def local_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"sessions": []},
            request=httpx.Request("GET", url),
        )

    launch = {}
    monkeypatch.setattr(cli.httpx, "get", local_get)
    monkeypatch.setattr(cli, "_discover_daemon_url", lambda: "")
    monkeypatch.setattr(cli, "_allocate_daemon_port", lambda: 4243)
    monkeypatch.setattr(cli, "_PROTECTED_DAEMON_PRESENT", False)

    class FakeProcess:
        def kill(self):
            raise AssertionError("ready daemon must not be killed")

    def popen(command, **kwargs):
        launch["command"] = command
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    url = cli.cmd_start(argparse.Namespace(), quiet=True)

    assert url == "http://127.0.0.1:4243"
    assert launch["command"][-2:] == ["--port", "4243"]


def test_start_never_launches_second_backend_for_legacy_electron(monkeypatch, capsys):
    from cyrene import cli

    def discover():
        cli._PROTECTED_DAEMON_PRESENT = True
        return ""

    monkeypatch.setattr(cli, "_discover_daemon_url", discover)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not launch a competing backend")
        ),
    )

    with pytest.raises(SystemExit) as exc:
        cli.cmd_start(argparse.Namespace())

    assert exc.value.code == 1
    assert "Restart Electron once" in capsys.readouterr().err


def test_cli_discovers_authenticated_electron_backend(monkeypatch, tmp_path):
    from cyrene import cli

    connection_path = tmp_path / "cli-connection.json"
    connection_path.write_text(
        json.dumps({
            "version": 1,
            "url": "http://127.0.0.1:4242",
            "token": "desktop-secret",
        }),
        encoding="utf-8",
    )
    connection_path.chmod(0o600)
    seen = {}

    def local_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(cli, "_desktop_connection_path", lambda: connection_path)
    monkeypatch.setattr(cli.httpx, "get", local_get)
    monkeypatch.setattr(cli, "DAEMON_TOKEN", "")

    assert cli._discover_daemon_url() == "http://127.0.0.1:4242"
    assert cli.DAEMON_TOKEN == "desktop-secret"
    assert seen["headers"] == {"X-Cyrene-Token": "desktop-secret"}


def test_electron_publishes_same_user_cli_connection():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "electron" / "main.js"
    ).read_text(encoding="utf-8")

    assert "function publishCliConnection(port)" in source
    assert "mode: 0o600" in source
    assert "token: AUTH_TOKEN" in source
    assert "publishCliConnection(port);" in source
    assert "clearCliConnection();" in source
