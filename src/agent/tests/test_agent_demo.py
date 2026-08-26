from __future__ import annotations

import asyncio

from agent.demo import AgentTreeSession
from agent.hook import CONTEXT_CHANGE
from agent.plugin import Plugin, PluginPack, PluginRegistry


def run(coroutine):
    return asyncio.run(coroutine)


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
    session = AgentTreeSession(
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
    assert assistant_change_seen is True
    assert agent_tool_sets == [
        {"Bash", "Read", "Write", "toolbox"},
        {"Bash", "Read", "Write", "toolbox"},
    ]
    assert snapshot["status"] == "idle"
    assert snapshot["leaf_id"] == snapshot["nodes"][-1]["id"]
    session.close()


def test_demo_restores_tree_and_does_not_repeat_completed_transition(tmp_path):
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
    first = AgentTreeSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="persistent-demo",
        registry=registry,
    )
    first.submit("hello")
    run(first.drain())
    first_snapshot = first.snapshot()
    first.close()

    second = AgentTreeSession(
        tmp_path / "data",
        workspace,
        plugin_directory,
        tree_id="persistent-demo",
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
