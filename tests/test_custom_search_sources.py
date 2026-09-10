from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import yaml

from cyrene.plugins.builtin.cyrene_content import custom_search_sources as custom


def source(**updates):
    return {"id": "custom-" + "a" * 32, "name": "Team search", "type": "json",
            "search_url": "https://example.test/search?q={query}",
            "results_path": "data/results", "title_path": "title", "url_path": "url",
            "content_path": "description", **updates}


@pytest.mark.parametrize("updates", [
    {"search_url": "file:///tmp/{query}"}, {"search_url": "https://{query}/"},
    {"search_url": "https://user:password@example.test?q={query}"},
    {"search_url": "https://example.test?q={query.__class__}"},
    {"search_url": "https://example.test/search"}, {"method": "DELETE"},
    {"type": "python"}, {"type": "html", "title_path": "//*["},
    {"auth_header": "Authorization", "auth_value": "key\nInjected: value"},
    {"method": "POST", "request_body": "not-json"},
])
def test_custom_sources_validate_before_saving(updates):
    with pytest.raises(custom.CustomSourceError):
        custom.normalize_sources([source(**updates)], [])


def test_credentials_are_redacted_preserved_and_clearable(monkeypatch):
    saved = custom.normalize_sources([source(auth_header="Authorization", auth_value="Bearer secret")], [])
    monkeypatch.setattr(custom, "stored_sources", lambda: saved)
    public = custom.public_sources()
    assert "secret" not in str(public)
    assert public[0]["auth_configured"] is True
    assert custom.normalize_sources(public, saved) == saved
    assert custom.normalize_sources([{**public[0], "clear_auth": True}], saved)[0]["auth_value"] == ""
    engines = custom.engine_definitions()
    assert engines[0]["headers"] == {"Authorization": "Bearer secret"}
    assert engines[0]["results_query"] == "data/results"
    assert engines[0]["shortcut"] == saved[0]["id"]


@pytest.fixture
def settings_state(monkeypatch):
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_content import plugin_pack, search_settings
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")
    state = {"search": {}, "enabled_plugins": {"WebSearch": True}}
    monkeypatch.setattr(search_settings.config_store, "get_setting", lambda key, default=None: state.get(key, default))
    monkeypatch.setattr(search_settings.config_store, "get_settings_revision", lambda: 1)
    monkeypatch.setattr(search_settings.config_store, "get_enabled_plugins", lambda: dict(state["enabled_plugins"]))
    monkeypatch.setattr(search_settings.config_store, "get_env", lambda *args: "")
    monkeypatch.setattr(search_settings, "_custom_sources_supported", lambda: True)
    def update(changes, env, **kwargs):
        state.update(changes)
        return 1, dict(state)
    monkeypatch.setattr(search_settings.config_store, "update_settings_and_env_atomic", update)
    return state, registry


async def test_custom_source_changes_reload_but_switches_do_not(settings_state, monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_settings, search_service
    state, registry = settings_state
    calls = []
    async def restart(*args):
        calls.append(args)
    monkeypatch.setattr(search_service, "get_search_service", lambda: SimpleNamespace(restart=restart))
    async def publish(*args):
        pass
    service = search_settings.SearchSettingsApplicationService(registry, "WebSearch", publish)
    body = {"enabled": True, "providers": [{"id": name, "enabled": True} for name in search_settings.PROVIDER_IDS]}
    body["providers"][0]["custom_sources"] = [source()]
    result = await service.update_settings(body)
    assert len(calls) == 1
    assert result["providers"][0]["custom_sources"][0]["name"] == "Team search"
    body["providers"][0]["engines"] = []
    await service.update_settings(body)
    assert len(calls) == 1
    body["providers"][0]["custom_sources"] = []
    await service.update_settings(body)
    assert len(calls) == 2
    assert state["search"]["custom_sources"] == []


async def test_reload_failure_reports_saved_configuration(settings_state, monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_settings, search_service
    _, registry = settings_state
    async def restart(*args):
        raise RuntimeError("could contain secret")
    monkeypatch.setattr(search_service, "get_search_service", lambda: SimpleNamespace(restart=restart))
    async def publish(*args):
        pass
    service = search_settings.SearchSettingsApplicationService(registry, "WebSearch", publish)
    body = {"enabled": True, "providers": [{"id": name, "enabled": True} for name in search_settings.PROVIDER_IDS]}
    body["providers"][0]["custom_sources"] = [source()]
    result = await service.update_settings(body)
    assert result["ok"]
    assert result["runtime_warning"] == "custom_sources_restart_failed"
    assert "could contain secret" not in str(result)


def test_real_simplexng_custom_json_html_and_authenticated_post(tmp_path, monkeypatch):
    """Exercise the actual vendored adapters and child hook against local fixtures."""
    captured = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            captured.append(("GET", parse_qs(urlsplit(self.path).query), self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if self.path.startswith("/html") else "application/json")
            self.end_headers()
            if self.path.startswith("/html"):
                self.wfile.write(b'<article><h2>HTML title</h2><a href="https://example.test/html">Link</a><p>HTML snippet</p></article>')
            else:
                self.wfile.write(json.dumps({"data": {"results": [{"title": "JSON title", "url": "https://example.test/json", "description": "JSON snippet"}]}}).encode())
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append(("POST", json.loads(data), self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"results": [{"title": "POST title", "url": "https://example.test/post"}]}}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    sources = custom.normalize_sources([
        source(search_url=base + "/json?q={query}", auth_header="Authorization", auth_value="Bearer fixture"),
        source(id="custom-" + "b" * 32, type="html", search_url=base + "/html?q={query}", results_path="//article", title_path=".//h2", url_path=".//a/@href", content_path=".//p"),
        source(id="custom-" + "c" * 32, search_url=base + "/post", method="POST", request_body='{{"q":"{query}"}}', auth_header="Authorization", auth_value="Bearer fixture"),
    ], [])
    monkeypatch.setattr(custom, "stored_sources", lambda: sources)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = {"use_default_settings": {"engines": {"keep_only": []}},
                "server": {"secret_key": "custom-source-test-secret", "limiter": False},
                "search": {"formats": ["html", "json"]}, "engines": custom.engine_definitions()}
    path = tmp_path / "settings.yml"
    path.write_text(yaml.safe_dump(settings))
    env = dict(os.environ, SEARXNG_SETTINGS_PATH=str(path), CYRENE_SIMPLEXNG_PARENT_PID=str(os.getpid()), XDG_CACHE_HOME=str(tmp_path / "cache"))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    session = requests.Session()
    session.trust_env = False
    with (tmp_path / "child.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "cyrene.simplexng_child", "--settings", str(path), "-p", str(port), "-H", "127.0.0.1"], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / "child.log").read_text()
                try:
                    if session.get(f"http://127.0.0.1:{port}/config", timeout=1).ok:
                        break
                except requests.RequestException:
                    time.sleep(0.1)
            else:
                pytest.fail("Custom search child failed to start")
            query = '中文 "quoted" & value'
            response = session.get(f"http://127.0.0.1:{port}/search", params={"q": query, "format": "json", "engines": ",".join(row["id"] for row in sources)}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            assert {item["title"] for item in payload["results"]} == {"JSON title", "HTML title", "POST title"}, payload
            assert ("POST", {"q": query}, "Bearer fixture") in captured
            assert ("GET", {"q": [query]}, "Bearer fixture") in captured
        finally:
            process.terminate()
            process.wait(timeout=10)
            session.close()
            server.shutdown()
            server.server_close()
