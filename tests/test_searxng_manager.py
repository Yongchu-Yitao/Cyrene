"""Regression tests for the managed SimpleXNG lifecycle."""

from __future__ import annotations

import os
import sys
import types


class _FakeProcess:
    pid = 43210

    def poll(self):
        return None


class _ProcessThatExits:
    returncode = 1

    def __init__(self):
        self._polls = iter((None, 1))

    def poll(self):
        return next(self._polls, 1)


def test_start_uses_fallback_port_when_requested_port_is_occupied(monkeypatch, tmp_path):
    from cyrene.tooling.backends import searxng_manager as manager_module

    manager = manager_module.SearXNGManager()
    written = {}

    monkeypatch.setattr(manager_module, "SEARXNG_URL", "")
    monkeypatch.setattr(manager_module, "_is_port_available", lambda host, port: False)
    monkeypatch.setattr(manager_module, "_find_available_port", lambda host: 49152)
    monkeypatch.setattr(
        manager_module,
        "_write_simplexng_settings",
        lambda port, host: written.update(port=port, host=host) or tmp_path / "settings.yml",
    )
    monkeypatch.setattr(
        manager_module,
        "_build_simplexng_launch_cmd",
        lambda port, host, settings_path=None: ["simplexng", str(port)],
    )
    monkeypatch.setattr(manager_module, "_build_simplexng_env", lambda settings_path: {})
    monkeypatch.setattr(manager_module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(manager, "_wait_ready", lambda: True)

    assert manager.start(8888, "127.0.0.1") == "http://127.0.0.1:49152"
    assert written == {"port": 49152, "host": "127.0.0.1"}


def test_build_env_records_parent_pid(monkeypatch, tmp_path):
    from cyrene.tooling.backends import searxng_manager as manager_module

    monkeypatch.setattr(manager_module, "_get_effective_search_proxy", lambda: "")
    env = manager_module._build_simplexng_env(tmp_path / "settings.yml")

    assert env["CYRENE_SIMPLEXNG_PARENT_PID"] == str(os.getpid())


def test_source_launch_uses_parent_watching_wrapper(monkeypatch, tmp_path):
    from cyrene.tooling.backends import searxng_manager as manager_module

    monkeypatch.setattr(manager_module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        manager_module.importlib.util,
        "find_spec",
        lambda name: object() if name == "simplexng.simplexng" else None,
    )

    command = manager_module._build_simplexng_launch_cmd(
        8888,
        "127.0.0.1",
        settings_path=tmp_path / "settings.yml",
    )

    assert command[:3] == [manager_module.sys.executable, "-m", "cyrene.simplexng_child"]


def test_external_searxng_url_is_used_without_starting_child(monkeypatch):
    from cyrene.tooling.backends import searxng_manager as manager_module

    monkeypatch.setattr(manager_module, "SEARXNG_URL", "https://search.example.test/")
    monkeypatch.setattr(
        manager_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    manager = manager_module.SearXNGManager()

    assert manager.start() == "https://search.example.test"
    assert manager.is_running
    manager.stop()
    assert not manager.is_running


def test_search_prefers_external_searxng_url(monkeypatch):
    from cyrene.tooling.backends import search

    monkeypatch.setattr(search, "SEARXNG_URL", "https://search.example.test/")

    assert search._get_simplexng_url() == "https://search.example.test"


def test_search_only_bypasses_proxy_for_loopback_urls():
    from cyrene.tooling.backends import search

    assert search._is_loopback_url("http://127.0.0.1:8888")
    assert search._is_loopback_url("http://[::1]:8888")
    assert search._is_loopback_url("http://localhost:8888")
    assert not search._is_loopback_url("https://search.example.test")


def test_parent_identity_change_marks_parent_dead(monkeypatch):
    from cyrene.tooling.backends import simplexng_child

    monkeypatch.setattr(simplexng_child.os, "getppid", lambda: 222)
    monkeypatch.setattr(simplexng_child, "_pid_exists", lambda pid: True)

    assert not simplexng_child._parent_is_alive(111)


def test_simplexng_child_installs_windows_compat_patches(monkeypatch):
    from cyrene.tooling.backends import simplexng_child
    import multiprocessing

    original_uvloop = sys.modules.pop("uvloop", None)
    original_pwd = sys.modules.pop("pwd", None)
    fake_winloop = types.ModuleType("winloop")
    try:
        monkeypatch.setattr(simplexng_child.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winloop", fake_winloop)
        monkeypatch.setattr(multiprocessing, "get_context", lambda method=None: method)

        simplexng_child._install_windows_compat_patches()

        assert sys.modules["uvloop"] is fake_winloop
        assert sys.modules["pwd"].getpwuid(1000).pw_uid == 1000
        assert multiprocessing.get_context("fork") == "spawn"
        assert multiprocessing.get_context("spawn") == "spawn"
    finally:
        if original_uvloop is not None:
            sys.modules["uvloop"] = original_uvloop
        else:
            sys.modules.pop("uvloop", None)
        if original_pwd is not None:
            sys.modules["pwd"] = original_pwd
        else:
            sys.modules.pop("pwd", None)


def test_readiness_rejects_response_from_an_old_process(monkeypatch):
    from cyrene.tooling.backends import searxng_manager as manager_module

    manager = manager_module.SearXNGManager()
    manager._url = "http://127.0.0.1:8888"
    manager._process = _ProcessThatExits()

    class _Response:
        status_code = 200

    monkeypatch.setattr(manager_module.httpx, "get", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(manager_module.time, "sleep", lambda seconds: None)

    assert not manager._wait_ready()
