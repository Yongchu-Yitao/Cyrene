"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime
from cyrene.model.error_details import ModelCallError, classify_model_error
from cyrene.plugins import model_router
from cyrene.workbench.core_adapter import chat_runtime


def run(coroutine):
    return asyncio.run(coroutine)


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
                    "arguments": json.dumps({"path": "README.md"}),
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
    registry.register_plugin(model_router.create_model_router_plugin(), source="test")
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
                "tools": [],
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
        }
    ]
    assert result["reasoning"] == "inspect first"
    assert result["model"] == "selected-model"
    assert result["model_identity"]["candidateId"] == "candidate-selected"
    assert result["model_identity"]["provider"] == "test_provider"
    assert result["output_tokens_per_second"] == 2.0
    assert captured["remembered"][0:2] == ("chat-selected", "candidate-selected")


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

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
    monkeypatch.setattr(model_router, "_publish_fallback", capture_fallback)
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
    registry.register_plugin(model_router.create_model_router_plugin(), source="test")

    result = run(
        PluginRuntime(registry).call(
            model_router.MODEL_ROUTER_PLUGIN,
            {"messages": [{"role": "user", "content": "hello"}]},
            PluginContext(data={"session_id": "chat-fallback"}),
        )
    )

    assert result.success is True
    assert result.value["content"] == "done"
    assert result.value["provider_plugin"] == "ProviderTwo"
    assert calls == ["failed", "succeeded"]
    assert fallbacks == [("primary", "fallback")]


def test_model_router_preserves_public_failure_after_all_fallbacks(monkeypatch):
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

    monkeypatch.setattr(model_router, "_publish_llm_event", ignore_event)
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
    operation.chat = {"projectMemorySnapshot": {}, "title": "Route chat"}
    operation.context = SimpleNamespace(bot=object(), db_path=str(tmp_path / "db.sqlite3"))
    operation.routes = SimpleNamespace(chat_id="host-route")
    operation.service = SimpleNamespace(
        chat_soul_active=lambda _chat: True,
        chat_workspace_active=lambda _chat: False,
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
