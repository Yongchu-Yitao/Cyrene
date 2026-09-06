"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime
from cyrene.model.error_details import ModelCallError, classify_model_error
from cyrene.plugins import ensure_model_router, model_router
from cyrene.workbench.core_adapter import chat_runtime
from cyrene.workbench.core_adapter import conversation_runtime


def run(coroutine):
    return asyncio.run(coroutine)


def test_model_router_preserves_provider_stream_diagnostics(monkeypatch):
    diagnostics = {
        "adapter": "anthropic",
        "http_status": 200,
        "termination_reason": "invalid_tool_arguments",
        "tool_calls": [{
            "index": "1",
            "name": "Write",
            "arguments_length": 10_049,
            "arguments_validation": "invalid_json",
        }],
    }

    async def provider(_arguments, _context):
        raise ModelCallError(
            classify_model_error("invalid Provider Plugin result"),
            diagnostics=diagnostics,
        )

    candidate = {
        "id": "candidate-invalid",
        "profile_id": "candidate-invalid",
        "provider": "test_provider",
        "adapter": "anthropic",
        "model": "test-model",
    }
    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda *_args, **_kwargs: [candidate],
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="TestProvider",
            description="test provider",
            input_schema={"type": "object"},
            handler=provider,
            kind="model",
            metadata={"provider": {"id": "test_provider", "name": "Test"}},
        ),
        source="test",
    )
    ensure_model_router(registry)

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {"messages": [{"role": "user", "content": "write"}]},
            PluginContext(data={"session_id": "chat-invalid"}),
        )
    )

    assert result.success is False
    assert result.error_details["retry_scope"] == "different_arguments"
    assert result.error_details["stream_diagnostics"] == diagnostics


def test_model_router_forwards_session_messages_and_normalizes_tools(monkeypatch):
    captured = {}

    async def provider(arguments, context):
        captured["arguments"] = arguments
        captured["candidate_context"] = dict(context.data["model_candidate"])
        return {
            "content": "",
            "reasoning": "inspect first",
            "reasoning_details": [{"type": "reasoning.text", "text": "inspect first"}],
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {
                    "name": "Read",
                    "arguments": '```json\n{"path":"README.md",}\n```',
                },
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            "model": "selected-model",
            "response_id": "response-selected",
            "latency_ms": 2000,
            "endpoint": "https://provider.example/v1/chat/completions",
        }

    candidate = {
        "id": "candidate-selected",
        "profile_id": "candidate-selected",
        "connection_id": "connection-selected",
        "provider": "openai",
        "adapter": "openai",
        "model": "selected-model",
        "base_url": "https://provider.example/v1",
        "api_key": "must-not-enter-runtime-context",
        "options": {"provider_preset": "test_provider"},
        "context_limit": 64_000,
    }
    def configured(session_id, *, route="primary"):
        captured["session_id"] = session_id
        captured["route"] = route
        return [candidate]

    monkeypatch.setattr(model_router, "configured_model_candidates", configured)

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(
        model_router,
        "remember_model_success",
        lambda session_id, used, endpoint, **_kwargs: captured.update({
            "remembered": (session_id, used["id"], endpoint),
        }),
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="TestProvider",
            description="test provider",
            input_schema={"type": "object"},
            handler=provider,
            kind="model",
            metadata={"provider": {"id": "test_provider", "name": "Test"}},
        ),
        source="test",
    )
    ensure_model_router(registry)
    runtime = PluginRuntime(registry)
    stored_messages = [
        {"role": "system", "content": "base system"},
        {"role": "user", "content": "inspect"},
    ]
    call_result = run(
        runtime.call(
            model_router.MODEL_ROUTER_PLUGIN,
            {
                "messages": stored_messages,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            },
            PluginContext(
                tree_id="chat-selected",
                node_id="node-selected",
                data={
                    "session_id": "chat-selected",
                    "run_id": "run-selected",
                    "model_call_kind": "agent",
                    "system_extra": "turn-only context",
                },
            ),
        )
    )
    assert call_result.success is True
    result = call_result.value
    assert captured["session_id"] == "chat-selected"
    assert captured["arguments"]["messages"] == stored_messages
    assert stored_messages[0]["content"] == "base system"
    assert "api_key" not in captured["candidate_context"]
    assert result["tool_calls"] == [
        {
            "id": "call-read",
            "name": "Read",
            "arguments": {"path": "README.md"},
            "arguments_normalized": True,
        }
    ]
    assert result["reasoning"] == "inspect first"
    assert result["model"] == "selected-model"
    assert result["model_identity"]["candidateId"] == "candidate-selected"
    assert result["model_identity"]["provider"] == "test_provider"
    assert result["output_tokens_per_second"] == 2.0
    assert captured["remembered"][0:2] == ("chat-selected", "candidate-selected")


