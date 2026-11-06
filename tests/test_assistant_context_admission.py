"""References survive input rebuilding; background answers acknowledge persistence."""
import asyncio
from types import SimpleNamespace

import pytest

from cyrene.plugins.builtin.session_management.context import setup_import_context
from test_task_contexts import make_session


@pytest.mark.asyncio
async def test_reference_survives_compaction_rebuild_and_replacement(tmp_path):
    seen = []
    async def model(args, context):
        seen.append(args["messages"])
        return {"content": "done", "tool_calls": []}
    s = make_session(tmp_path, model)
    try:
        setup_import_context(SimpleNamespace(services={"task_contexts": s.task_contexts},
            tree=s.store, tree_id=s.tree.id, hooks=s.hooks, data={"personal_assistant": True}))
        state = s.task_contexts.read()
        state["session_management_import"] = {"content": "REFERENCE_A"}
        s.task_contexts.write(state)
        s.submit("do the task", run_id="r1")
        await s.drain()
        assert str(seen[-1]).count("REFERENCE_A") == 1
        active = s.task_contexts.read()["active"]
        await s.task_contexts.execute("append_context", {"context_id": active, "content": "evidence " * 5000}, "notes")
        class Gateway:
            async def complete(self, messages, **kwargs):
                assert "REFERENCE_A" not in str(messages)
                return {"content": "summary of task"}
        s._plugin_service_values["model"] = Gateway()
        leaf = s.store.get_node(s.tree.id, s._leaf_id)
        node, outcome = await s._compact_at_node(leaf, context_limit=0, force=True, reason="test", resume_model=False)
        assert outcome["compacted"]
        rebuilt = s._prepare_model_input(node).messages
        assert str(rebuilt).count("REFERENCE_A") == 1
        assert not any("REFERENCE_A" in str(m) for m in rebuilt if m["role"] == "system")
        state = s.task_contexts.read()
        state["session_management_import"] = {"content": "REFERENCE_B"}
        s.task_contexts.write(state)
        s.submit("continue", run_id="r2")
        await s.drain()
        assert "REFERENCE_A" not in str(seen[-1])
        assert str(seen[-1]).count("REFERENCE_B") == 1
        # Rebuild without invoking any Hook, as automatic compaction does.
        assert str(s._prepare_model_input(s.store.get_node(s.tree.id, s._leaf_id)).messages).count("REFERENCE_B") == 1
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_background_answer_waits_for_persistence(write_fails):
    from cyrene.workbench.http.workbench.chat_routes.run_answer_routes import _AnswerOperation
    entered, release = asyncio.Event(), asyncio.Event()
    run = SimpleNamespace(done=asyncio.Event())
    jobs = []
    operation = _AnswerOperation(SimpleNamespace(service=None), "child", {
        "question_id": "q", "answer": "known answer", "agentOriginated": True, "background": True})
    async def prepare(_modes):
        return None
    async def execute_run(_run):
        operation.answer_persisted = False
        entered.set()
        await release.wait()
        if not write_fails:
            operation.answer_persisted = True
        run.done.set()
    def start(_id, _ack, runner, **kwargs):
        jobs.append(asyncio.create_task(runner(run)))
        return run, True
    operation.service = SimpleNamespace(run_manager=SimpleNamespace(start_or_get=start))
    operation._prepare = prepare
    operation._run = execute_run
    operation._response = lambda _run: {"error": "persistence failed"}
    task = asyncio.create_task(operation.execute())
    try:
        await entered.wait()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        result = await task
        if write_fails:
            assert result == {"error": "persistence failed"}
        else:
            assert result["status"] == "answer_submitted"
    finally:
        release.set()
        await asyncio.gather(*jobs)
