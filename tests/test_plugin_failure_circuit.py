"""Structured Plugin failure and circuit-breaker integration tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from cyrene.core.plugin import (
    Plugin,
    PluginContext,
    PluginExecutionError,
    PluginFailure,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
)
from cyrene.core.session import AgentSession


def run(awaitable):
    return asyncio.run(awaitable)


def test_runtime_enforces_declared_run_plugin_circuit():
    calls = 0

    def unavailable(_arguments, _context):
        nonlocal calls
        calls += 1
        raise PluginExecutionError(
            PluginFailure(
                error_code="backend_unavailable",
                message="backend unavailable",
                retryable=True,
                retry_scope="after_delay",
                retry_after_ms=30_000,
                circuit_scope="run_plugin",
                details={"provider_health": [{"provider": "one", "state": "open"}]},
            )
        )

    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin("Lookup", "lookup", {"type": "object"}, unavailable),
        source="test",
    )
    runtime = PluginRuntime(registry)
    same_run = PluginContext(data={"run_id": "run-one"})

    first = run(runtime.call("Lookup", {}, same_run))
    blocked = run(runtime.call("Lookup", {}, same_run))
    next_run = run(
        runtime.call("Lookup", {}, PluginContext(data={"run_id": "run-two"}))
    )

    assert calls == 2
    assert first.failure is not None
    assert first.failure.error_code == "backend_unavailable"
    assert blocked.failure is not None
    assert blocked.failure.error_code == "plugin_circuit_open"
    assert blocked.failure.details["cause"]["error_code"] == "backend_unavailable"
    assert next_run.failure is not None
    assert next_run.failure.error_code == "backend_unavailable"


@pytest.mark.parametrize("exposure", ("registry", "session"))
def test_session_persists_failure_and_restores_plugin_circuit(tmp_path, exposure):
    tool_calls = 0
    model_calls = 0

    def web_search(_arguments, _context):
        nonlocal tool_calls
        tool_calls += 1
        raise PluginExecutionError(
            PluginFailure(
                error_code="search_providers_unavailable",
                message="search unavailable",
                retryable=True,
                retry_scope="after_delay",
                retry_after_ms=30_000,
                circuit_scope="run_plugin",
                details={
                    "provider_health": [
                        {
                            "provider": "simplexng",
                            "state": "open",
                            "error_code": "upstream_unreachable",
                        }
                    ]
                },
            )
        )

    async def model(arguments, _context):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            assert any(
                item["function"]["name"] == "WebSearch"
                for item in arguments["tools"]
            )
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "search-one",
                        "name": "WebSearch",
                        "arguments": {"query": "weather"},
                    }
                ],
            }
        assert all(
            item["function"]["name"] != "WebSearch"
            for item in arguments["tools"]
        )
        projected = json.loads(arguments["messages"][-1]["content"])
        assert projected["failure"]["error_code"] == "search_providers_unavailable"
        return {"content": "fallback used", "tool_calls": []}

    def registry() -> PluginRegistry:
        value = PluginRegistry()
        value.register_pack(
            PluginPack(
                "test-runtime",
                "test runtime",
                (
                    Plugin(
                        "MiniMax",
                        "model",
                        {"type": "object"},
                        model,
                        kind="model",
                    ),
                    Plugin(
                        "WebSearch",
                        "search",
                        {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                        web_search,
                        metadata=(
                            {"agent_exposure": "direct"}
                            if exposure == "registry"
                            else {}
                        ),
                    ),
                ),
            ),
            source="test",
        )
        return value

    plugin_directory = tmp_path / "plugins"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry(),
        extra_direct_tool_names=(
            ("WebSearch",) if exposure == "session" else ()
        ),
    )
    session.submit("weather", run_id="run-weather")
    run(session.drain())

    tool_node = next(
        node
        for node in session.snapshot()["nodes"]
        if node["value"].get("role") == "tool_results"
    )
    failure = tool_node["value"]["results"][0]["failure"]
    assert failure["error_code"] == "search_providers_unavailable"
    assert tool_calls == 1
    assert session.snapshot()["nodes"][-1]["value"]["content"] == "fallback used"
    session.close()

    reopened = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry(),
        extra_direct_tool_names=(
            ("WebSearch",) if exposure == "session" else ()
        ),
    )
    restored = reopened.runtime.circuit_failure("WebSearch", "run-weather")
    assert restored is not None
    assert restored.error_code == "search_providers_unavailable"
    reopened.close()


def test_web_search_translates_provider_health_into_core_failure():
    from cyrene.plugins.builtin.cyrene_content import plugin_pack
    from cyrene.plugins.builtin.cyrene_content import search_backend as search

    calls = 0

    class FailedSearchService:
        async def search(self, _topic: str, **_options: object) -> str:
            nonlocal calls
            calls += 1
            raise search.SearchBackendUnavailable(
                "all providers failed",
                error_code="search_providers_unavailable",
                retryable=True,
                retry_scope="after_delay",
                retry_after_ms=30_000,
                affects_health=False,
                circuit_scope="run_plugin",
                provider_health=(
                    {
                        "provider": "simplexng",
                        "state": "open",
                        "error_code": "upstream_unreachable",
                    },
                ),
            )

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test-content")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        data={"run_id": "run-search", "language": "en"},
        services={"web_search": FailedSearchService()},
    )

    first = run(runtime.call("WebSearch", {"query": "weather"}, context))
    blocked = run(runtime.call("WebSearch", {"query": "weather"}, context))

    assert calls == 1
    assert first.failure is not None
    assert first.failure.error_code == "search_providers_unavailable"
    assert first.failure.details["provider_health"][0]["state"] == "open"
    assert blocked.failure is not None
    assert blocked.failure.error_code == "plugin_circuit_open"


async def test_search_provider_health_skips_open_providers(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import (
        SearchRuntimeSettings,
    )

    calls: list[str] = []

    async def failed(provider, _topic, **_kwargs):
        calls.append(provider)
        if provider == "deepseek":
            raise search.SearchBackendUnavailable(
                "credentials missing",
                provider=provider,
                error_code="credentials_missing",
                retryable=False,
                retry_scope="after_config_change",
                retry_after_ms=None,
            )
        raise search.SearchBackendUnavailable(
            "upstream unreachable",
            provider=provider,
            error_code="upstream_unreachable",
            retryable=True,
            retry_scope="after_delay",
            retry_after_ms=30_000,
        )

    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("simplexng", "deepseek")),
    )
    monkeypatch.setattr(search, "_run_search_provider", failed)
    health = search.ProviderHealthRegistry()

    with pytest.raises(search.SearchBackendUnavailable) as first:
        await search.deep_search("query", provider_health=health)
    with pytest.raises(search.SearchBackendUnavailable) as second:
        await search.deep_search("query", provider_health=health)

    assert calls == ["simplexng", "deepseek"]
    assert first.value.error_code == "search_providers_unavailable"
    assert first.value.circuit_scope == "run_plugin"
    assert {item["state"] for item in first.value.provider_health} == {"open"}
    assert second.value.error_code == "search_providers_unavailable"


async def test_search_no_results_does_not_open_provider_health(monkeypatch):
    from cyrene.plugins.builtin.cyrene_content import search_backend as search
    from cyrene.plugins.builtin.cyrene_content.search_settings import (
        SearchRuntimeSettings,
    )

    calls = 0

    async def no_results(provider, _topic, **_kwargs):
        nonlocal calls
        calls += 1
        raise search.SearchBackendUnavailable(
            "no results",
            provider=provider,
            error_code="no_results",
            retryable=True,
            retry_scope="different_arguments",
            retry_after_ms=None,
            affects_health=False,
            circuit_scope="none",
        )

    monkeypatch.setattr(
        search,
        "runtime_settings",
        lambda: SearchRuntimeSettings(True, ("simplexng",)),
    )
    monkeypatch.setattr(search, "_run_search_provider", no_results)
    health = search.ProviderHealthRegistry()

    for query in ("first", "second"):
        with pytest.raises(search.SearchBackendUnavailable) as failure:
            await search.deep_search(query, provider_health=health)
        assert failure.value.error_code == "search_no_results"
        assert failure.value.circuit_scope == "none"

    assert calls == 2
    assert health.snapshots(("simplexng",))[0]["state"] == "closed"
