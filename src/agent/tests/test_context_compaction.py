from __future__ import annotations

import asyncio

from agent import AgentSession
from agent.context.compaction import (
    COMPACT_BLOCK_PREFIX,
    compact_messages,
    messages_token_estimate,
)
from agent.plugin import Plugin, PluginPack, PluginRegistry
from agent.workbench import WorkbenchSessionBridge


def run(coroutine):
    return asyncio.run(coroutine)


def _model_registry(handler) -> PluginRegistry:
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


class _DistillingGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {
            "content": (
                "The user requested the retained task; earlier tool output was "
                "processed successfully."
            )
        }


def test_force_compaction_without_limit_folds_everything_before_exact_episode():
    latest_episode = [
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "latest-call",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": '{"path":"latest.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "latest-call",
            "name": "Read",
            "content": "latest result",
        },
        {"role": "user", "content": "continue from that result"},
    ]
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "tool", "content": "x" * 20_000},
        *latest_episode,
    ]

    result = compact_messages(messages, context_limit=0, force=True)

    assert result.compacted is True
    assert result.context_limit == 0
    assert list(result.messages[-len(latest_episode) :]) == latest_episode
    compacted_block = next(
        item for item in result.messages if item.get("compacted_block") is True
    )
    assert compacted_block["content"].startswith(COMPACT_BLOCK_PREFIX)
    assert "x" * 1_000 not in compacted_block["content"]
    assert result.after_tokens < result.before_tokens


def test_automatic_compaction_keeps_recent_thirty_percent_and_latest_episode():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old " * 2_000},
        {"role": "assistant", "content": "old response"},
        {"role": "tool", "content": "bulky " * 4_000},
        {
            "role": "assistant",
            "content": "latest tool call",
            "tool_calls": [
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-2",
            "name": "Read",
            "content": "exact latest result",
        },
        {"role": "user", "content": "use the exact latest result"},
    ]
    before = messages_token_estimate(messages)
    context_limit = max(1, int(before / 0.7))

    result = compact_messages(
        messages,
        context_limit=context_limit,
        force=False,
    )

    assert result.compacted is True
    assert list(result.messages[-3:]) == messages[-3:]
    assert "bulky " * 100 not in str(result.messages)


def test_agent_auto_compacts_distills_and_manual_node_restores_idle(tmp_path):
    observed_messages: list[list[dict]] = []
    call_count = 0

    async def model(arguments, _context):
        nonlocal call_count
        call_count += 1
        observed_messages.append(arguments["messages"])
        return {
            "content": ("old evidence " * 3_000) if call_count == 1 else "done",
            "tool_calls": [],
        }

    gateway = _DistillingGateway()
    registry = _model_registry(model)
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="compact-chat",
        registry=registry,
        plugin_services={"model": gateway},
    )
    bridge = WorkbenchSessionBridge(session)
    session._configured_compaction_limit = lambda: 0
    run(bridge.submit("first request", run_id="run-1"))

    current_messages = session._messages(session.snapshot()["leaf_id"])
    before = messages_token_estimate(current_messages) + session._model_tool_tokens()
    automatic_limit = max(1, int(before / 0.7))
    session._configured_compaction_limit = lambda: automatic_limit
    run(bridge.submit("second request", run_id="run-2"))

    automatic_nodes = [
        node
        for node in session.store.get_subtree(session.tree.id, session.tree.root_id)
        if isinstance(node.value, dict)
        and node.value.get("role") == "context_compaction"
        and node.value.get("resume_model") is True
    ]
    assert len(automatic_nodes) == 1
    assert automatic_nodes[0].value["run_id"] == "run-2"
    assert gateway.calls[-1]["route"] == "secondary"
    assert any(
        message.get("compacted_block") is True
        for message in observed_messages[-1]
    )

    parent_id = session.snapshot()["leaf_id"]
    old_call = session.store.mount(
        session.tree.id,
        parent_id,
        {
            "role": "assistant",
            "content": "",
            "run_id": "run-manual",
            "tool_calls": [
                {"id": "bulky-call", "name": "Read", "arguments": {}}
            ],
        },
        node_id="manual-old-call",
    )
    old_result = session.store.mount(
        session.tree.id,
        old_call.id,
        {
            "role": "tool_results",
            "run_id": "run-manual",
            "results": [
                {
                    "call_id": "bulky-call",
                    "name": "Read",
                    "success": True,
                    "value": "bulky result " * 5_000,
                    "error": "",
                }
            ],
        },
        node_id="manual-old-result",
    )
    old_done = session.store.mount(
        session.tree.id,
        old_result.id,
        {
            "role": "assistant",
            "content": "old tool processed",
            "run_id": "run-manual",
            "session_end_complete": True,
        },
        node_id="manual-old-done",
    )
    latest_call = session.store.mount(
        session.tree.id,
        old_done.id,
        {
            "role": "assistant",
            "content": "",
            "run_id": "run-manual",
            "tool_calls": [
                {"id": "latest-call", "name": "Read", "arguments": {}}
            ],
        },
        node_id="manual-latest-call",
    )
    latest_result = session.store.mount(
        session.tree.id,
        latest_call.id,
        {
            "role": "tool_results",
            "run_id": "run-manual",
            "results": [
                {
                    "call_id": "latest-call",
                    "name": "Read",
                    "success": True,
                    "value": "small exact result",
                    "error": "",
                }
            ],
        },
        node_id="manual-latest-result",
    )
    latest_done = session.store.mount(
        session.tree.id,
        latest_result.id,
        {
            "role": "assistant",
            "content": "latest tool processed",
            "run_id": "run-manual",
            "session_end_complete": True,
        },
        node_id="manual-latest-done",
    )
    with session._state_lock:
        session._leaf_id = latest_done.id
        session._current_run_id = "run-manual"
        session._status = "idle"

    session._configured_compaction_limit = lambda: 0
    manual = run(bridge.compact(context_limit=0))
    assert manual.keys() >= {"before", "after", "limit", "reason"}
    assert manual["limit"] == 0
    assert manual["reason"] == "manual"
    assert manual["compacted"] is True
    manual_node_id = manual["node_id"]
    stored_messages = session.store.get_node(
        session.tree.id,
        manual_node_id,
    ).value["messages"]
    bridge.close()

    reopened = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="compact-chat",
        registry=registry,
        plugin_services={"model": gateway},
    )
    assert reopened.snapshot()["status"] == "idle"
    assert reopened.snapshot()["leaf_id"] == manual_node_id
    assert reopened._messages(manual_node_id) == stored_messages
    reopened.close()