def test_model_router_selects_codex_oauth_parser_plugin(monkeypatch):
    from cyrene.plugins.tool_call_parsers import CODEX_OAUTH_TOOL_CALL_PARSER

    async def provider(_arguments, _context):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-codex",
                    "name": "toolbox",
                    "arguments": {
                        "operation": "browser_snapshot",
                        "name": "cyrene_browser",
                    },
                }
            ],
            "model": "codex-test",
            "usage": {},
        }

    candidate = {
        "id": "candidate-codex",
        "provider": "openai",
        "adapter": "codex_oauth",
        "model": "codex-test",
        "options": {"provider_preset": "codex_provider"},
    }
    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        model_router,
        "remember_model_success",
        lambda *_args, **_kwargs: None,
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    registry = PluginRegistry()
    registry.register_plugin(
        Plugin(
            name="CodexProvider",
            description="Codex provider",
            input_schema={"type": "object"},
            handler=provider,
            kind="model",
            metadata={
                "provider": {
                    "id": "codex_provider",
                    "tool_call_parser": CODEX_OAUTH_TOOL_CALL_PARSER,
                }
            },
        ),
        source="test",
    )
    ensure_model_router(registry)
    toolbox_definition = registry.resolve("toolbox").tool_definition()

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {
                "messages": [{"role": "user", "content": "inspect"}],
                "tools": [toolbox_definition],
            },
            PluginContext(data={"session_id": "chat-codex-parser"}),
        )
    )

    assert result.success is True
    assert result.value["tool_calls"] == [
        {
            "id": "call-codex",
            "name": "toolbox",
            "arguments": {
                "operation": "invoke",
                "name": "browser_snapshot",
                "arguments": {},
            },
            "arguments_normalized": True,
        }
    ]


def test_model_gateway_routes_one_exact_identity_without_route_fallback(monkeypatch):
    from cyrene.plugins.model_gateway import PluginModelGateway

    captured = {}
    identity = {
        "candidateId": "candidate-exact",
        "provider": "openai",
        "model": "exact-model",
        "baseUrl": "https://provider.example/v1",
    }
    candidate = {
        "id": "candidate-exact",
        "provider": "openai",
        "adapter": "openai",
        "model": "exact-model",
        "base_url": "https://provider.example/v1",
        "api_key": "must-not-enter-runtime-context",
        "options": {"provider_preset": "test_provider"},
    }

    monkeypatch.setattr(
        "cyrene.plugins.model_catalog.resolve_exact_model_candidate",
        lambda requested: candidate if requested == identity else None,
    )
    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exact model calls must not use a configured route")
        ),
    )
    monkeypatch.setattr(model_router, "remember_model_success", lambda *_args, **_kwargs: None)

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)

    async def provider(arguments, context):
        captured["arguments"] = arguments
        captured["candidate_context"] = dict(context.data["model_candidate"])
        return {"content": "learned", "model": "exact-model", "usage": {}}

    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="ExactProvider",
            description="exact provider",
            input_schema={"type": "object"},
            handler=provider,
            kind="model",
            metadata={"provider": {"id": "test_provider", "name": "Test"}},
        ),
        source="test",
    )
    gateway = PluginModelGateway(registry)
    result = run(
        gateway.complete(
            [{"role": "user", "content": "learn this"}],
            model_identity=identity,
            session_id="memory:project-a",
            caller="project_memory_agent",
        )
    )

    assert result["content"] == "learned"
    assert captured["arguments"]["model"] == "exact-model"
    assert captured["candidate_context"]["id"] == "candidate-exact"
    assert "api_key" not in captured["candidate_context"]


