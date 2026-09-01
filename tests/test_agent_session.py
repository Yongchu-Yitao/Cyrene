"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import threading

from cyrene.core.session import AgentSession
from cyrene.core.hook import (
    CONTEXT_CHANGE,
    SESSION_START,
    TURN_START,
    with_session_start_cache_fingerprint,
)
from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.model.error_details import ModelCallError, classify_model_error


def run(coroutine):
    return asyncio.run(coroutine)


def test_session_extra_direct_tool_exposes_hidden_tool_only_when_selected(tmp_path):
    captured_tools = []

    async def fake_model(arguments, _context):
        captured_tools.append(
            {
                item["function"]["name"]
                for item in arguments.get("tools") or []
            }
        )
        return {"content": "done", "tool_calls": []}

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (Plugin("MiniMax", "fake", {"type": "object"}, fake_model, kind="model"),),
        ),
        source="test",
    )
    registry.register_pack(
        PluginPack(
            "hidden-control",
            "hidden control",
            (
                Plugin(
                    "finish_hidden_workflow",
                    "hidden",
                    {"type": "object", "additionalProperties": False},
                    lambda _arguments, _context: {"ok": True},
                    metadata={"agent_exposure": "hidden", "read_only": True},
                ),
            ),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()

    ordinary = AgentSession(
        tmp_path / "ordinary-data",
        tmp_path / "ordinary-workspace",
        plugin_directory,
        registry=registry,
    )
    ordinary.submit("ordinary", run_id="ordinary-run")
    run(ordinary.drain())
    ordinary.close()

    selected = AgentSession(
        tmp_path / "selected-data",
        tmp_path / "selected-workspace",
        plugin_directory,
        registry=registry,
        extra_direct_tool_names=("finish_hidden_workflow",),
    )
    selected.submit("selected", run_id="selected-run")
    run(selected.drain())
    selected.close()

    assert "finish_hidden_workflow" not in captured_tools[0]
    assert "finish_hidden_workflow" in captured_tools[1]


def test_model_failure_mounts_structured_public_error_metadata(tmp_path):
    async def failing_model(_arguments, _context):
        raise ModelCallError(classify_model_error("HTTP 401 Unauthorized: Invalid API Key"))

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (Plugin("MiniMax", "fake", {"type": "object"}, failing_model, kind="model"),),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )

    session.submit("hello", run_id="failed-auth")
    run(session.drain())

    output = session.final_output("failed-auth")
    assert output is not None
    assert output["error"] is True
    assert output["failure_kind"] == "model_authentication_failed"
    assert output["detail_key"] == "workbenchChat.error.modelAuthenticationFailed"
    assert output["retryable"] is False
    assert output["status_code"] == 401
    assert "API Key" not in output["content"]
    session.close()


def test_workspace_file_boundary_matches_v0713_review_scope(tmp_path):
    reviewed_requests = []

    async def fake_model(arguments, _context):
        reviewed_requests.append(json.loads(arguments["messages"][-1]["content"]))
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "decision",
                    "name": "decide",
                    "arguments": {"approve": True, "rationale": "approved"},
                }
            ],
        }

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (Plugin("MiniMax", "fake", {"type": "object"}, fake_model, kind="model"),),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    workspace = tmp_path / "workspace"
    session = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        registry=registry,
        permission_user_request="Create the requested report",
        plugin_context_data={
            "run_context": {"agent_id": "main", "permission_mode": "auto"}
        },
    )
    context = PluginContext(
        workspace=workspace,
        tree=session.store,
        tree_id=session.tree.id,
        node_id=session.tree.root_id,
        hooks=session.hooks,
    )

    inside = run(session.runtime.call(
        "Write", {"path": "draft/report.txt", "content": "inside"}, context
    ))
    assert inside.success is True
    assert reviewed_requests == []

    outside_path = tmp_path / "outside.txt"
    outside = run(session.runtime.call(
        "Write", {"path": str(outside_path), "content": "outside"}, context
    ))
    assert outside.success is True
    assert outside_path.read_text(encoding="utf-8") == "outside"
    assert reviewed_requests[0]["user_request"] == "Create the requested report"
    assert reviewed_requests[0]["tools"][0]["arguments"]["path"] == str(outside_path)
    session.close()


