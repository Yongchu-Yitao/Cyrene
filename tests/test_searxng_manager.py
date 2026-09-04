"""Regression tests for the managed SimpleXNG lifecycle."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest
import yaml


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
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

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
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    monkeypatch.setattr(manager_module, "_get_effective_search_proxy", lambda: "")
    env = manager_module._build_simplexng_env(tmp_path / "settings.yml")

    assert env["CYRENE_SIMPLEXNG_PARENT_PID"] == str(os.getpid())


def test_build_env_removes_rejected_inherited_proxy(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )
    for key in proxy_keys:
        monkeypatch.setenv(key, "http://127.0.0.1:6578")
    monkeypatch.setattr(manager_module, "_get_effective_search_proxy", lambda: "")

    env = manager_module._build_simplexng_env(tmp_path / "settings.yml")

    assert all(key not in env for key in proxy_keys)


def test_managed_settings_enable_mainland_no_key_engines(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module
    import simplexng.settings as simplexng_settings

    template = tmp_path / "template.yml"
    template.write_text(
        yaml.safe_dump(
            {
                "use_default_settings": True,
                "general": {},
                "search": {"formats": ["html"]},
                "server": {},
                "outgoing": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "simplexng_settings.yml"
    monkeypatch.setattr(simplexng_settings, "get_bundled_template", lambda: template)
    monkeypatch.setattr(manager_module, "_SIMPLEXNG_SETTINGS_PATH", output)
    monkeypatch.setattr(manager_module, "_get_effective_search_proxy", lambda: "")
    monkeypatch.setattr(manager_module, "_is_windows_arm", lambda: False)

    assert manager_module._write_simplexng_settings(8888, "127.0.0.1") == output

    settings = yaml.safe_load(output.read_text(encoding="utf-8"))
    engines = {engine["name"]: engine for engine in settings["engines"]}
    assert engines["baidu"]["disabled"] is False
    assert engines["baidu"]["timeout"] == 10.0
    assert engines["sogou"]["disabled"] is False
    assert engines["sogou"]["timeout"] == 10.0
    assert engines["bing"]["disabled"] is False
    assert engines["bing"]["timeout"] == 10.0
    assert engines["bing"]["base_url"] == "https://www.bing.com/search"
    assert "proxies" not in settings["outgoing"]


def test_china_engine_overrides_preserve_existing_engine_settings():
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    settings = {
        "engines": [
            {"name": "baidu", "weight": 9, "disabled": True},
            {"name": "custom", "disabled": False},
        ]
    }

    manager_module._enable_china_search_engines(settings)

    engines = {engine["name"]: engine for engine in settings["engines"]}
    assert engines["baidu"] == {
        "name": "baidu",
        "timeout": 10.0,
        "weight": 9,
        "disabled": False,
    }
    assert engines["custom"] == {"name": "custom", "disabled": False}
    assert engines["sogou"]["disabled"] is False
    assert engines["bing"]["base_url"] == "https://www.bing.com/search"


def test_build_env_applies_proxy_to_every_standard_variable(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    proxy_url = "http://proxy.example.test:8080"
    monkeypatch.setattr(
        manager_module,
        "_get_effective_search_proxy",
        lambda: proxy_url,
    )
    env = manager_module._build_simplexng_env(tmp_path / "settings.yml")

    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        assert env[key] == proxy_url


def test_source_launch_uses_parent_watching_wrapper(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    monkeypatch.setattr(manager_module.sys, "frozen", False, raising=False)

    command = manager_module._build_simplexng_launch_cmd(
        8888,
        "127.0.0.1",
        settings_path=tmp_path / "settings.yml",
    )

    child_entrypoint = manager_module.Path(manager_module.__file__).with_name(
        "simplexng_child.py"
    ).resolve()
    assert command[:2] == [manager_module.sys.executable, str(child_entrypoint)]


def test_windows_arm_launches_only_the_x64_simplexng_sidecar(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    sidecar = tmp_path / "CyreneSimpleXNG.exe"
    sidecar.touch()
    monkeypatch.setattr(manager_module.sys, "platform", "win32")
    monkeypatch.setattr(manager_module, "platform_machine", lambda: "arm64")
    monkeypatch.setenv("CYRENE_X64_SIMPLEXNG_SIDECAR", str(sidecar))

    command = manager_module._build_simplexng_launch_cmd(
        8888,
        "127.0.0.1",
        settings_path=tmp_path / "settings.yml",
    )

    assert command == [
        str(sidecar), "-p", "8888", "-H", "127.0.0.1",
        "--settings", str(tmp_path / "settings.yml"),
    ]


def test_windows_arm_settings_request_includes_mainland_engines(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    sidecar = tmp_path / "CyreneSimpleXNG.exe"
    sidecar.touch()
    output = tmp_path / "simplexng_settings.yml"
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(json.loads(kwargs["input"]))
        output.write_text("server: {}\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(manager_module, "_SIMPLEXNG_SETTINGS_PATH", output)
    monkeypatch.setattr(manager_module, "_woa_simplexng_sidecar", lambda: sidecar)
    monkeypatch.setattr(manager_module, "_get_effective_search_proxy", lambda: "")
    monkeypatch.setattr(manager_module.subprocess, "run", fake_run)

    assert manager_module._write_simplexng_settings(8888, "127.0.0.1") == output
    engines = {engine["name"]: engine for engine in captured["engine_overrides"]}
    assert engines["baidu"]["disabled"] is False
    assert engines["sogou"]["disabled"] is False
    assert engines["bing"]["base_url"] == "https://www.bing.com/search"


def test_windows_arm_does_not_fall_back_to_in_process_simplexng(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    monkeypatch.setattr(manager_module.sys, "platform", "win32")
    monkeypatch.setattr(manager_module, "platform_machine", lambda: "arm64")
    monkeypatch.delenv("CYRENE_X64_SIMPLEXNG_SIDECAR", raising=False)
    monkeypatch.setattr(manager_module, "INSTALL_RESOURCES_DIR", "/missing")

    with pytest.raises(FileNotFoundError, match="x64 SimpleXNG sidecar"):
        manager_module._build_simplexng_launch_cmd(8888, "127.0.0.1")


@pytest.mark.asyncio
async def test_search_service_startup_failure_is_degraded(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    async def fail_startup(*_args, **_kwargs):
        raise RuntimeError("sidecar unavailable")

    monkeypatch.setattr(manager_module, "start_searxng", fail_startup)
    service = manager_module.WebSearchService()

    assert await service.startup_best_effort() == ""
    assert service.startup_error == "RuntimeError: sidecar unavailable"


def test_external_searxng_url_is_used_without_starting_child(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

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
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    monkeypatch.setattr(search, "SEARXNG_URL", "https://search.example.test/")

    assert search._get_simplexng_url() == "https://search.example.test"


def test_search_only_bypasses_proxy_for_loopback_urls():
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    assert search._is_loopback_url("http://127.0.0.1:8888")
    assert search._is_loopback_url("http://[::1]:8888")
    assert search._is_loopback_url("http://localhost:8888")
    assert not search._is_loopback_url("https://search.example.test")


def test_parent_identity_change_marks_parent_dead(monkeypatch):
    from cyrene import simplexng_child

    monkeypatch.setattr(simplexng_child.os, "getppid", lambda: 222)
    monkeypatch.setattr(simplexng_child, "_pid_exists", lambda pid: True)

    assert not simplexng_child._parent_is_alive(111)


def test_simplexng_child_installs_windows_compat_patches(monkeypatch):
    from cyrene import simplexng_child
    import multiprocessing

    fake_winloop = types.ModuleType("winloop")
    monkeypatch.delitem(sys.modules, "uvloop", raising=False)
    monkeypatch.delitem(sys.modules, "pwd", raising=False)
    monkeypatch.setattr(simplexng_child.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winloop", fake_winloop)
    monkeypatch.setattr(multiprocessing, "get_context", lambda method=None: method)

    simplexng_child._install_windows_compat_patches()

    assert sys.modules["uvloop"] is fake_winloop
    assert sys.modules["pwd"].getpwuid(1000).pw_uid == 1000
    assert multiprocessing.get_context("fork") == "spawn"
    assert multiprocessing.get_context("spawn") == "spawn"


def test_readiness_rejects_response_from_an_old_process(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_service as manager_module

    manager = manager_module.SearXNGManager()
    manager._url = "http://127.0.0.1:8888"
    manager._process = _ProcessThatExits()

    class _Response:
        status_code = 200

    monkeypatch.setattr(manager_module.httpx, "get", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(manager_module.time, "sleep", lambda seconds: None)

    assert not manager._wait_ready()
