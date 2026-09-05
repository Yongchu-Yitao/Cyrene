"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio

from cyrene.core import AgentSession
from cyrene.core.context.compaction import (
    COMPACT_BLOCK_PREFIX,
    compact_messages,
    messages_token_estimate,
)
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry


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


def test_shared_prose_is_not_compacted_as_task_content(tmp_path):
    async def model(arguments, _context):
        return {"content": "shared evidence " * 3000, "tool_calls": []}

    plugin_directory = tmp_path / "plugins"
    plugin_directory.mkdir()
    session = AgentSession(tmp_path / "data", tmp_path, plugin_directory,
                           registry=_model_registry(model))
    session._configured_compaction_limit = lambda: 0
    try:
        session.submit("exact shared request", run_id="r")
        run(session.drain())
        outcome = run(session.compact_context(context_limit=0))
        assert not outcome["compacted"]
        messages = session._messages(session.snapshot()["leaf_id"])
        assert "shared evidence " * 3000 in str(messages)
        assert "exact shared request" in str(messages)
    finally:
        session.close()