def test_default_permission_confirmation_grants_exact_retry(tmp_path):
    model_calls = 0
    outside_path = tmp_path / "outside.txt"

    async def fake_model(arguments, _context):
        nonlocal model_calls
        model_calls += 1
        if model_calls <= 2:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"write-{model_calls}",
                        "name": "Write",
                        "arguments": {
                            "path": str(outside_path),
                            "content": "approved",
                        },
                    }
                ],
            }
        return {"content": "done", "tool_calls": []}

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (Plugin("MiniMax", "fake", {"type": "object"}, fake_model, kind="model"),),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
        plugin_context_data={
            "run_context": {"agent_id": "main", "permission_mode": "default"}
        },
    )

    session.submit("Write the requested file", run_id="confirm-run")
    run(session.drain())
    pending = session.pending_output()
    assert pending is not None
    assert pending["kind"] == "write_permission_request"
    assert not outside_path.exists()

    session.answer(str(pending["id"]), "同意一次")
    run(session.drain())

    assert session.final_output("confirm-run")["content"] == "done"
    assert outside_path.read_text(encoding="utf-8") == "approved"
    session.close()


def test_context_change_hook_mounts_turn_context_before_model(tmp_path):
    captured_messages = []
    memory_context = ["Project memory:\nKeep the verified decision."]
    stable_dependency = ["memory-snapshot-v1"]
    session_start_calls = 0
    turn_start_calls = 0

    async def fake_model(arguments, _context):
        captured_messages.append(arguments["messages"])
        return {
            "content": "done",
            "tool_calls": [],
            "model": "fake",
        }

    registry = PluginRegistry()

    async def session_memory(_event):
        nonlocal session_start_calls
        session_start_calls += 1
        return {"context": memory_context[0]}

    with_session_start_cache_fingerprint(
        session_memory,
        lambda _event: stable_dependency[0],
    )

    async def turn_context(event):
        nonlocal turn_start_calls
        turn_start_calls += 1
        return {"context": f"Turn runtime: {event.payload['run_id']}"}

    def setup_memory(context):
        existing = {hook.id for hook in context.hooks.list()}
        bindings = (
            (
                SESSION_START,
                session_memory,
                "test.memory.session_start",
                "test-memory-session-start",
            ),
            (
                TURN_START,
                turn_context,
                "test.runtime.turn_start",
                "test-runtime-turn-start",
            ),
        )
        for event, handler, plugin_id, hook_id in bindings:
            if hook_id in existing:
                context.hooks.bind_plugin(plugin_id, handler, replace=True)
            else:
                context.hooks.register(
                    event,
                    handler,
                    plugin_id=plugin_id,
                    hook_id=hook_id,
                    root_only=True,
                )

    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
            setup=setup_memory,
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )

    session.submit("hello", run_id="run_1")
    run(session.drain())

    snapshot = session.snapshot()
    assert [node["value"].get("role") for node in snapshot["nodes"]] == [
        "system",
        "user",
        "context",
        "context",
        "assistant",
    ]
    mounted = snapshot["nodes"][2]["value"]
    assert mounted["context_kind"] == "plugin_session"
    assert mounted["context_source"] == "SessionStart"
    assert mounted["metadata"] == {"source": "SessionStart"}
    turn_mount = snapshot["nodes"][3]["value"]
    assert turn_mount["context_kind"] == "turn_context"
    assert turn_mount["context_source"] == "TurnStart"
    assert captured_messages[0][0]["role"] == "system"
    assert "Project memory:\nKeep the verified decision." in captured_messages[0][0]["content"]
    assert [message["role"] for message in captured_messages[0]] == ["system", "user"]

    memory_context[0] = "Project memory:\nUse the revised decision."
    session.submit("second turn", run_id="run_2")
    run(session.drain())

    second_system = captured_messages[1][0]["content"]
    assert "Keep the verified decision." in second_system
    assert "Project memory:\nUse the revised decision." not in second_system
    assert "Turn runtime: run_1" in captured_messages[0][-1]["content"]
    assert "Turn runtime: run_1" not in second_system
    assert "Turn runtime: run_2" in captured_messages[1][-1]["content"]
    stable_prefix_1 = captured_messages[0][0]["content"]
    assert second_system == stable_prefix_1
    assert captured_messages[1][:-1] == [
        *captured_messages[0],
        {"role": "assistant", "content": "done"},
    ]
    assert session_start_calls == 1
    assert turn_start_calls == 2
    session.close()

    reopened = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )
    reopened.submit("after reopen", run_id="run_3")
    run(reopened.drain())
    third_system = captured_messages[2][0]["content"]
    assert third_system == stable_prefix_1
    assert "Turn runtime: run_3" in captured_messages[2][-1]["content"]
    assert session_start_calls == 1
    assert turn_start_calls == 3
    reopened.close()

    stable_dependency[0] = "memory-snapshot-v2"
    invalidated = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )
    invalidated.submit("stable dependency changed", run_id="run_4")
    run(invalidated.drain())
    rebuilt_system = captured_messages[3][0]["content"]
    assert "Project memory:\nUse the revised decision." in rebuilt_system
    assert session_start_calls == 2
    assert turn_start_calls == 4

    invalidated.submit("stable again", run_id="run_5")
    run(invalidated.drain())
    stable_again = captured_messages[4][0]["content"]
    assert stable_again == rebuilt_system
    assert session_start_calls == 2
    assert turn_start_calls == 5
    invalidated.close()


