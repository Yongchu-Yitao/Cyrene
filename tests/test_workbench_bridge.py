"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone

import pytest

from cyrene.core import AgentSession, AgentSessionEvent
from cyrene.core.hook import SESSION_END, SESSION_START
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.workbench.core_adapter import (
    WorkbenchSessionBridge,
)
from cyrene.workbench.core_adapter.bridge import (
    _normalized_usage,
    _turn_metrics,
    project_tool_activity_messages,
    workbench_events,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_usage_normalization_preserves_openai_compatible_cache_details():
    usage = _normalized_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 80},
        }
    )

    assert usage["prompt_cache_hit_tokens"] == 80
    assert usage["prompt_cache_miss_tokens"] == 20


def test_turn_metrics_aggregate_model_usage_and_generation_time():
    snapshot = {
        "nodes": [
            {
                "id": "user",
                "parent_id": "root",
                "value": {"role": "user", "run_id": "run-1"},
            },
            {
                "id": "observation-1",
                "parent_id": "user",
                "value": {
                    "role": "model_observation",
                    "call_kind": "agent",
                    "latency_ms": 500,
                    "usage": {"total_tokens": 999},
                },
            },
            {
                "id": "assistant-1",
                "parent_id": "user",
                "value": {
                    "role": "assistant",
                    "run_id": "run-1",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                    "model_observation_id": "observation-1",
                    "model_latency_ms": 500,
                },
            },
            {
                "id": "observation-2",
                "parent_id": "assistant-1",
                "value": {
                    "role": "model_observation",
                    "call_kind": "permission",
                    "latency_ms": 250,
                },
            },
            {
                "id": "tool-results",
                "parent_id": "assistant-1",
                "value": {"role": "tool_results", "run_id": "run-1"},
            },
            {
                "id": "assistant-final",
                "parent_id": "tool-results",
                "value": {
                    "role": "assistant",
                    "run_id": "run-1",
                    "model": "provider/model",
                    "model_identity": {"provider": "provider"},
                    "usage": {
                        "input_tokens": 40,
                        "output_tokens": 10,
                    },
                    "model_observation_id": "",
                    "model_latency_ms": 250,
                    "auxiliary_usage": [
                        {
                            "usage": {
                                "prompt_tokens": 15,
                                "completion_tokens": 5,
                                "total_tokens": 20,
                            },
                            "model_observation_id": "observation-2",
                            "model_latency_ms": 250,
                        }
                    ],
                },
            },
        ]
    }

    usage, latest_usage, model, identity, duration_ms, rate = _turn_metrics(
        snapshot,
        "run-1",
        "assistant-final",
    )

    assert usage == {
        "prompt_tokens": 155,
        "completion_tokens": 35,
        "total_tokens": 190,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    assert latest_usage == {
        "prompt_tokens": 40,
        "completion_tokens": 10,
        "total_tokens": 50,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    assert model == "provider/model"
    assert identity == {"provider": "provider"}
    assert duration_ms == 1000
    assert rate == 35


def test_context_tree_tool_calls_project_to_durable_activity_cards():
    snapshot = {
        "nodes": [
            {
                "id": "assistant-discover",
                "parent_id": "user",
                "created_at": "2030-01-01T00:00:01+00:00",
                "value": {
                    "role": "assistant",
                    "run_id": "run-1",
                    "model": "provider/model",
                    "tool_calls": [
                        {
                            "id": "call-list",
                            "name": "toolbox",
                            "arguments": {"operation": "list"},
                        },
                        {
                            "id": "call-save",
                            "name": "toolbox",
                            "arguments": {
                                "operation": "invoke",
                                "name": "save_project_memory",
                                "arguments": {"content": "likes hamsters"},
                            },
                        },
                    ],
                    "permission_reviews": [{
                        "id": "permission-review-1",
                        "approved": True,
                        "approved_count": 2,
                        "denied_count": 0,
                        "created_at": "2030-01-01T00:00:01.500000+00:00",
                        "decisions": [
                            {
                                "index": 0,
                                "tool": "toolbox",
                                "tool_call_id": "call-list",
                                "approved": True,
                                "rationale": "Read-only discovery",
                            },
                            {
                                "index": 1,
                                "tool": "toolbox",
                                "tool_call_id": "call-save",
                                "approved": True,
                                "rationale": "Requested memory update",
                            },
                        ],
                    }],
                },
            },
            {
                "id": "tool-results",
                "parent_id": "assistant-discover",
                "created_at": "2030-01-01T00:00:02+00:00",
                "value": {
                    "role": "tool_results",
                    "run_id": "run-1",
                    "results": [
                        {
                            "call_id": "call-list",
                            "name": "toolbox",
                            "success": True,
                            "value": {"packs": ["cyrene_memory"]},
                        },
                        {
                            "call_id": "call-save",
                            "name": "toolbox",
                            "success": True,
                            "value": {
                                "operation": "invoke",
                                "result": "Saved to project memory",
                            },
                        },
                    ],
                },
            },
            {
                "id": "assistant-final",
                "parent_id": "tool-results",
                "created_at": "2030-01-01T00:00:03+00:00",
                "value": {
                    "role": "assistant",
                    "run_id": "run-1",
                    "content": "done",
                    "session_end_complete": True,
                },
            },
        ]
    }

    activities = project_tool_activity_messages(snapshot, "run-1")

    assert len(activities) == 1
    assert activities[0]["id"] == "activity_assistant-discover"
    assert activities[0]["activityCard"] is True
    assert [entry["text"] for entry in activities[0]["trace"]] == [
        "toolbox.list",
        "save_project_memory",
        "Permission review approved",
    ]
    assert activities[0]["trace"][1]["preview"] == "Saved to project memory"
    assert activities[0]["trace"][1]["status"] == "completed"
    assert activities[0]["trace"][2] == {
        "kind": "permission",
        "toolCallId": "permission-review-1",
        "text": "Permission review approved",
        "preview": (
            "toolbox · Read-only discovery; "
            "toolbox · Requested memory update"
        ),
        "status": "completed",
        "failed": False,
    }


def test_permission_review_projects_to_unified_workbench_event():
    event = AgentSessionEvent(
        sequence=3,
        type="permission.reviewed",
        tree_id="tree-1",
        run_id="run-1",
        node_id="assistant-1",
        time=datetime(2030, 1, 1, tzinfo=timezone.utc),
        data={
            "id": "permission-review-1",
            "approved": False,
            "approved_count": 0,
            "denied_count": 1,
            "decisions": [{
                "index": 0,
                "tool": "Bash",
                "tool_call_id": "call-1",
                "approved": False,
                "rationale": "Command is too broad",
            }],
            "created_at": "2030-01-01T00:00:00+00:00",
        },
    )

    projected = workbench_events(event)

    assert len(projected) == 1
    assert projected[0]["type"] == "permission.reviewed"
    assert projected[0]["eventId"] == (
        "agent:tree-1:run-1:permission:permission-review-1"
    )
    assert projected[0]["payload"] == event.data


def test_resource_presentation_projects_through_tool_activity_events():
    presentation = {
        "locations": [{
            "kind": "file",
            "access": "write",
            "phase": "started",
            "projectId": "project-1",
            "path": "src/app.py",
        }],
        "reveal": True,
        "phase": "started",
    }
    started = AgentSessionEvent(
        sequence=4,
        type="assistant.tool_calls",
        tree_id="tree-1",
        run_id="run-1",
        node_id="assistant-1",
        time=datetime(2030, 1, 1, tzinfo=timezone.utc),
        data={"tool_calls": [{
            "id": "call-1",
            "name": "Write",
            "arguments": {"path": "src/app.py", "content": "value = 1"},
            "presentation": presentation,
        }]},
    )
    projected_started = workbench_events(started)
    assert projected_started[0]["payload"]["presentation"] == presentation

    completed_presentation = {
        **presentation,
        "phase": "completed",
        "locations": [{**presentation["locations"][0], "phase": "completed"}],
    }
    completed = AgentSessionEvent(
        sequence=5,
        type="tool.completed",
        tree_id="tree-1",
        run_id="run-1",
        node_id="assistant-1",
        time=datetime(2030, 1, 1, tzinfo=timezone.utc),
        data={
            "call_id": "call-1",
            "name": "Write",
            "success": True,
            "value": "ok",
            "error": "",
            "presentation": completed_presentation,
        },
    )
    projected_completed = workbench_events(completed)
    assert projected_completed[0]["payload"]["presentation"] == completed_presentation


def model_registry(handler) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (
                Plugin(
                    "MiniMax",
                    "fake model",
                    {"type": "object"},
                    handler,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    return registry


def replace_model(registry: PluginRegistry, handler) -> None:
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (
                Plugin(
                    "MiniMax",
                    "fake model",
                    {"type": "object"},
                    handler,
                    kind="model",
                ),
            ),
        ),
        source="test",
        replace=True,
    )


def test_session_prepares_nested_resource_reveal_before_toolbox_execution(tmp_path):
    async def model(_arguments, _context):
        return {"content": "", "tool_calls": []}

    def edit_resource(_arguments, _context):
        return "ok"

    registry = model_registry(model)
    registry.register_plugin(
        Plugin(
            "DeferredEdit",
            "edit a file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            edit_resource,
            metadata={"resource_effects": ({
                "argument_path": ("path",),
                "kind": "file",
                "access": "write",
                "phase": "both",
            },)},
        ),
        source="test-resource",
    )
    plugin_directory = tmp_path / "plugin_impl"
    workspace = tmp_path / "workspace"
    plugin_directory.mkdir()
    workspace.mkdir()
    session = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="resource-chat",
        registry=registry,
        plugin_context_data={"project_id": "project-1"},
    )
    prepared = session._prepare_resource_tool_calls([{
        "id": "call-1",
        "name": "toolbox",
        "arguments": {
            "operation": "invoke",
            "name": "DeferredEdit",
            "arguments": {"path": "src/app.py", "reveal": True},
        },
    }])[0]

    assert prepared["arguments"]["arguments"] == {"path": "src/app.py"}
    assert prepared["resource_plugin_name"] == "DeferredEdit"
    assert prepared["resource_reveal"] is True
    assert prepared["presentation"] == {
        "locations": [{
            "kind": "file",
            "access": "write",
            "phase": "started",
            "projectId": "project-1",
            "path": "src/app.py",
        }],
        "reveal": True,
        "phase": "started",
    }
    session.close()


def test_plain_chat_bridge_submits_publishes_and_forwards_plugin_context(tmp_path):
    model_contexts = []
    tool_contexts = []

    async def model(arguments, context):
        model_contexts.append(dict(context.data))
        tool_names = {
            item.get("function", {}).get("name")
            for item in arguments.get("tools") or []
            if isinstance(item, dict)
        }
        if tool_names == {"decide"}:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "permission",
                        "name": "decide",
                        "arguments": {"approve": True, "rationale": "allowed"},
                    }
                ],
            }
        if arguments["messages"][-1]["role"] == "user":
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "probe-call",
                        "name": "toolbox",
                        "arguments": {
                            "operation": "invoke",
                            "name": "Probe",
                            "arguments": {"value": "hello"},
                        },
                    }
                ],
            }
        return {"content": "echo=hello", "tool_calls": []}

    def probe(arguments, context):
        tool_contexts.append(dict(context.data))
        return {"echo": arguments["value"]}

    registry = model_registry(model)
    registry.register_plugin(
        Plugin(
            "Probe",
            "record PluginContext data",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            probe,
            timeout_seconds=10,
        ),
        source="test-probe",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="chat-1",
        registry=registry,
        host_context={"bot": object(), "chat_id": "chat-1"},
        plugin_context_data={"db_path": "/tmp/workbench.sqlite3", "language": "en"},
    )
    bridge = WorkbenchSessionBridge(session)
    published = []

    async def scenario():
        async def publish(event):
            published.append(event)

        return await bridge.submit(
            "use the probe",
            run_id="run-chat-1",
            publish=publish,
        )

    assert run(scenario()) == "echo=hello"
    types = [event["type"] for event in published]
    assert "run.started" in types
    assert "tool.started" in types
    assert "tool.completed" in types
    assert "reply_start" in types
    assert "reply_delta" in types
    assert "reply_done" in types
    reply_delta = next(event for event in published if event["type"] == "reply_delta")
    assert reply_delta["delta"] == "echo=hello"
    reply_done = next(event for event in published if event["type"] == "reply_done")
    assert reply_done["response"] == "echo=hello"
    assert {item["model_call_kind"] for item in model_contexts} == {"agent"}
    assert all(item["chat_id"] == "chat-1" for item in model_contexts)
    assert all(item["run_id"] == "run-chat-1" for item in model_contexts)
    assert tool_contexts == [
        {
            "bot": session.plugin_context_data["bot"],
            "chat_id": "chat-1",
            "db_path": "/tmp/workbench.sqlite3",
            "language": "en",
            "run_id": "run-chat-1",
            "permission_user_request": "use the probe",
            "model_call_kind": "tool",
            "user_request": "use the probe",
        }
    ]
    snapshot = bridge.snapshot()
    assert snapshot["status"] == "idle"
    assert snapshot["run_id"] == "run-chat-1"
    assert session.final_output("run-chat-1")["content"] == "echo=hello"
    bridge.close()