def test_model_router_falls_back_through_provider_plugins(monkeypatch):
    calls = []
    fallbacks = []
    fallback_results = []

    async def failed(_arguments, _context):
        calls.append("failed")
        raise RuntimeError("offline")

    async def succeeded(_arguments, _context):
        calls.append("succeeded")
        return {"content": "done", "model": "fallback", "usage": {}}

    candidates = [
        {
            "id": "primary",
            "provider": "openai",
            "adapter": "openai",
            "model": "primary",
            "options": {"provider_preset": "provider_one"},
        },
        {
            "id": "fallback",
            "provider": "openai",
            "adapter": "openai",
            "model": "fallback",
            "options": {"provider_preset": "provider_two"},
        },
    ]
    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda _session, **_kwargs: candidates,
    )
    monkeypatch.setattr(model_router, "remember_model_success", lambda *_args, **_kwargs: None)

    async def ignore_event(*_args, **_kwargs):
        return None

    async def capture_fallback(_context, failed_candidate, fallback_candidate):
        fallbacks.append((failed_candidate["id"], fallback_candidate["id"]))

    async def capture_fallback_result(_context, candidate, *, status):
        fallback_results.append((candidate["id"], status))

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(model_router, "_publish_fallback", capture_fallback)
    monkeypatch.setattr(
        model_router,
        "_persist_fallback_result",
        capture_fallback_result,
    )
    registry = PluginRegistry(include_core=False)
    for name, provider_id, handler in (
        ("ProviderOne", "provider_one", failed),
        ("ProviderTwo", "provider_two", succeeded),
    ):
        registry.register_plugin(
            Plugin(
                name=name,
                description=name,
                input_schema={"type": "object"},
                handler=handler,
                kind="model",
                metadata={"provider": {"id": provider_id, "name": name}},
            ),
            source="test",
        )
    ensure_model_router(registry)

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {"messages": [{"role": "user", "content": "hello"}]},
            PluginContext(data={"session_id": "chat-fallback"}),
        )
    )

    assert result.success is True
    assert result.value["content"] == "done"
    assert calls == ["failed", "succeeded"]
    assert fallbacks == [("primary", "fallback")]
    assert fallback_results == [("fallback", "switched")]
    assert result.value["provider_plugin"] == "ProviderTwo"


def test_model_router_does_not_fallback_on_provider_protocol_error(monkeypatch):
    calls = []
    fallbacks = []
    route_statuses = []

    async def protocol_invalid(_arguments, _context):
        calls.append("primary")
        return {
            "content": "",
            "model": "primary",
            "tool_calls": [{
                "id": "call-bash",
                "name": "Bash",
                "arguments": {"command": "pwd"},
            }],
            "usage": {},
        }

    async def fallback(_arguments, _context):
        calls.append("fallback")
        return {"content": "must not run", "model": "fallback", "usage": {}}

    candidates = [
        {
            "id": "primary",
            "provider": "openai",
            "adapter": "openai",
            "model": "primary",
            "options": {"provider_preset": "provider_one"},
        },
        {
            "id": "fallback",
            "provider": "openai",
            "adapter": "openai",
            "model": "fallback",
            "options": {"provider_preset": "provider_two"},
        },
    ]
    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda _session, **_kwargs: candidates,
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    async def capture_fallback(_context, failed_candidate, fallback_candidate):
        fallbacks.append((failed_candidate["id"], fallback_candidate["id"]))

    async def capture_route_status(_context, candidate, *, status):
        route_statuses.append((candidate["id"], status))

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(model_router, "_publish_fallback", capture_fallback)
    monkeypatch.setattr(
        model_router,
        "_persist_fallback_result",
        capture_route_status,
    )
    registry = PluginRegistry(include_core=False)
    for name, provider_id, handler in (
        ("ProviderOne", "provider_one", protocol_invalid),
        ("ProviderTwo", "provider_two", fallback),
    ):
        registry.register_plugin(
            Plugin(
                name=name,
                description=name,
                input_schema={"type": "object"},
                handler=handler,
                kind="model",
                metadata={"provider": {"id": provider_id, "name": name}},
            ),
            source="test",
        )
    ensure_model_router(registry)

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {
                "messages": [{"role": "user", "content": "continue"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                    },
                }],
            },
            PluginContext(
                data={
                    "session_id": "chat-protocol-error",
                    "run_id": "run-protocol-error",
                }
            ),
        )
    )

    assert result.success is False
    assert result.error_details["code"] == "model_response_invalid"
    assert result.error_details["detail_key"] == "workbenchChat.error.modelResponseInvalid"
    assert calls == ["primary"]
    assert fallbacks == []
    assert route_statuses == [("primary", "failed")]


def test_model_router_preserves_public_failure_after_all_fallbacks(monkeypatch):
    route_statuses = []

    async def rejected(_arguments, _context):
        raise ModelCallError(classify_model_error("HTTP 401 Unauthorized: Invalid API Key"))

    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda _session, **_kwargs: [{
            "id": "primary",
            "provider": "openai",
            "adapter": "openai",
            "model": "primary",
            "options": {"provider_preset": "provider_one"},
        }],
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    async def capture_route_status(_context, candidate, *, status):
        route_statuses.append((candidate["id"], status))

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(
        model_router,
        "_persist_fallback_result",
        capture_route_status,
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="ProviderOne",
            description="provider",
            input_schema={"type": "object"},
            handler=rejected,
            kind="model",
            metadata={"provider": {"id": "provider_one", "name": "Provider"}},
        ),
        source="test",
    )
    registry.register_plugin(model_router.create_model_router_plugin(), source="test")

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {"messages": [{"role": "user", "content": "hello"}]},
            PluginContext(data={"session_id": "chat-auth-failure"}),
        )
    )

    assert result.success is False
    assert result.error_details["code"] == "model_authentication_failed"
    assert result.error_details["detail_key"] == "workbenchChat.error.modelAuthenticationFailed"
    assert result.error_details["retryable"] is False
    assert result.error_details["status_code"] == 401
    assert route_statuses == [("primary", "failed")]