def test_model_transition_builds_messages_once_when_compaction_is_not_needed(tmp_path):
    async def fake_model(_arguments, _context):
        return {"content": "done", "tool_calls": []}

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (Plugin("MiniMax", "fake", {"type": "object"}, fake_model, kind="model"),),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )
    session._configured_compaction_limit = lambda: 100_000
    original_messages = session._messages
    calls = 0

    def counted_messages(node_id):
        nonlocal calls
        calls += 1
        return original_messages(node_id)

    session._messages = counted_messages
    session.submit("hello", run_id="run-once")
    run(session.drain())

    assert calls == 1
    session.close()


def test_context_updates_drive_model_tool_model_without_agent_loop(tmp_path):
    assistant_change_seen = False
    agent_tool_sets = []

    async def fake_model(arguments, _context):
        tools = arguments.get("tools") or []
        names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, dict)
        }
        if names == {"decide"}:
            assert arguments["tool_choice"] == {
                "type": "function",
                "function": {"name": "decide"},
            }
            assert assistant_change_seen is True
            return {
                "content": "",
                "reasoning": "",
                "tool_calls": [
                    {
                        "id": "permission",
                        "name": "decide",
                        "arguments": {"approve": True, "rationale": "allowed"},
                    }
                ],
                "usage": {"prompt_tokens": 4},
                "model": "fake",
                "model_identity": {"provider": "test-permission"},
                "response_id": "permission-response",
            }
        agent_tool_sets.append(names)
        messages = arguments["messages"]
        if messages[-1]["role"] == "user":
            return {
                "content": "",
                "reasoning": "read first",
                "tool_calls": [
                    {
                        "id": "read-file",
                        "name": "Read",
                        "arguments": {"path": str(answer_path)},
                    }
                ],
                "usage": {"prompt_tokens": 10},
                "model": "fake",
            }
        assert messages[-1]["role"] == "tool"
        return {
            "content": "The file says forty-two.",
            "reasoning": "",
            "tool_calls": [],
            "usage": {"prompt_tokens": 16},
            "model": "fake",
        }

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    answer_path = tmp_path / "answer.txt"
    answer_path.write_text("forty-two", encoding="utf-8")
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            id="model",
            description="test model",
            plugins=(
                Plugin(
                    name="MiniMax",
                    description="fake model",
                    input_schema={"type": "object"},
                    handler=fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    session = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        registry=registry,
        plugin_context_data={
            "run_context": {"agent_id": "main", "permission_mode": "auto"}
        },
    )
    registry.register_pack(
        PluginPack(
            "deferred",
            "deferred",
            (
                Plugin(
                    "Deferred",
                    "deferred",
                    {"type": "object"},
                    lambda _arguments, _context: "deferred",
                ),
            ),
        ),
        source="test",
    )

    def observe_assistant_change(event):
        nonlocal assistant_change_seen
        node = session.store.get_node(session.tree.id, event.payload.node_id)
        value = node.value if isinstance(node.value, dict) else {}
        if value.get("role") == "assistant" and value.get("tool_calls"):
            assistant_change_seen = True

    session.hooks.register(
        CONTEXT_CHANGE,
        observe_assistant_change,
        hook_id="test-observe-assistant",
        plugin_id="test.observe-assistant",
    )
    session.submit("What is in answer.txt?")
    run(session.drain())

    snapshot = session.snapshot()
    roles = [node["value"].get("role") for node in snapshot["nodes"]]
    assert roles == ["system", "user", "assistant", "tool_results", "assistant"]
    assert snapshot["nodes"][-1]["value"]["content"] == "The file says forty-two."
    assert snapshot["nodes"][2]["value"]["usage"] == {"prompt_tokens": 10}
    assert snapshot["nodes"][2]["value"]["auxiliary_usage"] == [
        {
            "kind": "permission",
            "usage": {"prompt_tokens": 4},
            "model": "fake",
            "model_identity": {"provider": "test-permission"},
            "response_id": "permission-response",
            "model_observation_id": "",
            "model_latency_ms": 0.0,
        }
    ]
    permission_reviews = snapshot["nodes"][2]["value"]["permission_reviews"]
    assert len(permission_reviews) == 1
    assert permission_reviews[0]["approved"] is True
    assert permission_reviews[0]["approved_count"] == 1
    assert permission_reviews[0]["denied_count"] == 0
    assert permission_reviews[0]["decisions"] == [{
        "index": 0,
        "tool": "Read",
        "tool_call_id": "read-file",
        "approved": True,
        "rationale": "allowed",
    }]
    durable_event_types = [event.type for event in session.events()]
    tool_calls_index = durable_event_types.index("assistant.tool_calls")
    permission_index = durable_event_types.index("permission.reviewed")
    tools_completed_index = durable_event_types.index("tools.completed")
    assert tool_calls_index < permission_index < tools_completed_index
    assert snapshot["nodes"][-1]["value"]["usage"] == {"prompt_tokens": 16}
    assert assistant_change_seen is True
    assert agent_tool_sets == [
        {"Bash", "Read", "Write", "toolbox"},
        {"Bash", "Read", "Write", "toolbox"},
    ]
    assert snapshot["status"] == "idle"
    assert snapshot["leaf_id"] == snapshot["nodes"][-1]["id"]
    session.close()


def test_default_session_allows_more_than_twelve_model_calls(tmp_path):
    model_calls = 0

    async def looping_model(_arguments, _context):
        nonlocal model_calls
        model_calls += 1
        if model_calls <= 12:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"step-{model_calls}",
                        "name": "step",
                        "arguments": {"number": model_calls},
                    }
                ],
            }
        return {"content": "finished after thirteen calls", "tool_calls": []}

    async def step(arguments, _context):
        return arguments["number"]

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model-and-step",
            "model and loop tool",
            (
                Plugin(
                    "MiniMax",
                    "fake model",
                    {"type": "object"},
                    looping_model,
                    kind="model",
                ),
                Plugin(
                    "step",
                    "advance the test loop",
                    {
                        "type": "object",
                        "properties": {"number": {"type": "integer"}},
                        "required": ["number"],
                    },
                    step,
                    metadata={"permission_review": False},
                ),
            ),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )

    session.submit("keep going", run_id="unlimited-model-calls")
    run(session.drain())

    assert model_calls == 13
    final = session.final_output("unlimited-model-calls")
    assert final is not None
    assert final["content"] == "finished after thirteen calls"
    assert session.snapshot()["status"] == "idle"
    session.close()


