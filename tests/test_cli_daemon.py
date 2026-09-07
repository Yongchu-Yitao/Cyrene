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
            json={"service": "cyrene", "status": "ok", "sessions": []},
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

    assert [url for url, _ in calls] == [
        "http://127.0.0.1:4242/api/health", "http://127.0.0.1:4242/api/ui-data",
    ]
    assert all(kwargs["trust_env"] is False for _, kwargs in calls)
    assert launch["start_new_session"] is (cli.sys.platform != "win32")


def test_start_uses_an_alternate_port_when_default_port_is_unavailable(monkeypatch):
    from cyrene import cli

    def local_get(url, **kwargs):
        assert url == "http://127.0.0.1:4243/api/health"
        return httpx.Response(
            200,
            json={"service": "cyrene", "status": "ok", "sessions": []},
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
            json={"service": "cyrene", "status": "ok"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(cli, "_desktop_connection_path", lambda: connection_path)
    monkeypatch.setattr(cli.httpx, "get", local_get)
    monkeypatch.setattr(cli, "DAEMON_TOKEN", "")

    assert cli._discover_daemon_url() == "http://127.0.0.1:4242"
    assert cli.DAEMON_TOKEN == "desktop-secret"
    assert seen["url"] == "http://127.0.0.1:4242/api/health"
    assert seen["headers"] == {"X-Cyrene-Token": "desktop-secret"}


def test_electron_publishes_same_user_cli_connection():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "electron" / "main.js"
    ).read_text(encoding="utf-8")

    assert "function publishCliConnection(port)" in source
    assert "mode: 0o600" in source
    assert "token: AUTH_TOKEN" in source
    assert "publishConnection: publishCliConnection" in source
    assert "clearConnection: clearCliConnection" in source


@pytest.mark.parametrize("payload", [{"ok": True}, [], None, {"service": "other", "status": "ok"}])
def test_discovery_rejects_unrelated_http_servers(monkeypatch, payload):
    from cyrene import cli

    monkeypatch.setattr(cli, "_read_desktop_connection", lambda: None)
    monkeypatch.setattr(cli, "_CLI_PORT_RANGE", [4242])
    monkeypatch.setattr(cli, "_port_is_open", lambda port: True)
    monkeypatch.setattr(cli.httpx, "get", lambda *a, **kw: httpx.Response(200, json=payload))
    assert cli._discover_daemon_url() == ""


def test_discovery_preserves_protected_desktop_detection(monkeypatch):
    from cyrene import cli

    paths = []

    def get(url, **kwargs):
        paths.append(url)
        if url.endswith("/api/health"):
            return httpx.Response(401)
        return httpx.Response(200, json={"instance_id": "desktop"})

    monkeypatch.setattr(cli, "_read_desktop_connection", lambda: None)
    monkeypatch.setattr(cli, "_CLI_PORT_RANGE", [4242])
    monkeypatch.setattr(cli, "_port_is_open", lambda port: True)
    monkeypatch.setattr(cli, "_PROTECTED_DAEMON_PRESENT", False)
    monkeypatch.setattr(cli.httpx, "get", get)
    assert cli._discover_daemon_url() == ""
    assert cli._PROTECTED_DAEMON_PRESENT is True
    assert paths == ["http://127.0.0.1:4242/api/health", "http://127.0.0.1:4242/api/instance-id"]


def test_readiness_retries_until_authenticated_cyrene_is_ready(monkeypatch):
    from cyrene.platform import daemon_health

    responses = iter([httpx.Response(200, text="<html>other server</html>"),
                      httpx.Response(401), httpx.Response(200, json={"service": "cyrene", "status": "ok"})])
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(daemon_health.httpx, "get", get)
    monkeypatch.setattr(daemon_health.time, "sleep", lambda seconds: None)
    assert daemon_health.wait_for_daemon("http://127.0.0.1:4242", {"X-Cyrene-Token": "secret"})
    assert len(calls) == 3
    assert all(url.endswith("/api/health") and kw["headers"] == {"X-Cyrene-Token": "secret"}
               for url, kw in calls)