def test_bridge_projects_provider_stream_without_replaying_full_content_delta(tmp_path):
    async def model(_arguments, context):
        stream = context.services["model_stream"]
        await stream({"type": "reply_start"})
        await stream({"type": "reply_delta", "delta": "Hel"})
        await stream({"type": "reply_delta", "delta": "lo"})
        await stream({"type": "reply_done", "response": "Hello"})
        return {"content": "Hello", "tool_calls": []}

    registry = model_registry(model)
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="stream-chat",
        registry=registry,
    )
    bridge = WorkbenchSessionBridge(session)
    published = []

    async def scenario():
        return await bridge.submit(
            "stream",
            run_id="stream-run",
            publish=lambda event: published.append(event),
        )

    assert run(scenario()) == "Hello"
    assert [
        event["delta"]
        for event in published
        if event["type"] == "reply_delta"
    ] == ["Hel", "lo"]
    assert [
        event["response"]
        for event in published
        if event["type"] == "reply_done"
    ] == ["Hello", "Hello"]
    bridge.close()


def test_bridge_recovers_unfinished_chat_after_process_restart(tmp_path):
    started = threading.Event()

    async def interrupted_model(_arguments, _context):
        started.set()
        await asyncio.Event().wait()

    registry = model_registry(interrupted_model)
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    first = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="recover-chat",
        registry=registry,
    )
    first.submit("finish after restart", run_id="recover-run")
    assert started.wait(timeout=2)
    first.close()

    async def recovered_model(_arguments, _context):
        return {"content": "recovered reply", "tool_calls": []}

    replace_model(registry, recovered_model)
    second = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="recover-chat",
        registry=registry,
    )
    bridge = WorkbenchSessionBridge(second)
    published = []

    async def scenario():
        return await bridge.resume(publish=lambda event: published.append(event))

    assert run(scenario()) == "recovered reply"
    assert second.snapshot()["run_id"] == "recover-run"
    reply_done = next(event for event in published if event["type"] == "reply_done")
    assert reply_done["response"] == "recovered reply"
    bridge.close()