def test_cancel_between_assistant_commit_and_success_finish_is_terminal(tmp_path):
    async def fake_model(_arguments, _context):
        return {
            "content": "completed at the cancellation boundary",
            "tool_calls": [],
            "model": "fake",
        }

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )

    assistant_committed = threading.Event()
    cancel_completed = threading.Event()
    cancel_results = []
    observed_events = []
    publish_context_output = session._publish_context_output

    def wait_for_cancel_after_assistant_commit(change):
        node = session.store.get_node(change.tree_id, change.node_id)
        value = node.value if isinstance(node.value, dict) else {}
        if (
            value.get("role") == "assistant"
            and value.get("cancelled") is not True
            and value.get("run_id") == "cancel-finish-race"
        ):
            assistant_committed.set()
            assert cancel_completed.wait(2)
        publish_context_output(change)

    session._publish_context_output = wait_for_cancel_after_assistant_commit
    session.subscribe(lambda event: observed_events.append(event.type))

    def cancel_after_commit():
        if not assistant_committed.wait(2):
            cancel_results.append(False)
            cancel_completed.set()
            return
        cancel_results.append(session.request_cancel("commit_boundary_cancel"))
        cancel_completed.set()

    cancel_thread = threading.Thread(target=cancel_after_commit)
    cancel_thread.start()
    session.submit("finish and cancel", run_id="cancel-finish-race")
    run(session.drain())
    cancel_thread.join(2)

    snapshot = session.snapshot()
    run_nodes = [
        node["value"]
        for node in snapshot["nodes"]
        if node["value"].get("run_id") == "cancel-finish-race"
    ]
    completed = [
        value
        for value in run_nodes
        if value.get("role") == "assistant" and not value.get("cancelled")
    ]
    cancelled = [value for value in run_nodes if value.get("cancelled") is True]

    assert cancel_thread.is_alive() is False
    assert cancel_results == [True]
    assert len(completed) == 1
    assert "session_end_complete" not in completed[0]
    assert len(cancelled) == 1
    assert cancelled[0]["cancel_reason"] == "commit_boundary_cancel"
    assert session.final_output("cancel-finish-race")["cancelled"] is True
    assert "run.cancelled" in observed_events
    assert "assistant.completed" not in observed_events
    assert snapshot["status"] == "idle"
    assert snapshot["leaf_id"] == session.final_output("cancel-finish-race")["node_id"]
    session.close()


