from __future__ import annotations

import argparse

import httpx


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
        if url.endswith("/api/status"):
            raise httpx.ConnectError(
                "not running",
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json={"sessions": []},
            request=httpx.Request("GET", url),
        )

    class FakeProcess:
        def kill(self):
            raise AssertionError("ready daemon must not be killed")

    monkeypatch.setattr(cli.httpx, "get", local_get)
    def popen(*args, **kwargs):
        launch.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    cli.cmd_start(argparse.Namespace())

    assert len(calls) == 2
    assert all(kwargs["trust_env"] is False for _, kwargs in calls)
    assert launch["start_new_session"] is (cli.sys.platform != "win32")