def test_bridge_persists_pending_question_and_answers_same_run_after_restart(tmp_path):
    observed_tool_answers = []

    async def model(arguments, _context):
        tools = {
            item.get("function", {}).get("name")
            for item in arguments.get("tools") or []
            if isinstance(item, dict)
        }
        if tools == {"decide"}:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "permission",
                        "name": "decide",
                        "arguments": {"approve": True, "rationale": "allowed"},
                    }
                ],
            }
        last = arguments["messages"][-1]
        if last["role"] == "user":
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "ask-call",
                        "name": "toolbox",
                        "arguments": {
                            "operation": "invoke",
                            "name": "ask_user",
                            "arguments": {
                                "text": "Choose a format",
                                "options": ["short", "long"],
                            },
                        },
                    }
                ],
            }
        observed_tool_answers.append(
            json.loads(last["content"])["value"]["result"]
        )
        return {"content": "using long", "tool_calls": []}

    async def ask_user(_arguments, _context):
        return json.dumps(
            {
                "status": "awaiting_user",
                "question_id": "question-format",
            }
        )

    registry = model_registry(model)
    registry.register_plugin(
        Plugin(
            "ask_user",
            "pause for a user answer",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "options": {"type": "array"},
                },
                "required": ["text"],
            },
            ask_user,
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    first = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="pending-chat",
        registry=registry,
    )
    first_bridge = WorkbenchSessionBridge(first)
    published = []

    pending = run(
        first_bridge.submit_result(
            "ask before continuing",
            run_id="pending-run",
            publish=lambda event: published.append(event),
        )
    )
    assert pending.status == "awaiting_user"
    assert pending.run_id == "pending-run"
    assert pending.pending_question is not None
    assert pending.pending_question.as_dict() == {
        "id": "question-format",
        "text": "Choose a format",
        "options": ["short", "long"],
        "allowCustom": True,
        "kind": "clarification",
        "roundId": "pending-run",
        "clientRequestId": "",
        "askedAt": pending.pending_question.asked_at,
    }
    assert "awaiting_user" in [event["type"] for event in published]
    first_bridge.close()

    reopened = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="pending-chat",
        registry=registry,
    )
    reopened_bridge = WorkbenchSessionBridge(reopened)
    assert reopened.snapshot()["status"] == "awaiting_user"

    completed = run(
        reopened_bridge.answer_result("question-format", "long")
    )
    assert completed.status == "completed"
    assert completed.run_id == "pending-run"
    assert completed.text == "using long"
    assert observed_tool_answers == [
        {
            "status": "answered",
            "question_id": "question-format",
            "answer": "long",
        }
    ]
    assert reopened._permission_request_for_run("pending-run") == (
        "ask before continuing\n\n用户随后澄清：long"
    )
    reopened_bridge.close()