def test_session_keeps_working_when_an_optional_plugin_pack_is_broken(tmp_path):
    async def fake_model(_arguments, _context):
        return {
            "content": "done",
            "reasoning": "",
            "tool_calls": [],
            "model": "fake",
        }

    plugin_directory = tmp_path / "plugin_impl"
    broken = plugin_directory / "broken"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("plugin_pack = None\n", encoding="utf-8")
    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )

    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
    )
    listing = run(
        session.runtime.call(
            "toolbox",
            {"operation": "list"},
            PluginContext(workspace=tmp_path / "workspace"),
        )
    )

    assert listing.success is True
    assert listing.value["refresh_errors"] == [
        {
            "path": str(broken),
            "error": "Plugin pack __init__.py must export PluginPack as plugin_pack",
        }
    ]
    session.close()


def test_session_restores_tree_and_does_not_repeat_completed_transition(tmp_path):
    model_calls = 0

    async def fake_model(_arguments, _context):
        nonlocal model_calls
        model_calls += 1
        return {
            "content": "done",
            "reasoning": "",
            "tool_calls": [],
            "usage": {"prompt_tokens": 3},
            "model": "fake",
        }

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    workspace = tmp_path / "workspace"
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    first = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="persistent-session",
        registry=registry,
    )
    first.submit("hello")
    run(first.drain())
    first_snapshot = first.snapshot()
    first.close()

    second = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="persistent-session",
        registry=registry,
    )
    run(second.drain())
    second_snapshot = second.snapshot()

    assert model_calls == 1
    assert second_snapshot["tree_id"] == first_snapshot["tree_id"]
    assert second_snapshot["leaf_id"] == first_snapshot["leaf_id"]
    assert len(second_snapshot["nodes"]) == len(first_snapshot["nodes"])
    assert second_snapshot["status"] == "idle"
    second.close()


