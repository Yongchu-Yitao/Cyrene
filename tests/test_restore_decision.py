"""Recovery precedence and lazy reads are part of crash-resume behavior."""
import pytest

from cyrene.core.restore_decision import select_restore_action


@pytest.mark.parametrize("value,question,assistant,tools,provider,action,reads", [
    ({"cancelled": True, "role": "assistant", "tool_calls": [1]}, {"id": "q"}, False, False, False, "cancelled", []),
    ({"role": "assistant", "tool_calls": [1]}, {"id": "q"}, False, False, False, "awaiting_user", ["pending"]),
    ({"role": "context_compaction", "resume_model": True}, None, False, False, False, "rewrite", ["pending"]),
    ({"role": "context_reflection", "resume_model": True}, None, True, False, False, "rewrite", ["pending"]),
    ({"role": "context_compaction"}, None, False, False, False, "rewrite", ["pending"]),
    ({"role": "assistant", "tool_calls": [1]}, None, False, False, False, "resume_tools", ["pending", "tools"]),
    ({"role": "assistant", "tool_calls": [1], "trigger_model": True}, None, False, True, False, "tools_complete", ["pending", "tools"]),
    ({"role": "context", "trigger_model": False}, None, False, False, False, "resume_context_source", ["pending"]),
    ({"role": "user", "trigger_model": False}, None, False, False, True, "resume_user_context", ["pending", "provider"]),
    ({"role": "user", "trigger_model": False}, None, False, False, False, "idle", ["pending", "provider"]),
    ({"role": "assistant"}, None, False, False, False, "resume_session_end", ["pending"]),
    ({"role": "assistant", "session_end_complete": True}, None, False, False, False, "idle", ["pending"]),
    ({"role": "tool_results", "trigger_model": True}, None, False, True, False, "resume_model", ["pending", "assistant"]),
    ({"role": "tool_results", "trigger_model": True}, None, True, True, False, "idle", ["pending", "assistant"]),
])
def test_restore_action_preserves_precedence_and_required_queries(
    value, question, assistant, tools, provider, action, reads,
):
    observed = []

    def read(name, result):
        def query():
            observed.append(name)
            return result
        return query

    result = select_restore_action(
        value, pending=read("pending", question),
        has_assistant=read("assistant", assistant),
        has_tool_result=read("tools", tools),
        has_context_provider=read("provider", provider),
    )
    assert result.action == action
    assert observed == reads
    if action == "awaiting_user":
        assert result.pending is question