def test_model_router_maps_localized_plugin_timeout_to_model_timeout(monkeypatch):
    async def slow_provider(_arguments, _context):
        await asyncio.sleep(1)

    monkeypatch.setattr(
        model_router,
        "configured_model_candidates",
        lambda _session, **_kwargs: [{
            "id": "slow-primary",
            "provider": "openai",
            "adapter": "openai",
            "model": "slow-model",
            "options": {"provider_preset": "slow_provider"},
        }],
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(
        model_router,
        "_persist_fallback_result",
        ignore_event,
    )
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(
        Plugin(
            name="SlowProvider",
            description="slow provider",
            input_schema={"type": "object"},
            handler=slow_provider,
            kind="model",
            timeout_seconds=0.01,
            metadata={"provider": {"id": "slow_provider", "name": "Slow"}},
        ),
        source="test",
    )
    registry.register_plugin(model_router.create_model_router_plugin(), source="test")

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {"messages": [{"role": "user", "content": "hello"}]},
            PluginContext(
                data={"session_id": "chat-timeout", "language": "zh"},
            ),
        )
    )

    assert result.success is False
    assert result.error_details["code"] == "model_timeout"
    assert result.error_details["detail_key"] == "workbenchChat.error.modelTimeout"
    assert result.error_details["message_zh"] == "模型服务响应超时。"
    assert result.error_details["retryable"] is True


def test_permission_model_usage_does_not_report_agent_context():
    from cyrene.plugins.builtin.cyrene_model._shared import (
        ModelProvider,
        _normalized_result,
    )

    reported = []

    class Tree:
        def mount(self, _tree_id, _node_id, _value, *, node_id):
            return SimpleNamespace(id=node_id)

        def report_context_used(self, *args, **kwargs):
            reported.append((args, kwargs))

    result = _normalized_result(
        {"content": "allowed", "usage": {"prompt_tokens": 42}},
        ModelProvider(
            id="test",
            name="Test",
            plugin_name="Test",
            adapter="openai",
            default_base_url="https://example.test/v1",
        ),
        PluginContext(
            tree=Tree(),
            tree_id="chat-permission",
            node_id="node-permission",
            data={"model_call_kind": "permission"},
        ),
        response_id="permission-response",
        model="permission-model",
        latency_ms=100,
    )

    assert result["content"] == "allowed"
    assert result["model_identity"]["provider"] == "test"
    assert reported == []


