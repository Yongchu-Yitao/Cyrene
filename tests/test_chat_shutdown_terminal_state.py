"""Chat termination must be durable before a new user turn is admitted."""
import asyncio
from dataclasses import replace

import pytest

from cyrene.core.context import ContextStoreRouter
from cyrene.workbench.chat.chat_runs import ChatRunManager
from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge
from cyrene.workbench.core_adapter.conversation_runtime import (
    ConversationConfig, ConversationRuntime, context_checkpoint_from_nodes,
)
from test_task_contexts import make_session


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("failure", ["shutdown_timeout", "model_error"])
async def test_terminal_chat_allows_next_turn_without_checkpoint_replacement(tmp_path, monkeypatch, retry, failure):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    calls = []
    seed = make_session(tmp_path)
    seed.submit("earlier conversation", run_id="bootstrap")
    await seed.drain()
    tree_id, root_id = seed.tree.id, seed.tree.root_id
    seed.close()

    async def model(arguments, context):
        calls.append(context.data["run_id"])
        if len(calls) == 1:
            loop.call_soon_threadsafe(entered.set)
            if failure == "model_error":
                raise RuntimeError("model transport failed")
            await asyncio.Event().wait()
        return {"content": "new answer", "tool_calls": []}

    def checkpoint(*args):
        store = ContextStoreRouter(tmp_path / "data" / "context")
        try:
            return context_checkpoint_from_nodes(store.get_subtree(tree_id, root_id))
        finally:
            store.close()

    def configure(runtime):
        def open_bridge(config, **kwargs):
            return WorkbenchSessionBridge(make_session(tmp_path, model, tree_id=tree_id))
        monkeypatch.setattr(runtime, "_open_bridge", open_bridge)
        monkeypatch.setattr(runtime, "context_checkpoint", checkpoint)
        monkeypatch.setattr(runtime, "kick_commit_outbox", lambda _: None)

    manager = ChatRunManager(shutdown_grace_seconds=0)
    configure(manager.conversation_runtime)
    config = ConversationConfig(session_id=tree_id, workspace_dir=str(tmp_path), db_path="")

    async def runner(run):
        await manager.conversation_runtime.send(config, "first", run_id=run.run_id, publish=lambda _: None)

    run, _ = manager.start_or_get(tree_id, {"type": "ack"}, runner, stream=False)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if failure == "model_error":
            await asyncio.wait_for(run.done.wait(), 3)
        await asyncio.wait_for(manager.shutdown(), 5)
        assert run.done.is_set()
        assert checkpoint()["status"] == ("cancelled" if failure == "shutdown_timeout" else "failed")
        assert checkpoint()["run_id"] == run.run_id

        # Simulate the next process. No send-time repair, cancellation, or
        # paused restore is allowed: the previous run must already be terminal.
        runtime = ConversationRuntime()
        configure(runtime)
        config = replace(config, retry=retry)
        try:
            result = await asyncio.wait_for(runtime.send(config, "next", run_id="next", publish=lambda _: None), 5)
            assert result.text == "new answer"
            assert calls == [run.run_id, "next"]
            assert checkpoint()["status"] == "completed"
        finally:
            await runtime.shutdown(grace_seconds=0)
    finally:
        if not manager.conversation_runtime._stopping:
            await manager.shutdown()
