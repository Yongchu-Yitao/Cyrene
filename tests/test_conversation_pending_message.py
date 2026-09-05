"""A new user message denies pending work without resuming the old model run."""

import json

import pytest

from cyrene.core.session import AgentSession
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime


@pytest.mark.asyncio
async def test_new_message_denies_pending_write_and_reaches_model(tmp_path, monkeypatch):
    calls = []
    outside = tmp_path / "outside.txt"

    async def model(arguments, _context):
        calls.append(arguments["messages"])
        if len(calls) == 1:
            return {"content": "", "tool_calls": [{
                "id": "write-1", "name": "Write",
                "arguments": {"path": str(outside), "content": "must not be written"},
            }]}
        return {"content": "Handling the new request", "tool_calls": []}

    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, model, kind="model"),
    )), source="test")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    session = AgentSession(
        tmp_path / "data", tmp_path / "workspace", plugins, registry=registry,
        plugin_context_data={"run_context": {"agent_id": "main", "permission_mode": "default"}},
    )
    runtime = ConversationRuntime()
    bridge = WorkbenchSessionBridge(session)

    async def use_bridge(_config, operation, *, publish):
        return await operation(bridge)

    async def publish(_event):
        pass

    monkeypatch.setattr(runtime, "_with_bridge", use_bridge)
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda _chat_id: None)
    config = ConversationConfig(
        session_id="pending-message", workspace_dir=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "workbench.sqlite3"),
    )
    try:
        session.submit("Write the requested file", run_id="old-run")
        await session.drain()
        question = session.pending_output()
        assert question is not None
        assert not outside.exists()

        # A reconnect/replay of the same run must not count as a new message.
        replay = await runtime.send(config, "Write the requested file", run_id="old-run", publish=publish)
        assert replay.status == "awaiting_user"
        assert session.pending_output()["id"] == question["id"]
        assert len(calls) == 1

        with pytest.raises(ValueError, match="Message cannot be empty"):
            await runtime.send(config, "  ", run_id="empty-run", publish=publish)
        assert session.pending_output()["id"] == question["id"]

        result = await runtime.send(config, "Instead, explain the design", run_id="new-run", publish=publish)
        assert result.text == "Handling the new request"
        assert result.run_id == "new-run"
        assert result.status == "completed"
        assert session.pending_output() is None
        assert len(calls) == 2  # No model call between denial and the new request.
        assert not outside.exists()
        messages = json.dumps(calls[1], ensure_ascii=False)
        assert "Instead, explain the design" in messages
        assert "deny" in messages
        nodes = session.snapshot()["nodes"]
        answered = [node["value"]["pending_question"] for node in nodes
                    if isinstance(node.get("value"), dict) and node["value"].get("pending_question")]
        assert any(p["id"] == question["id"] and p["answer"] == "deny" for p in answered)
        with pytest.raises((RuntimeError, ValueError)):
            session.answer(question["id"], "同意一次")
        assert not outside.exists()
    finally:
        session.close()
