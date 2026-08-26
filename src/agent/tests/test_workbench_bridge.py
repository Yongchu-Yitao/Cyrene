from __future__ import annotations

import asyncio
import threading

import pytest

from agent import AgentSession
from agent.plugin import Plugin, PluginPack, PluginRegistry
from agent.workbench import (
    WorkbenchSessionBridge,
)


def run(coroutine):
    return asyncio.run(coroutine)


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
        plugin_context_data={"db_path": "/tmp/workbench.sqlite3"},
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
    assert {item["model_call_kind"] for item in model_contexts} == {
        "agent",
        "permission",
    }
    assert all(item["chat_id"] == "chat-1" for item in model_contexts)
    assert all(item["run_id"] == "run-chat-1" for item in model_contexts)
    assert tool_contexts == [
        {
            "bot": session.plugin_context_data["bot"],
            "chat_id": "chat-1",
            "db_path": "/tmp/workbench.sqlite3",
            "run_id": "run-chat-1",
            "model_call_kind": "tool",
            "user_request": "use the probe",
        }
    ]
    snapshot = bridge.snapshot()
    assert snapshot["status"] == "idle"
    assert snapshot["run_id"] == "run-chat-1"
    assert session.final_output("run-chat-1")["content"] == "echo=hello"
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


def test_cancelling_workbench_task_cancels_and_persists_agent_run(tmp_path):
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
        task = asyncio.create_task(
            bridge.submit("wait forever", run_id="cancel-run")
        )
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

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