def test_production_runtime_seeds_forwards_context_and_leaves_final_reply_to_lifecycle(
    tmp_path,
    monkeypatch,
):
    resolved = []
    opened = {}
    published = []

    def fake_resolve(directory):
        resolved.append(directory)
        return PluginRegistry(), True

    class FakeBridge:
        def snapshot(self):
            return {"status": "idle", "run_id": ""}

        async def submit_result(self, text, *, run_id, metadata, publish):
            opened["text"] = text
            opened["run_id"] = run_id
            opened["metadata"] = metadata
            await publish({"type": "tool.started", "payload": {"name": "Read"}})
            await publish({"type": "reply_start"})
            await publish({"type": "reply_delta", "delta": "done"})
            await publish({"type": "reply_done", "response": "done"})

            writer = opened["plugin_context_data"]["run_context"][
                "runtime_event_writer"
            ]

            def worker_publish():
                asyncio.run(writer({"type": "runtime-event"}))

            await asyncio.to_thread(worker_publish)
            return SimpleNamespace(
                text="done",
                usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                model="test-model",
                model_identity={"provider": "test"},
                generation_duration_ms=200.0,
                output_tokens_per_second=20.0,
                activity_messages=(),
            )

        async def resume(self, *, publish):
            raise AssertionError("a new idle session must submit")

        async def cancel(self, _reason):
            raise AssertionError("an idle session must not cancel")

        def close(self):
            opened["closed"] = True

    def fake_open(*args, **kwargs):
        opened["args"] = args
        opened.update(kwargs)
        return FakeBridge()

    class Run:
        run_id = "run-production"

        async def publish(self, event):
            published.append(event)

    monkeypatch.setattr(
        chat_runtime,
        "resolve_plugin_registry",
        fake_resolve,
    )
    monkeypatch.setattr(chat_runtime.WorkbenchSessionBridge, "open", fake_open)

    result = run(
        chat_runtime.run_workbench_chat(
            run=Run(),
            user_message="inspect it",
            bot=object(),
            host_chat_id="host-chat",
            db_path=str(tmp_path / "workbench.sqlite3"),
            session_id="chat-production",
            workspace_dir=str(tmp_path / "workspace"),
            client_request_id="request-1",
            permission_mode="default",
            command="",
            public_user_message="inspect it",
            attachment_paths={"report.txt": "/tmp/report.txt"},
            system_extra="project context",
            project_id="project-production",
            response_capabilities=("interactive_blocks",),
            plugin_directory=tmp_path / "plugins",
            data_directory=tmp_path / "agent-data",
        )
    )

    assert result.text == "done"
    assert resolved == [(tmp_path / "plugins").resolve()]
    assert opened["model_plugin"] == chat_runtime.MODEL_ROUTER_PLUGIN
    assert opened["chat_id"] == "chat-production"
    assert opened["host_context"]["chat_id"] == "host-chat"
    assert opened["host_context"]["notify_state"] is None
    assert "system_extra" not in opened["plugin_context_data"]
    assert opened["plugin_context_data"]["project_id"] == "project-production"
    assert opened["text"] == "inspect it"
    assert "context_mounts" not in opened["metadata"]
    assert opened["metadata"]["ephemeral_context"] == "project context"
    context = opened["plugin_context_data"]["run_context"]
    assert context["session_id"] == "chat-production"
    assert context["round_id"] == "run-production"
    assert context["client_request_id"] == "request-1"
    assert context["attachment_paths"] == {"report.txt": "/tmp/report.txt"}
    assert context["response_capabilities"] == frozenset({"interactive_blocks"})
    assert "project_id" not in context
    assert [event["type"] for event in published] == [
        "tool.started",
        "runtime-event",
    ]
    assert opened["closed"] is True


def test_conversation_runtime_forwards_exact_model_identity(tmp_path, monkeypatch):
    opened = {}
    identity = {
        "candidateId": "candidate-minimax",
        "provider": "minimax",
        "adapter": "openai",
        "model": "MiniMax-M3",
        "baseUrl": "https://api.minimax.example",
        "reasoningEffort": "",
    }

    monkeypatch.setattr(
        conversation_runtime,
        "resolve_plugin_registry",
        lambda _directory: (PluginRegistry(), True),
    )
    monkeypatch.setattr(
        conversation_runtime,
        "application_plugin_scope",
        lambda: None,
    )

    marker = object()

    def fake_open(*_args, **kwargs):
        opened.update(kwargs)
        return marker

    monkeypatch.setattr(
        conversation_runtime.WorkbenchSessionBridge,
        "open",
        fake_open,
    )
    runtime = conversation_runtime.ConversationRuntime()
    config = conversation_runtime.ConversationConfig(
        session_id="chat-exact-model",
        workspace_dir=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "workbench.sqlite3"),
        model_identity=identity,
        plugin_directory=tmp_path / "plugins",
        data_directory=tmp_path / "agent-data",
    )
    loop = asyncio.new_event_loop()
    try:
        result = runtime._open_bridge(
            config,
            owner_loop=loop,
            raw_publisher=None,
        )
    finally:
        loop.close()

    assert result is marker
    assert opened["plugin_context_data"]["model_identity"] == identity
    assert opened["plugin_context_data"]["model_identity"] is not identity


def test_send_operation_uses_session_route_instead_of_exact_model_identity():
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    operation = object.__new__(_SendOperation)
    operation.chat_id = "chat-preferred-route"
    operation.workspace_dir = "/tmp/workspace"
    operation.context = SimpleNamespace(
        db_path="/tmp/workbench.sqlite3",
        bot=None,
    )
    operation.routes = SimpleNamespace(chat_id="host-chat")
    operation.client_request_id = "request-1"
    operation.mode = "default"
    operation.command = ""
    operation.public_message = "hello"
    operation.normalized = []
    operation.chat = {"title": "Chat", "remoteDeviceIds": []}
    operation.context_activations = {}
    operation.resolved_context_activations = {}
    operation.project_id = "project-1"
    operation.is_side_agent = False
    operation.retry = False
    operation.completed_turn_count_before = 0
    operation.ui_instance_id = "ui-1"
    operation.service = SimpleNamespace(
        chat_soul_active=lambda _chat: True,
        chat_workspace_active=lambda _chat: True,
        chat_short_term_memory_active=lambda _chat: True,
        chat_project_memory_active=lambda _chat: True,
    )

    config = operation._conversation_config(
        SimpleNamespace(guidance_channel=None),
        memory_snapshot={},
        turn_system_extras=[],
        source="desktop_local",
    )

    assert config.model_identity == {}


