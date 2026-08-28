from __future__ import annotations

import asyncio
import threading

from agent.session import AgentSession
from agent.hook import CONTEXT_CHANGE, SESSION_START
from agent.plugin import Plugin, PluginContext, PluginPack, PluginRegistry


def run(coroutine):
    return asyncio.run(coroutine)


def test_context_change_hook_mounts_turn_context_before_model(tmp_path):
    captured_messages = []
    memory_context = ["Project memory:\nKeep the verified decision."]

    async def fake_model(arguments, _context):
        captured_messages.append(arguments["messages"])
        return {
            "content": "done",
            "tool_calls": [],
            "model": "fake",
        }

    registry = PluginRegistry()

    async def session_memory(_event):
        return {"context": memory_context[0]}

    def setup_memory(context):
        context.hooks.register(
            SESSION_START,
            session_memory,
            plugin_id="test.memory.session_start",
            hook_id="test-memory-session-start",
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
        "assistant",
    ]
    mounted = snapshot["nodes"][2]["value"]
    assert mounted["context_kind"] == "plugin_session"
    assert mounted["context_source"] == "SessionStart"
    assert mounted["metadata"] == {"source": "SessionStart"}
    assert captured_messages[0][0]["role"] == "system"
    assert "Project memory:\nKeep the verified decision." in captured_messages[0][0]["content"]
    assert [message["role"] for message in captured_messages[0]] == ["system", "user"]

    memory_context[0] = "Project memory:\nUse the revised decision."
    session.submit("second turn", run_id="run_2")
    run(session.drain())

    second_system = captured_messages[1][0]["content"]
    assert "Keep the verified decision." not in second_system
    assert second_system.count("Project memory:\nUse the revised decision.") == 1
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
                        "arguments": {"path": "answer.txt"},
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
    (workspace / "answer.txt").write_text("forty-two", encoding="utf-8")
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
    assert snapshot["nodes"][-1]["value"]["usage"] == {"prompt_tokens": 16}
    assert assistant_change_seen is True
    assert agent_tool_sets == [
        {"Bash", "Read", "Write", "toolbox"},
        {"Bash", "Read", "Write", "toolbox"},
    ]
    assert snapshot["status"] == "idle"
    assert snapshot["leaf_id"] == snapshot["nodes"][-1]["id"]
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