def test_bridge_projection_failure_does_not_revoke_agent_reply(tmp_path):
    async def model(_arguments, _context):
        return {"content": "durable reply", "tool_calls": []}

    registry = model_registry(model)
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="projection-failure-chat",
        registry=registry,
    )
    bridge = WorkbenchSessionBridge(session)

    async def scenario():
        async def broken_publish(_event):
            raise RuntimeError("projection database is locked")

        return await bridge.submit(
            "complete despite projection failure",
            run_id="projection-failure-run",
            publish=broken_publish,
        )

    assert run(scenario()) == "durable reply"
    assert session.final_output("projection-failure-run")["content"] == "durable reply"
    bridge.close()


def test_plugin_pack_session_context_and_terminal_lifecycle_are_durable(tmp_path):
    model_messages = []
    model_services = []
    lifecycle_events = []

    async def model(arguments, context):
        model_messages.append(arguments["messages"])
        model_services.append(dict(context.services))
        return {"content": "memory-aware reply", "tool_calls": []}

    async def session_start(event):
        lifecycle_events.append(("start", dict(event.payload)))
        return {"context": "Durable memory context"}

    async def session_end(event):
        lifecycle_events.append(("end", dict(event.payload)))

    def setup(context):
        context.provide("test.memory", {"ready": True})
        context.hooks.register(
            SESSION_START,
            session_start,
            plugin_id="test.memory.start",
            hook_id="test-memory-start",
            root_only=True,
        )
        context.hooks.register(
            SESSION_END,
            session_end,
            plugin_id="test.memory.end",
            hook_id="test-memory-end",
            root_only=True,
        )

    registry = model_registry(model)
    registry.register_pack(
        PluginPack(
            "memory-test",
            "session lifecycle fixture",
            (),
            setup=setup,
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="memory-lifecycle-chat",
        registry=registry,
    )
    bridge = WorkbenchSessionBridge(session)

    assert run(
        bridge.submit("remember this", run_id="memory-lifecycle-run")
    ) == "memory-aware reply"

    assert model_services[0]["test.memory"] == {"ready": True}
    assert "subagents" not in model_services[0]
    assert "Durable memory context" in model_messages[0][0]["content"]
    context_nodes = [
        node
        for node in session.store.get_subtree(session.tree.id, session.tree.root_id)
        if isinstance(node.value, dict) and node.value.get("role") == "context"
    ]
    assert len(context_nodes) == 1
    assert context_nodes[0].value["context_kind"] == "plugin_session"
    assert context_nodes[0].value["content"] == "Durable memory context"
    terminal = session.final_output("memory-lifecycle-run")
    assert terminal is not None
    assert terminal["session_end_complete"] is True
    assert [name for name, _payload in lifecycle_events] == ["start", "end"]
    assert lifecycle_events[-1][1]["assistant_text"] == "memory-aware reply"
    bridge.close()


def test_required_session_pack_hot_setup_failure_blocks_model_and_submit(tmp_path):
    model_calls = []

    async def model(_arguments, _context):
        model_calls.append(True)
        return {"content": "must not run", "tool_calls": []}

    def setup(_context):
        return None

    registry = model_registry(model)
    registry.register_pack(
        PluginPack(
            "required-context",
            "required session context fixture",
            (),
            setup=setup,
            metadata={"required": True},
        ),
        source="test:required-context",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="required-context-chat",
        registry=registry,
    )

    def broken_setup(_context):
        raise RuntimeError("context setup broke during reload")

    registry.register_pack(
        PluginPack(
            "required-context",
            "required session context fixture",
            (),
            setup=broken_setup,
            metadata={"required": True},
        ),
        source="test:required-context",
        replace=True,
    )

    with pytest.raises(
        RuntimeError,
        match="Required Plugin session setup unavailable: required-context",
    ):
        session._plugin_services()
    with pytest.raises(
        RuntimeError,
        match="Required Plugin session setup unavailable: required-context",
    ):
        session.submit("do not run unconstrained", run_id="blocked-run")

    assert model_calls == []
    assert all(
        not isinstance(node.value, dict) or node.value.get("role") != "user"
        for node in session.store.get_subtree(session.tree.id, session.tree.root_id)
    )
    session.close()


def test_live_session_reconciles_memory_soul_and_subagent_style_setups(tmp_path):
    async def model(_arguments, _context):
        return {"content": "unused", "tool_calls": []}

    drivers = []

    class Driver:
        def __init__(self):
            self.attached = 0
            self.closed = False
            drivers.append(self)

        def attach(self):
            self.attached += 1

        def request_cancel_all(self, _reason):
            pass

        def close(self):
            self.closed = True

        @property
        def has_pending_work(self):
            return False

    def memory_setup(context):
        context.provide("memory.v1", object())
        context.hooks.register(
            SESSION_START,
            lambda _event: {"context": "memory-context"},
            plugin_id="cyrene_memory.start",
            hook_id="cyrene-memory-start",
        )

    def soul_setup(context):
        context.hooks.register(
            SESSION_START,
            lambda _event: {"context": "soul-context"},
            plugin_id="cyrene_soul.mount",
            hook_id="cyrene-soul-start",
        )

    def subagent_setup(context):
        driver = Driver()
        context.provide("subagents", driver)
        context.provide("session_driver", driver)

    registry = model_registry(model)
    for pack_id, setup in (
        ("cyrene_memory", memory_setup),
        ("cyrene_soul", soul_setup),
        ("cyrene_subagent", subagent_setup),
    ):
        registry.register_pack(
            PluginPack(pack_id, f"{pack_id} fixture", (), setup=setup),
            source=f"test:{pack_id}",
        )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="hot-plugin-session",
        registry=registry,
    )
    root_before = session.store.get_node(session.tree.id, session.tree.root_id).value

    assert set(run(session.hooks.session_start()).split("\n\n")) == {
        "memory-context",
        "soul-context",
    }
    assert "memory.v1" in session.plugin_services
    assert "subagents" in session.plugin_services
    assert drivers[-1].attached == 1

    registry.set_pack_enabled("cyrene_memory", False)
    assert run(session.hooks.session_start()) == "soul-context"
    assert "memory.v1" not in session.plugin_services
    assert "cyrene-memory-start" not in {hook.id for hook in session.hooks.list()}

    registry.set_pack_enabled("cyrene_soul", False)
    assert run(session.hooks.session_start()) == ""
    assert "cyrene-soul-start" not in {hook.id for hook in session.hooks.list()}

    old_driver = drivers[-1]
    registry.set_pack_enabled("cyrene_subagent", False)
    session.reconcile_plugins()
    assert old_driver.closed is True
    assert "subagents" not in session.plugin_services

    registry.set_pack_enabled("cyrene_memory", True)
    registry.set_pack_enabled("cyrene_soul", True)
    registry.set_pack_enabled("cyrene_subagent", True)
    session.reconcile_plugins()
    assert set(run(session.hooks.session_start()).split("\n\n")) == {
        "memory-context",
        "soul-context",
    }
    assert drivers[-1] is not old_driver
    assert drivers[-1].attached == 1
    assert session.store.get_node(session.tree.id, session.tree.root_id).value == root_before
    session.close()


def test_cancelling_workbench_run_cancels_and_persists_agent_state(tmp_path):
    started = threading.Event()

    async def blocking_model(_arguments, _context):
        started.set()
        await asyncio.Event().wait()

    registry = model_registry(blocking_model)
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="cancel-chat",
        registry=registry,
    )
    bridge = WorkbenchSessionBridge(session)

    async def scenario():
        run_task = asyncio.create_task(
            bridge.submit("wait forever", run_id="cancel-run")
        )
        assert await asyncio.to_thread(started.wait, 2)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    run(scenario())
    output = session.final_output("cancel-run")
    assert output is not None
    assert output["cancelled"] is True
    assert output["cancel_reason"] == "workbench_run_cancelled"
    assert session.snapshot()["status"] == "idle"

    async def next_model(_arguments, _context):
        return {"content": "next reply", "tool_calls": []}

    replace_model(registry, next_model)
    assert run(bridge.submit("continue", run_id="next-run")) == "next reply"
    bridge.close()