def test_model_selection_is_validated_and_persisted_canonically(monkeypatch):
    from cyrene.core import plugin as plugin_module
    from cyrene.workbench.http.workbench.chat_routes.detail_routes import (
        _apply_model_selection,
    )

    candidates = [
        {
            "id": "candidate-minimax",
            "name": "MiniMax M3",
            "model": "MiniMax-M3",
        }
    ]
    service = SimpleNamespace(selectable_model_candidates=lambda: candidates)
    monkeypatch.setattr(
        plugin_module,
        "application_plugin_service",
        lambda name: service if name == "model_configuration" else None,
    )
    chat = {
        "modelSelectionId": "candidate-qwen",
        "model": "qwen",
        "lastModel": "qwen",
    }

    error = _apply_model_selection(chat, "MiniMax M3")

    assert error is None
    assert chat["modelSelectionId"] == "candidate-minimax"
    assert chat["model"] == "MiniMax-M3"
    assert "lastModel" not in chat


def test_model_selection_rejects_unknown_candidate_without_mutating_chat(monkeypatch):
    from cyrene.core import plugin as plugin_module
    from cyrene.workbench.http.workbench.chat_routes.detail_routes import (
        _apply_model_selection,
    )

    service = SimpleNamespace(selectable_model_candidates=lambda: [])
    monkeypatch.setattr(
        plugin_module,
        "application_plugin_service",
        lambda name: service if name == "model_configuration" else None,
    )
    chat = {
        "modelSelectionId": "candidate-qwen",
        "model": "qwen",
        "lastModel": "qwen",
    }
    before = dict(chat)

    error = _apply_model_selection(chat, "missing-model")

    assert error is not None
    assert error.status_code == 400
    assert chat == before


def test_builtin_workbench_route_always_uses_new_runtime(
    tmp_path,
    monkeypatch,
):
    from cyrene.platform import host_bridge
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    captured = {}

    async def fake_runtime(config, text, *, run_id, metadata, publish):
        captured.update(
            config=config,
            text=text,
            run_id=run_id,
            metadata=metadata,
            publish=publish,
        )
        return SimpleNamespace(
            text="new-kernel-reply",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            latest_request_usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            model="provider/model",
            model_identity={"provider": "provider"},
            generation_duration_ms=250.0,
            output_tokens_per_second=20.0,
            active_plan=None,
            activity_messages=(
                {
                    "id": "activity-1",
                    "role": "assistant",
                    "activityCard": True,
                    "trace": [{"kind": "tool", "text": "Read"}],
                },
            ),
        )

    async def fake_source(_ui_instance_id):
        return "desktop_local"

    monkeypatch.setattr(host_bridge, "resolve_conversation_source", fake_source)

    operation = object.__new__(_SendOperation)
    operation.chat_id = "chat-route"
    operation.client_request_id = "request-route"
    operation.is_external_agent = False
    operation.is_side_agent = False
    operation.agent_message = "hello"
    operation.public_message = "hello"
    operation.public_attachments = []
    operation.normalized = []
    operation.command = ""
    operation.mode = "default"
    operation.workspace_dir = str(tmp_path / "workspace")
    operation.ui_instance_id = "ui-route"
    operation.conversation_source = ""
    operation.context_activations = {"skills": ["writer"]}
    operation.resolved_context_activations = {"skills": ["writer"]}
    operation.dynamic_command_prompt = ""
    operation.project_id = "project-route"
    operation.retry = False
    operation.fork_replay = False
    operation.completed_turn_count_before = 2
    operation.chat = {
        "projectMemorySnapshot": {},
        "title": "Route chat",
        "shortTermMemoryActive": False,
        "projectMemoryActive": False,
    }
    operation.context = SimpleNamespace(bot=object(), db_path=str(tmp_path / "db.sqlite3"))
    operation.routes = SimpleNamespace(chat_id="host-route")
    operation.service = SimpleNamespace(
        chat_soul_active=lambda _chat: True,
        chat_workspace_active=lambda _chat: False,
        chat_short_term_memory_active=lambda chat: chat.get(
            "shortTermMemoryActive", True
        ),
        chat_project_memory_active=lambda chat: chat.get(
            "projectMemoryActive", True
        ),
        ensure_chat_memory_snapshot=lambda chat: chat.get("projectMemorySnapshot"),
        run_manager=SimpleNamespace(
            conversation_runtime=SimpleNamespace(send=fake_runtime),
        ),
    )
    from cyrene.workbench.chat.chat_external_turn_service import ExternalTurnProjection

    operation.external = ExternalTurnProjection()

    workbench_run = SimpleNamespace(
        run_id="run-route",
        publish=lambda _event: None,
        events=[],
        guidance_channel=None,
    )

    result = run(operation._run_turn(workbench_run))

    assert result.text == "new-kernel-reply"
    assert operation.external.usage["total_tokens"] == 15
    assert operation.external.model == "provider/model"
    assert operation.external.output_tokens_per_second == 20.0
    assert operation.external.activity_messages[0]["id"] == "activity-1"
    assert captured["run_id"] == "run-route"
    assert captured["publish"] is workbench_run.publish
    assert captured["text"] == "hello"
    config = captured["config"]
    assert config.session_id == "chat-route"
    assert config.host_chat_id == "host-route"
    assert config.conversation_source == "desktop_local"
    assert config.system_extra == ""
    assert config.context_activations == {"skills": ["writer"]}
    assert config.resolved_context_activations == {"skills": ["writer"]}
    assert config.project_id == "project-route"
    assert config.project_memory_snapshot == {}
    assert config.session_title == "Route chat"
    assert config.memory_write_enabled is True
    assert config.memory_trigger_enabled is True
    assert config.memory_archive_enabled is True
    assert config.memory_short_term_enabled is False
    assert config.memory_project_enabled is False
    assert config.completed_turn_count == 3