def test_failed_retry_restores_previous_committed_branch(tmp_path):
    model_calls = 0

    async def fake_model(_arguments, _context):
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            raise RuntimeError("retry failed")
        return {"content": "first answer", "tool_calls": [], "model": "fake"}

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    workspace = tmp_path / "workspace"
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="retry-session",
        registry=registry,
    )
    session.submit("question", run_id="run-1", metadata={"turn_id": "msg-1"})
    run(session.drain())
    committed_leaf = session.snapshot()["leaf_id"]
    session.commit_result(committed_leaf, "run-1")

    origin = session.prepare_retry()
    assert origin["previous_run_id"] == "run-1"
    session.submit(
        "question",
        run_id="run-2",
        metadata={
            "retry": True,
            "turn_id": "msg-1",
            "retry_of_run_id": origin["previous_run_id"],
        },
    )
    run(session.drain())
    failed_leaf = session.snapshot()["leaf_id"]
    assert failed_leaf != committed_leaf
    session.close()

    reopened = AgentSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="retry-session",
        registry=registry,
    )
    assert reopened.snapshot()["leaf_id"] == committed_leaf
    assert reopened.snapshot()["run_id"] == "run-1"
    reopened.close()


def test_normal_failure_after_committed_retry_does_not_restore_older_branch(tmp_path):
    model_calls = 0

    async def fake_model(_arguments, _context):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 3:
            raise RuntimeError("ordinary follow-up failed")
        return {
            "content": f"answer {model_calls}",
            "tool_calls": [],
            "model": "fake",
        }

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "model",
            (
                Plugin(
                    "MiniMax",
                    "fake",
                    {"type": "object"},
                    fake_model,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        data,
        workspace,
        plugin_directory,
        tree_id="retry-then-follow-up",
        registry=registry,
    )
    session.submit("question", run_id="run-1", metadata={"turn_id": "msg-1"})
    run(session.drain())
    session.commit_result(session.snapshot()["leaf_id"], "run-1")

    session.prepare_retry()
    session.submit(
        "question",
        run_id="run-2",
        metadata={"retry": True, "turn_id": "msg-1"},
    )
    run(session.drain())
    session.commit_result(session.snapshot()["leaf_id"], "run-2")

    session.submit("follow up", run_id="run-3", metadata={"turn_id": "msg-2"})
    run(session.drain())
    failed_leaf = session.snapshot()["leaf_id"]
    assert session.snapshot()["run_id"] == "run-3"
    session.close()

    reopened = AgentSession(
        data,
        workspace,
        plugin_directory,
        tree_id="retry-then-follow-up",
        registry=registry,
    )
    assert reopened.snapshot()["leaf_id"] == failed_leaf
    assert reopened.snapshot()["run_id"] == "run-3"
    reopened.close()
