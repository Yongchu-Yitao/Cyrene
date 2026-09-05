"""Memory must consume the same effective history as the live Agent."""
from copy import deepcopy
from types import SimpleNamespace
import asyncio

import pytest

from cyrene.core.session import AgentSession
from cyrene.plugins.builtin.cyrene_memory.service import MemoryService
from cyrene.plugins.builtin.cyrene_memory import project_memory
from cyrene.model.error_details import ModelCallError
from cyrene.plugins.model_router import request_token_estimate


@pytest.mark.parametrize("replacement_role", ["context_compaction", "context_reflection"])
@pytest.mark.parametrize("run_id", ["old", "current"])
def test_memory_and_agent_share_effective_history(replacement_role, run_id):
    def node(name, **value):
        return SimpleNamespace(id=name, value=value)

    compacted = [
        {"role": "system", "content": "old mounted context"},
        {"role": "system", "content": "summary", "compacted_block": True},
    ]
    path = [
        node("root", role="system", content="base"),
        node("old-user", role="user", content="old question", run_id="old"),
        node("large", role="tool_results", results=[{"value": "discarded" * 1000}]),
        node("current-user", role="user", content="continue", run_id="current"),
        node("replacement", role=replacement_role, messages=compacted, run_id=run_id),
        node("context", role="context", content="fresh context", run_id="current",
             context_kind="stable", context_lifecycle="session"),
        node("result", role="tool_results", results=[{
            "call_id": "call", "name": "Read", "success": False,
            "value": "recent", "failure": {"code": "missing"},
        }]),
        node("answer", role="assistant", content="done", run_id="current"),
    ]
    before = deepcopy(compacted)
    tree = SimpleNamespace(get_path=lambda *_: path)
    memory = MemoryService(workspace=None, tree=tree, tree_id="tree", data={})
    agent = SimpleNamespace(store=tree, tree=SimpleNamespace(id="tree"),
                            _plugin_services=lambda: {})
    actual = memory.messages("answer")
    assert actual == AgentSession._messages(agent, "answer")
    assert len(actual) == 4
    assert "discarded" not in str(actual)
    assert actual[1]["content"] == "summary\n\nfresh context"
    assert actual[0]["content"] == ("old mounted context" if run_id == "current" else "base")
    assert '"failure": {"code": "missing"}' in actual[-2]["content"]
    actual[0]["content"] = "mutated"
    assert compacted == before
    assert memory.messages("answer", include_anchor=False)[-1]["role"] == "tool"


@pytest.mark.parametrize("limit_kind", ["history_only", "complete_request"])
def test_memory_budget_includes_appended_prompt_and_tools(monkeypatch, limit_kind):
    from cyrene.platform import config_store

    history = [{"role": "user", "content": "remember the decision"}]
    instruction = "memory instruction " * 100
    monkeypatch.setattr(project_memory, "_memory_agent_instruction", lambda *a, **k: instruction)
    full = [*history, {"role": "user", "content": instruction}]
    required = request_token_estimate(full, [project_memory._MEMORY_SUBMIT_TOOL])
    limit = required if limit_kind == "complete_request" else request_token_estimate(history, None)
    monkeypatch.setattr(config_store, "effective_ctx_limit_for_model", lambda _: limit)
    called = []

    async def complete(messages, **kwargs):
        called.append(messages)
        return {}

    monkeypatch.setattr(project_memory, "_parse_memory_agent_response", lambda *a, **k: ("saved", "", {}))
    snapshot = {"messages": history, "model": {"model": "test"}, "language": "en"}
    operation = project_memory._learn_prompt(snapshot, "", model_gateway=SimpleNamespace(complete=complete))
    if limit_kind == "history_only":
        with pytest.raises(ModelCallError):
            asyncio.run(operation)
        assert not called
    else:
        assert asyncio.run(operation)[0] == "saved"
        assert called == [full]
    assert snapshot["messages"] == history