def test_failed_plugin_workflow_atomically_restores_the_user_turn(tmp_path):
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    repository = ChatRepository(str(tmp_path / "workbench.db"))
    base_chat = {
        "id": "chat-workflow-atomic",
        "projectId": "project-1",
        "kind": "chat",
        "title": "New chat",
        "status": "idle",
        "messages": [],
    }
    repository.write({"chats": [base_chat]})
    before = repository.get(base_chat["id"])
    operation = object.__new__(_SendOperation)
    operation.chat_id = base_chat["id"]
    operation.lang = "en"
    operation.base_chat = before
    operation.chat = {
        **dict(before or {}),
        "title": "/goal ship it",
        "status": "running",
        "messages": [{"id": "msg-goal", "role": "user", "content": "/goal ship it"}],
    }
    operation.service = SimpleNamespace(repository=repository)
    operation.retry_state_backup = None
    workflow_error = object()
    finalized: list[bool] = []

    async def ok():
        return None

    async def persist():
        repository.write_one(operation.chat, base_chat=operation.base_chat)
        return None

    async def fail_workflow():
        return workflow_error

    async def finalize():
        finalized.append(True)

    operation._parse_request = ok
    operation._load_chat = lambda _permission_modes: ok()
    operation._load_project_and_model = ok
    operation._prepare_user_turn = ok
    operation._persist_user_turn = persist
    operation._begin_plugin_workflow = fail_workflow
    operation._finalize_persisted_user_turn = finalize

    result = run(operation.execute())

    assert result is workflow_error
    assert repository.get(base_chat["id"]) == before
    assert operation.chat == before
    assert finalized == []


def test_chat_write_and_conversation_commit_outbox_are_atomic(tmp_path):
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.chat.conversation_commit import ConversationTurnCommit

    repository = ChatRepository(str(tmp_path / "workbench.db"))
    repository.write(
        {
            "chats": [
                {
                    "id": "chat-commit",
                    "title": "Commit",
                    "messages": [],
                    "status": "running",
                }
            ]
        }
    )
    chat = repository.get("chat-commit")
    assert chat is not None
    base = dict(chat)
    chat["status"] = "idle"
    chat["messages"] = [
        {"id": "msg-1", "role": "user", "content": "question"},
        {"id": "msg-2", "role": "assistant", "content": "answer"},
    ]
    event = ConversationTurnCommit(
        chat_id="chat-commit",
        turn_id="msg-1",
        run_id="run-1",
        node_id="assistant-1",
        status="completed",
        retry=False,
        user_text="question",
        assistant_text="answer",
        completed_turn_count=1,
    ).as_event()

    repository.write_one(chat, base_chat=base, commit_event=event)
    assert repository.get("chat-commit")["status"] == "idle"
    assert repository.pending_commit_events("chat-commit", limit=1) == [event]
    repository.fail_commit_event(event["event_id"], "try again")
    assert repository.pending_commit_events("chat-commit", limit=1) == [event]
    repository.complete_commit_event(event["event_id"])
    assert repository.pending_commit_events("chat-commit", limit=1) == []


