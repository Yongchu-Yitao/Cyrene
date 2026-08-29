"""Focused tests for phase-1 external Agent extension support.

Covers the recommended Agent catalog, manifest proposal validation and
idempotency, confirmation into the async install pipeline without shell
execution, installed enumeration from installation state (including
non-recommended external Agents), and the /api/agents runtime/settings
routes with safe placeholder states.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def _inline_manifest(**overrides):
    manifest = {
        "manifestApi": "cyrene.agent/v1",
        "agentId": "my-agent",
        "displayName": "My Agent",
        "version": "1.2.3",
        "driver": "acp_stdio",
        "command": "my-agent",
        "protocolVersion": 1,
        "publisher": "Example",
        "description": "An external Agent.",
        "capabilities": {
            "session": {"load": "supported", "fork": "unsupported"},
            "model": {"agentManaged": "supported", "cyreneManaged": ["openai_chat"]},
        },
    }
    manifest.update(overrides)
    return manifest


@pytest.fixture
def saved_settings(monkeypatch):
    saved = {}
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service

    monkeypatch.setattr(agent_runtime, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(agent_runtime, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(service, "get_setting", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr(service, "set_setting", lambda key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(agent_runtime, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    return saved


class FakeTasks:
    def __init__(self):
        self.created = []
        self.started = []

    def create(self, **kwargs):
        task = {"id": "task-agent-1", **kwargs}
        self.created.append(task)
        return task

    def start(self, task, manager, worker):
        self.started.append((task, manager, worker))

    def update(self, task_id, **changes):
        return None

    def list(self):
        return list(self.created)


def _service(tasks=None):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    extension_service = object.__new__(service_module.ExtensionService)
    extension_service.tasks = tasks or FakeTasks()
    return extension_service


def test_recommended_agent_catalog_has_pinned_registry_distributions():
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS, RECOMMENDED_AGENT_ORDER

    assert RECOMMENDED_AGENT_ORDER == ("opencode", "codex-acp", "pi-acp")
    for agent_id in RECOMMENDED_AGENT_ORDER:
        spec = RECOMMENDED_AGENTS[agent_id]
        assert spec["kind"] == "agent"
        assert spec["driver"] == "acp_stdio"
        assert spec["version_source"] == "acp_registry"
        distribution = spec["distribution"]
        if distribution["kind"] == "binary":
            assert all(item["url"].startswith("https://") and len(item["sha256"]) == 64 for item in distribution["platforms"].values())
        else:
            assert distribution["kind"] == "npm"
            assert "@" in distribution["package"]


@pytest.mark.asyncio
async def test_agent_search_returns_no_results():
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    extension_service = object.__new__(service_module.ExtensionService)
    result = await extension_service.search("agent", "anything", advanced=True)
    assert result == {"results": [], "source": "none", "next_cursor": "", "note": "agent_search_disabled"}


@pytest.mark.asyncio
async def test_inline_proposal_creates_pending_validation_and_is_idempotent(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    service = _service()
    first = await service.create_agent_install_proposal({"type": "inline", "manifest": _inline_manifest()})
    assert first["ok"] is True
    assert first["proposalId"].startswith("agent_prop_")
    assert first["agentId"] == "my-agent"
    assert first["sourceTrust"] == "external_unverified"
    assert first["requiresConfirmation"] is True
    assert first["status"] == "pending"
    assert first["inspect"]["driver"] == "acp_stdio"
    assert first["inspect"]["command"] == "my-agent"
    assert first["inspect"]["checksums"] == {}

    second = await service.create_agent_install_proposal({"type": "inline", "manifest": _inline_manifest()})
    assert second["proposalId"] == first["proposalId"]
    assert second["alreadyPending"] is True

    assert len(agent_runtime.list_agent_proposals()) == 1


@pytest.mark.asyncio
async def test_inline_proposal_rejects_invalid_manifests_and_sources(saved_settings):
    service = _service()
    cases = [
        _inline_manifest(manifestApi="vendor/v2"),
        _inline_manifest(agentId="Bad ID"),
        _inline_manifest(command="bash -c 'evil'"),
        _inline_manifest(command="../evil"),
        _inline_manifest(driver="http"),
        _inline_manifest(version="1.0 ../x"),
    ]
    for manifest in cases:
        with pytest.raises(ValueError, match="agent_manifest_invalid"):
            await service.create_agent_install_proposal({"type": "inline", "manifest": manifest})

    with pytest.raises(ValueError, match="proposal_source_invalid"):
        await service.create_agent_install_proposal({"type": "manifest_url", "url": "http://example.com/cyrene-agent.json"})
    with pytest.raises(ValueError, match="proposal_source_invalid"):
        await service.create_agent_install_proposal({"type": "archive", "url": "https://example.com/a.tar.gz"})
    with pytest.raises(ValueError, match="proposal_source_invalid"):
        await service.create_agent_install_proposal({"type": "inline"})

    # Unsupported model access modes fall back to the safe Cyrene-managed default.
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    fallback = await service.create_agent_install_proposal(
        {"type": "inline", "manifest": _inline_manifest(modelAccess={"mode": "everything"})}
    )
    proposal = agent_runtime.get_agent_proposal(fallback["proposalId"])
    assert proposal["manifest"]["modelAccess"]["mode"] == "cyrene_managed"


class _FakeStreamResponse:
    def __init__(self, content, *, is_redirect=False, location=""):
        self.content = content
        self.is_redirect = is_redirect
        self.headers = {"location": location} if location else {}

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        step = 64
        for offset in range(0, len(self.content), step):
            yield self.content[offset : offset + step]


class _FakeStreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_args):
        return None


class _FakeClient:
    """httpx.AsyncClient stand-in exposing the streaming ``stream()`` API."""

    def __init__(self, *, responses=None, **_kwargs):
        if responses is None:
            responses = [json.dumps(_inline_manifest()).encode()]
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, method, url):
        item = self._responses[0]
        if len(self._responses) > 1:
            self._responses.pop(0)
        if isinstance(item, tuple):
            content, response_kwargs = item
        else:
            content, response_kwargs = item, {}
        return _FakeStreamContext(_FakeStreamResponse(content, **response_kwargs))


@pytest.mark.asyncio
async def test_manifest_url_proposal_fetches_and_validates(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", _FakeClient)
    service = _service()
    result = await service.create_agent_install_proposal({"type": "manifest_url", "url": "https://example.com/cyrene-agent.json"})
    assert result["ok"] is True
    assert result["agentId"] == "my-agent"
    assert result["source"] == {"type": "manifest_url", "url": "https://example.com/cyrene-agent.json"}


@pytest.mark.asyncio
async def test_manifest_url_fetch_streams_and_enforces_size_cap(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    monkeypatch.setattr(
        agent_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses=[b"x" * (agent_runtime._MAX_MANIFEST_BYTES + 1)]),
    )
    service = _service()
    with pytest.raises(ValueError, match="exceeds the 1 MiB size limit"):
        await service.create_agent_install_proposal({"type": "manifest_url", "url": "https://example.com/big.json"})


@pytest.mark.asyncio
async def test_manifest_url_redirect_targets_are_ssrf_validated(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    monkeypatch.setattr(
        agent_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses=[(b"", {"is_redirect": True, "location": "http://127.0.0.1/private.json"})]),
    )
    service = _service()
    with pytest.raises(ValueError, match="proposal_source_invalid"):
        await service.create_agent_install_proposal({"type": "manifest_url", "url": "https://example.com/cyrene-agent.json"})


@pytest.mark.asyncio
async def test_manifest_url_redirect_is_followed_and_fetched(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    monkeypatch.setattr(
        agent_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses=[
            (b"", {"is_redirect": True, "location": "https://example.com/final.json"}),
            json.dumps(_inline_manifest()).encode(),
        ]),
    )
    service = _service()
    result = await service.create_agent_install_proposal({"type": "manifest_url", "url": "https://example.com/cyrene-agent.json"})
    assert result["ok"] is True
    assert result["agentId"] == "my-agent"


@pytest.mark.asyncio
async def test_manifest_auth_sanitized_by_declarative_allowlist(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    service = _service()
    result = await service.create_agent_install_proposal({"type": "inline", "manifest": _inline_manifest(auth={
        "type": "oauth2",
        "method": "authorization_code",
        "label": "Sign in",
        "hint": "Browser flow",
        "key": "sk-secret",
        "bearer": "Bearer abc",
        "accessKey": "AKIAEXAMPLE",
        "access_key": "AKIAEXAMPLE",
        "token": "tok-123",
        "apiKey": "k",
        "nested": {"bearer": "x"},
    })})
    proposal = agent_runtime.get_agent_proposal(result["proposalId"])
    auth = proposal["manifest"]["auth"]
    assert set(auth) == {"type", "method", "label", "hint"}
    assert "key" not in auth
    assert "bearer" not in auth
    assert "accessKey" not in auth
    assert "token" not in auth


@pytest.mark.asyncio
async def test_direct_manifest_install_without_proposal_is_rejected(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    service = _service()
    with pytest.raises(ValueError, match="agent_install_invalid"):
        await service._install_agent("task-agent-1", "my-agent", {"manifest": _inline_manifest()}, "user")
    assert agent_runtime.find_installation_by_agent_id("my-agent") is None


@pytest.mark.asyncio
async def test_failed_proposal_install_is_retryable(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    service = _service()
    proposal = await service.create_agent_install_proposal({"type": "inline", "manifest": _inline_manifest()})
    confirmed = await service.confirm_agent_install_proposal(proposal["proposalId"])
    assert confirmed["ok"] is True
    task, manager, worker = service.tasks.started[0]
    assert manager == "agent"

    calls = {"count": 0}
    original = agent_runtime.register_agent_installation

    def flaky_register(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated install failure")
        return original(**kwargs)

    monkeypatch.setattr(agent_runtime, "register_agent_installation", flaky_register)
    with pytest.raises(RuntimeError, match="simulated install failure"):
        await worker(task["id"])
    assert agent_runtime.get_agent_proposal(proposal["proposalId"])["status"] == "pending"

    retry = await service.confirm_agent_install_proposal(proposal["proposalId"])
    assert retry["ok"] is True
    assert len(service.tasks.started) == 2
    second_task, manager, second_worker = service.tasks.started[1]
    result = await second_worker(second_task["id"])
    assert result["installed"] is True
    assert agent_runtime.get_agent_proposal(proposal["proposalId"])["status"] == "confirmed"


@pytest.mark.asyncio
async def test_run_manager_timeout_terminates_then_kills_child(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    service = _service()

    class TimeoutProc:
        def __init__(self, *, wait_stuck=False):
            self.terminate_calls = 0
            self.kill_calls = 0
            self.wait_calls = 0
            self.wait_stuck = wait_stuck

        async def communicate(self):
            raise asyncio.TimeoutError()

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1

        async def wait(self):
            self.wait_calls += 1
            if self.wait_stuck and self.wait_calls == 1:
                raise asyncio.TimeoutError()
            return 0

    stuck = TimeoutProc(wait_stuck=True)
    async def create_stuck_process(*_args, **_kwargs):
        return stuck

    monkeypatch.setattr(service_module.asyncio, "create_subprocess_exec", create_stuck_process)
    with pytest.raises(RuntimeError, match="timed out"):
        await service._run_manager("task-1", ["cmd"], env={}, timeout=0.1)
    assert stuck.terminate_calls == 1
    assert stuck.kill_calls == 1
    assert stuck.wait_calls == 2


@pytest.mark.asyncio
async def test_download_enforces_pinned_sha256_even_when_verification_disabled(saved_settings, monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    saved_settings["extension_sources"] = {"verify_signatures": False}
    service = _service()

    class DownloadResponse:
        def __init__(self, content):
            self.content = content
            self.headers = {"content-length": str(len(content))}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            for offset in range(0, len(self.content), 4096):
                yield self.content[offset : offset + 4096]

    class DownloadStream:
        def __init__(self, content):
            self.content = content

        async def __aenter__(self):
            return DownloadResponse(self.content)

        async def __aexit__(self, *_args):
            return None

    class DownloadClient:
        def __init__(self, content, **_kwargs):
            self.content = content

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url):
            return DownloadStream(self.content)

    payload = b"archive bytes"
    monkeypatch.setattr(service_module.httpx, "AsyncClient", lambda **kwargs: DownloadClient(payload))
    destination = tmp_path / "download.bin"
    with pytest.raises(RuntimeError, match="checksum does not match"):
        await service._download("task-1", "https://example.com/file.bin", destination, expected_sha256="0" * 64)

    expected = hashlib.sha256(payload).hexdigest()
    actual = await service._download("task-1", "https://example.com/file.bin", destination, expected_sha256=expected)
    assert actual == expected
    assert destination.read_bytes() == payload


@pytest.mark.asyncio
async def test_recommended_npm_agent_same_version_install_is_idempotent(saved_settings, monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    monkeypatch.setattr(service_module, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(service_module, "_AGENT_DIR", tmp_path / "agents")
    monkeypatch.setattr(service_module, "_AGENT_BIN_DIR", tmp_path / "agents" / "bin")

    install_root = tmp_path / "agents" / "pi-acp" / "0.0.33"
    destination = install_root / "node_modules" / ".bin" / "pi-acp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"#!/usr/bin/env node\n")
    # The pi-acp profile declares the ``pi`` runtime as a dependency; the
    # idempotency short-circuit requires that shim to exist as well.
    (install_root / "node_modules" / ".bin" / "pi").write_bytes(b"#!/usr/bin/env node\n")

    calls = []

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        calls.append(command)

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    path, checksum = await service._install_recommended_agent("task-1", "pi-acp", profile)
    assert path == str(destination)
    assert checksum == ""
    assert calls == []


@pytest.mark.asyncio
async def test_recommended_npm_agent_install_does_not_claim_self_checksum(saved_settings, monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module
    from cyrene.plugins.builtin.cyrene_extensions.extension_catalog import RECOMMENDED_AGENTS

    monkeypatch.setattr(service_module, "_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(service_module, "_AGENT_DIR", tmp_path / "agents")
    monkeypatch.setattr(service_module, "_AGENT_BIN_DIR", tmp_path / "agents" / "bin")
    monkeypatch.setattr(service_module.shutil, "which", lambda *_args, **_kwargs: "npm")

    async def fake_run_manager(task_id, command, *, env, timeout=1800):
        assert command[0] == "npm"
        bin_dir = tmp_path / "staging" / task_id / "agent" / "npm" / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / "pi-acp").write_bytes(b"#!/usr/bin/env node\n")
        # The pi-acp profile bundles the ``pi`` runtime as a dependency.
        (bin_dir / "pi").write_bytes(b"#!/usr/bin/env node\n")

    service = _service()
    monkeypatch.setattr(service, "_run_manager", fake_run_manager)

    profile = RECOMMENDED_AGENTS["pi-acp"]
    path, checksum = await service._install_recommended_agent("task-1", "pi-acp", profile)
    assert path.endswith("pi-acp/0.0.33/node_modules/.bin/pi-acp")
    assert checksum == ""
    assert (tmp_path / "agents" / "pi-acp" / "0.0.33" / "node_modules" / ".bin" / "pi-acp").is_file()


@pytest.mark.asyncio
async def test_confirm_proposal_runs_async_pipeline_without_shell(saved_settings, monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    monkeypatch.setattr(service_module.asyncio, "create_subprocess_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("agent install must not execute a shell")))
    monkeypatch.setattr(service_module, "_STAGING_DIR", tmp_path / "staging")

    service = _service()
    proposal = await service.create_agent_install_proposal({"type": "inline", "manifest": _inline_manifest()})
    confirmed = await service.confirm_agent_install_proposal(proposal["proposalId"])
    assert confirmed["ok"] is True
    task, manager, worker = service.tasks.started[0]
    assert task["kind"] == "agent"
    assert task["extension_id"] == "my-agent"
    assert manager == "agent"

    result = await worker(task["id"])
    assert result["installed"] is True
    assert result["installation_id"] == "agent_my-agent_default"
    assert result["blocked_reason"] == ""

    record = agent_runtime.get_agent_installation("agent_my-agent_default")
    assert record is not None
    assert record["source_trust"] == "external_unverified"
    assert record["runtime_state"] == "not_started"
    assert record["recommended"] is False
    assert agent_runtime.get_agent_proposal(proposal["proposalId"])["status"] == "confirmed"

    again = await service.confirm_agent_install_proposal(proposal["proposalId"])
    assert again["already_installed"] is True
    assert len(service.tasks.started) == 1


@pytest.mark.asyncio
async def test_recommended_agent_install_records_managed_verified_artifact(saved_settings, monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime
    from cyrene.plugins.builtin.cyrene_extensions import extension_service as service_module

    monkeypatch.setattr(service_module, "_STAGING_DIR", tmp_path / "staging")
    service = _service()

    async def fake_install(_task_id, agent_id, profile):
        assert agent_id == "opencode"
        assert profile["distribution"]["kind"] == "binary"
        return str(tmp_path / "agents" / "opencode"), "a" * 64

    monkeypatch.setattr(service, "_install_recommended_agent", fake_install)
    result = await service._install_agent("task-agent-1", "opencode", {}, "user")
    assert result["installed"] is True
    record = agent_runtime.find_installation_by_agent_id("opencode")
    assert record["managed_path"].endswith("opencode")
    assert record["checksum"] == "a" * 64
    assert record["runtime_state"] == "not_started"


def test_legacy_pending_transport_installation_migrates_to_on_demand(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    saved_settings[agent_runtime.INSTALLATIONS_KEY] = [{
        "installation_id": "agent_legacy_default",
        "agent_id": "legacy",
        "runtime_state": "pending_transport",
    }]
    records = agent_runtime.list_agent_installations()
    assert records[0]["runtime_state"] == "not_started"
    assert agent_runtime.agent_card(records[0])["runtimeState"] == "not_started"


@pytest.mark.asyncio
async def test_successful_probe_refreshes_capabilities_and_card(saved_settings, monkeypatch):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    record = agent_runtime.register_agent_installation(
        agent_id="probe-agent",
        manifest=agent_runtime.validate_agent_manifest(_inline_manifest(
            agentId="probe-agent",
            command="probe-agent",
            modelAccess={"mode": "agent_managed"},
            capabilities={},
        )),
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )

    class Transport:
        async def initialize(self):
            return {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": True},
                    "sessionCapabilities": {"close": {}, "fork": {}},
                },
            }

    class Connection:
        transport = Transport()

        async def close(self):
            return None

    class Driver:
        async def connect(self, _request):
            return Connection()

    class Runtime:
        def driver(self):
            return Driver()

        async def close_all(self):
            return None

    monkeypatch.setattr("cyrene.agent_runtime.get_acp_runtime_service", lambda: Runtime())
    result = await agent_runtime.probe_agent(record["installation_id"])
    assert result["ok"] is True
    assert result["runtimeState"] == "not_started"
    assert result["agent"]["health"] == "healthy"
    assert result["agent"]["capabilities"]["session"] == {
        "load": "supported", "fork": "supported", "close": "supported",
    }
    assert result["agent"]["capabilities"]["input"]["image"] == "supported"
    assert result["agent"]["negotiatedCapabilities"]["loadSession"] is True


@pytest.mark.asyncio
async def test_agent_enable_disable_and_uninstall(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime

    service = _service()
    agent_runtime.register_agent_installation(
        agent_id="external-tool",
        manifest=agent_runtime.validate_agent_manifest(_inline_manifest(agentId="external-tool")),
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )
    installation_id = "agent_external-tool_default"

    result = await service.set_extension_enabled("agent", installation_id, False)
    assert result["ok"] is True and result["enabled"] is False
    assert agent_runtime.get_agent_installation(installation_id)["enabled"] is False

    uninstalled = await service.uninstall("agent", installation_id)
    assert uninstalled["ok"] is True
    assert agent_runtime.get_agent_installation(installation_id) is None


def test_agents_route_endpoints_with_runtime_states(saved_settings):
    from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime
    from cyrene.plugins.builtin.cyrene_extensions.agent_routes import register_agent_routes

    agent_runtime.register_agent_installation(
        agent_id="external-tool",
        manifest=agent_runtime.validate_agent_manifest(_inline_manifest(agentId="external-tool")),
        source={"type": "inline"},
        source_trust="external_unverified",
        recommended=False,
    )
    router = APIRouter()
    register_agent_routes(router, None, "")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/agents")
    assert response.status_code == 200
    assert [item["agentId"] for item in response.json()["agents"]] == ["external-tool"]

    response = client.get("/api/agents/agent_external-tool_default")
    assert response.status_code == 200
    body = response.json()["agent"]
    assert body["runtimeState"] == "not_started"
    assert body["runtime"]["reason"] == "starts_on_demand"
    assert body["diagnostics"]["noteCode"] == "starts_on_demand"
    assert body["authState"] == "not_configured"

    response = client.patch("/api/agents/agent_external-tool_default/settings", json={"modelAccess": {"mode": "agent_managed"}})
    assert response.status_code == 200
    assert response.json()["agent"]["modelAccess"]["mode"] == "agent_managed"

    response = client.patch("/api/agents/agent_external-tool_default/settings", json={"modelAccess": {"mode": "unsafe"}})
    assert response.status_code == 400

    response = client.patch("/api/agents/agent_external-tool_default/settings", json={"modelAccess": {"mode": "cyrene_managed", "profileId": "missing"}})
    assert response.status_code == 400

    response = client.post("/api/agents/agent_external-tool_default/probe")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["failureKind"] == "dependency_missing"

    response = client.post("/api/agents/agent_external-tool_default/restart")
    assert response.json()["ok"] is True

    response = client.post("/api/agents/agent_external-tool_default/auth/start")
    assert response.json()["error"] == "dependency_missing"

    response = client.get("/api/agents/agent_external-tool_default/diagnostics")
    assert response.status_code == 200
    assert response.json()["runtimeState"] == "not_started"

    response = client.get("/api/agents/does-not-exist")
    assert response.status_code == 404