def test_invalid_commit_event_rolls_back_the_public_chat_write(tmp_path):
    from cyrene.workbench.chat.chat_repository import ChatRepository

    repository = ChatRepository(str(tmp_path / "workbench.db"))
    original = {
        "id": "chat-invalid-commit",
        "title": "Commit",
        "messages": [],
        "status": "running",
    }
    repository.write({"chats": [original]})
    chat = repository.get(original["id"])
    assert chat is not None
    base = dict(chat)
    chat["status"] = "idle"

    import pytest

    with pytest.raises(ValueError, match="ConversationTurnCommitted"):
        repository.write_one(
            chat,
            base_chat=base,
            commit_event={
                "event_id": "bad",
                "type": "ConversationTurnCommitted",
                "chat_id": original["id"],
                "turn_id": "",
                "run_id": "run-1",
                "node_id": "assistant-1",
            },
        )
    assert repository.get(original["id"])["status"] == "running"


def test_retry_question_identity_survives_resume_without_incrementing_turn_count(
    tmp_path,
):
    from cyrene.workbench.chat.chat_application import next_completed_turn_count
    from cyrene.workbench.core_adapter.bridge import WorkbenchPendingQuestion
    from cyrene.workbench.http.workbench.chat_routes.run_answer_routes import (
        _AnswerOperation,
    )

    pending = WorkbenchPendingQuestion.from_mapping(
        {
            "id": "question-1",
            "text": "Choose",
            "round_id": "run-retry",
            "retry": True,
            "turn_id": "msg-original",
            "original_user_message": "original question",
        }
    ).as_dict()
    assert pending["retry"] is True
    assert pending["turnId"] == "msg-original"

    captured = {}

    async def answer(config, question_id, answer_text, *, publish):
        captured.update(
            config=config,
            question_id=question_id,
            answer_text=answer_text,
            publish=publish,
        )
        return SimpleNamespace(active_plan=None)

    operation = object.__new__(_AnswerOperation)
    operation.chat_id = "chat-retry-question"
    operation.question_id = "question-1"
    operation.answer_text = "choice"
    operation.original_request = "original question"
    operation.pending = {"clientRequestId": "request-1"}
    operation.mode = "default"
    operation.workspace_dir = str(tmp_path / "workspace")
    operation.routes = SimpleNamespace(chat_id="host-chat")
    operation.context = SimpleNamespace(db_path=str(tmp_path / "workbench.db"), bot=None)
    operation.chat = {
        "completedTurnCount": 7,
        "title": "Retry",
        "remoteDeviceIds": [],
    }
    operation.project_id = "project-1"
    operation.is_side_agent = False
    operation.retry = True
    operation.ui_instance_id = "ui-1"
    operation.conversation_source = "desktop_local"
    operation.service = SimpleNamespace(
        ensure_chat_memory_snapshot=lambda _chat: {},
        chat_short_term_memory_active=lambda _chat: True,
        chat_project_memory_active=lambda _chat: True,
        resolve_composer_input_context=lambda *_args, **_kwargs: {
            "remoteDeviceIds": [],
            "soulActive": True,
            "workspaceActive": True,
            "contextActivations": {},
            "resolvedContextActivations": {},
        },
        next_completed_turn_count=next_completed_turn_count,
        run_manager=SimpleNamespace(
            conversation_runtime=SimpleNamespace(answer=answer),
        ),
    )
    workbench_run = SimpleNamespace(
        publish=lambda _event: None,
        guidance_channel=None,
        events=[],
    )

    run(operation._resume_agent(workbench_run))

    assert captured["config"].retry is True
    assert captured["config"].completed_turn_count == 7


def test_builtin_runtime_message_fields_preserve_latest_request_usage():
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    operation = object.__new__(_SendOperation)
    fields = operation._runtime_message_fields(
        SimpleNamespace(
            usage={
                "prompt_tokens": 98058,
                "completion_tokens": 331,
                "total_tokens": 98389,
                "prompt_cache_hit_tokens": 41300,
                "prompt_cache_miss_tokens": 56758,
            },
            latest_request_usage={
                "prompt_tokens": 56417,
                "completion_tokens": 183,
                "total_tokens": 56600,
                "prompt_cache_hit_tokens": 41044,
                "prompt_cache_miss_tokens": 15373,
            },
            model_identity={},
            generation_duration_ms=None,
            output_tokens_per_second=None,
        )
    )

    assert fields["usage"]["prompt_cache_hit_tokens"] == 41300
    assert fields["latestRequestUsage"]["prompt_cache_hit_tokens"] == 41044
    assert fields["latestRequestUsage"]["prompt_cache_miss_tokens"] == 15373
