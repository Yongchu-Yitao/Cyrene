"""History must reconcile the live timeline with ContextTree reconstruction."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.core.context import ContextStoreRouter
from cyrene.core.session import AgentSessionEvent
from cyrene.workbench.chat.chat_application import (
    deduplicate_projected_messages,
    merge_chat_messages_chronologically,
    sanitize_durable_traces,
)
from cyrene.workbench.chat.conversation_context_service import AgentContextRepository
from cyrene.workbench.chat.run_timeline import RunTimeline
from cyrene.workbench.core_adapter.bridge import workbench_events
from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime
from cyrene.workbench.core_adapter.tool_activity_projection import project_tool_activity_messages
from cyrene.workbench.http.workbench.chat_routes import agent_config_routes
from cyrene.workbench.http.workbench.chat_routes.detail_routes import (
    _merge_context_activity_messages,
    _register_get_route,
)


def dual_projection(count=8, *, reviews=True, approved=True):
    timeline = RunTimeline("run-history")
    nodes = []
    for index in range(count):
        at = f"2030-01-01T00:00:{index + 1:02d}+00:00"
        source = f"assistant-{index}"
        call = f"call-{index}"
        review = {"id": f"permission_review_{index}", "approved": approved}
        timeline.apply({"type": "tool.started", "timestamp": at, "sourceId": source,
                        "toolCallId": call, "name": "Read", "input": {"path": "README.md"}})
        if reviews:
            timeline.apply({"type": "permission.reviewed", "timestamp": at,
                            "sourceId": source, **review})
        timeline.apply({"type": "tool.completed", "timestamp": at, "sourceId": source,
                        "toolCallId": call, "name": "Read", "result": {"text": "file contents"},
                        "status": "completed" if approved else "failed"})
        nodes.extend([
            {"id": source, "created_at": at, "value": {
                "role": "assistant", "run_id": "run-history",
                "tool_calls": [{"id": call, "name": "Read", "arguments": {"path": "README.md"}}],
                "permission_reviews": [review] if reviews else [],
            }},
            {"id": f"result-{index}", "created_at": at, "value": {
                "role": "tool_results", "run_id": "run-history",
                "results": [{"call_id": call, "success": approved, "value": "file contents"}],
            }},
        ])
    timeline.apply({"type": "reasoning_done", "sourceId": "final", "response": "Checked results",
                    "timestamp": "2030-01-01T00:00:20+00:00"})
    timeline.apply({"type": "reply_done", "sourceId": "final", "response": "Done",
                    "timestamp": "2030-01-01T00:00:21+00:00"})
    return timeline.messages(), list(project_tool_activity_messages({"nodes": nodes}, "run-history"))


@pytest.mark.parametrize("reviews,approved", [(False, True), (True, True), (True, False)])
def test_history_reload_keeps_eight_calls_and_canonical_details(reviews, approved):
    canonical, reconstructed = dual_projection(reviews=reviews, approved=approved)
    chat = {"id": "chat", "messages": canonical, "messageCount": len(canonical)}
    before = deepcopy((chat, reconstructed))

    result = _merge_context_activity_messages(chat, reconstructed)

    assert result["messages"] == canonical
    assert result["messageCount"] == len(canonical)
    tools = [entry for message in result["messages"] for entry in message.get("trace", [])
             if entry["kind"] == "tool"]
    assert len(tools) == len({entry["toolCallId"] for entry in tools}) == 8
    assert all(entry["output"] == {"text": "file contents"} for entry in tools)
    assert _merge_context_activity_messages(result, reconstructed) == result
    assert (chat, reconstructed) == before


@pytest.mark.parametrize("context_first", [False, True])
def test_previously_saved_duplicates_match_both_permission_id_formats(context_first):
    canonical, reconstructed = dual_projection()
    messages = reconstructed + canonical if context_first else canonical + reconstructed
    assert deduplicate_projected_messages(messages) == canonical


def test_context_history_still_supplies_missing_calls():
    canonical, reconstructed = dual_projection()
    # Only one activity was persisted, as in older history.
    chat = {"messages": canonical[:1], "messageCount": 1}
    result = _merge_context_activity_messages(chat, reconstructed)
    assert result["messages"] == [canonical[0], *reconstructed[1:]]
    assert result["messageCount"] == 8


@pytest.mark.parametrize("same_card_id", [False, True])
def test_history_recovers_full_bash_command_from_context(same_card_id):
    command = "echo '" + "x" * 450 + "'\nprintf '%s\\n' '{literal}'"
    args = {"command": command}
    output = {"exit_code": 1, "stderr": "Traceback"}
    timeline = RunTimeline("run-bash")
    timeline.apply({"type": "tool.started", "toolCallId": "bash", "name": "Bash", "args": args})
    assert timeline.messages()[0]["trace"][0]["input"] == args
    timeline.apply({"type": "tool.completed", "toolCallId": "bash", "name": "Bash", "outputSummary": output, "exitCode": 1})
    saved = timeline.messages()[0]
    assert saved["trace"][0].pop("input") == args
    assert saved["trace"][0].pop("exitCode") == 1

    nodes = [
        {"id": "assistant-bash", "value": {"role": "assistant", "run_id": "run-bash",
         "tool_calls": [{"id": "bash", "name": "Bash", "arguments": args}]}},
        {"id": "result-bash", "value": {"role": "tool_results", "run_id": "run-bash",
         "results": [{"call_id": "bash", "success": True, "value": output}]}},
    ]
    reconstructed = project_tool_activity_messages({"nodes": nodes}, "run-bash")
    assert reconstructed[0]["trace"][0]["command"] == command
    assert "Traceback" in reconstructed[0]["trace"][0]["preview"]
    assert reconstructed[0]["trace"][0]["exitCode"] == 1
    if same_card_id:
        reconstructed[0]["id"] = saved["id"]

    result = _merge_context_activity_messages({"messages": [saved]}, list(reconstructed))

    assert result["messageCount"] == 1
    assert result["messages"][0]["id"] == saved["id"]
    assert result["messages"][0]["trace"][0]["command"] == command
    assert result["messages"][0]["trace"][0]["exitCode"] == 1
    assert result["messages"][0]["trace"][0]["failed"] is False
    assert "input" not in saved["trace"][0]
    assert "exitCode" not in saved["trace"][0]


def test_trace_upload_preserves_server_bash_details(monkeypatch):
    args = {"command": "printf '%s' '" + "x" * 450 + "'\nexit 1"}
    saved_trace = [
        {"kind": "tool", "text": "Bash", "toolCallId": "bash", "input": {**args, "timeout_ms": 30000}, "exitCode": 1},
        {"kind": "tool", "text": "Read", "toolCallId": "read", "input": {"path": "file"}},
    ]
    chat = {"id": "chat", "messages": [{"id": "activity", "activityCard": True, "trace": saved_trace}]}
    repository = SimpleNamespace(get=lambda _: deepcopy(chat), write_one=Mock())
    service = SimpleNamespace(
        repository=repository, sanitize_durable_traces=sanitize_durable_traces, utc_now_iso=lambda: "now",
    )
    monkeypatch.setattr(agent_config_routes, "publish_chat_changed", AsyncMock())
    router = APIRouter()
    agent_config_routes._register_trace_route(router, SimpleNamespace(service=service))
    app = FastAPI()
    app.include_router(router)
    uploaded_trace = [
        {"kind": "tool", "text": "Bash", "toolCallId": "bash", "status": "completed"},
        {"kind": "tool", "text": "Read", "toolCallId": "read", "status": "completed"},
    ]

    with TestClient(app) as client:
        response = client.patch("/api/workbench/chats/chat/trace", json={
            "messageIds": ["activity"], "traces": [uploaded_trace],
        })

    assert response.json() == {"ok": True, "updated": 1}
    written = repository.write_one.call_args.args[0]["messages"][0]["trace"]
    assert written == [
        {**uploaded_trace[0], "command": args["command"], "exitCode": 1},
        uploaded_trace[1],
    ]


def test_checkpoint_save_retains_recovered_bash_details_for_the_same_activity():
    entry = {"kind": "tool", "tool": "Bash", "toolCallId": "bash", "status": "completed"}
    activity = {"id": "saved", "role": "assistant", "activityCard": True}
    chat = {"messages": [{**activity, "trace": [{**entry, "command": "exit 2", "exitCode": 2}]}]}

    merge_chat_messages_chronologically(chat, [{**activity, "trace": [entry]}])

    assert chat["messages"][0]["trace"] == [{**entry, "command": "exit 2", "exitCode": 2}]


def test_same_call_ids_in_separate_user_turns_remain_separate():
    canonical, reconstructed = dual_projection(count=1)
    first = [canonical[0], reconstructed[0]]
    second = deepcopy(first)
    for message in second:
        message["id"] += "-next-turn"
        message["createdAt"] = "2030-01-02T00:00:01+00:00"
    user = {"id": "user-next", "role": "user", "content": "Read again",
            "createdAt": "2030-01-02T00:00:00+00:00"}
    assert deduplicate_projected_messages([*first, user, *second]) == [first[0], user, second[0]]


def test_history_bash_exit_status_matches_its_own_run_when_call_ids_repeat():
    nodes = []
    for run_id, exit_code in [("first", 1), ("second", 0)]:
        nodes.extend([
            {"id": run_id, "value": {"role": "assistant", "run_id": run_id,
             "tool_calls": [{"id": "same-id", "name": "Bash", "arguments": {"command": f"exit {exit_code}"}}]}},
            {"id": f"{run_id}-result", "value": {"role": "tool_results", "run_id": run_id,
             "results": [{"call_id": "same-id", "success": True, "value": {"exit_code": exit_code}}]}},
            {"id": f"{run_id}-end", "value": {"role": "assistant", "run_id": run_id, "answer_complete": True}},
        ])

    first, second = project_tool_activity_messages({"nodes": nodes})

    assert first["trace"][0]["exitCode"] == 1
    assert second["trace"][0]["exitCode"] == 0


def test_history_keeps_repeated_bash_calls_in_one_run():
    nodes = []
    args = {"command": "printf 'repeat'"}
    for assistant_id, exit_code in [("first", 1), ("second", 0)]:
        nodes.extend([
            {"id": assistant_id, "value": {"role": "assistant", "run_id": "run",
             "tool_calls": [{"id": "same-id", "name": "Bash", "arguments": args}]}},
            {"id": f"{assistant_id}-result", "parent_id": assistant_id,
             "value": {"role": "tool_results", "run_id": "run",
             "results": [{"call_id": "same-id", "success": True, "value": {"exit_code": exit_code}}]}},
        ])
    first, second = project_tool_activity_messages({"nodes": nodes}, "run")
    timeline = RunTimeline("run")
    event = AgentSessionEvent(
        sequence=1, type="assistant.tool_calls", tree_id="tree", run_id="run", node_id="first",
        time=datetime(2030, 1, 1, tzinfo=timezone.utc),
        data={**nodes[0]["value"], "stream_source_id": "stream-1"},
    )
    for projected in workbench_events(event):
        timeline.apply(projected)
    saved = timeline.messages()[0]
    saved["trace"][0].pop("input")

    result = _merge_context_activity_messages({"messages": [saved]}, [first, second])

    assert [message["id"] for message in result["messages"]] == [saved["id"], second["id"]]
    assert [message["trace"][0]["exitCode"] for message in result["messages"]] == [1, 0]


def test_history_fork_and_retry_only_recover_commands_from_the_visible_branch(tmp_path):
    db_path = str(tmp_path / "chat.db")
    context_path = workbench_agent_data_directory(db_path) / "context"

    def add_turn(router, parent, run_id, command, *, retry=False):
        user = router.mount("source", parent, {"role": "user", "run_id": run_id, "metadata": {"retry": retry}})
        call = router.mount("source", user.id, {
            "role": "assistant", "run_id": run_id,
            "tool_calls": [{"id": "reused", "name": "Bash", "arguments": {"command": command}}],
        })
        result = router.mount("source", call.id, {
            "role": "tool_results", "run_id": run_id,
            "results": [{"call_id": "reused", "success": True, "value": {"exit_code": 0}}],
        })
        return router.mount("source", result.id, {"role": "assistant", "run_id": run_id, "answer_complete": True})

    def commands(chat_id):
        return [
            item["trace"][0]["command"]
            for item in AgentContextRepository(context_path).read(chat_id)["activityMessages"]
        ]

    with ContextStoreRouter(context_path) as router:
        tree = router.create_tree({"role": "system", "content": "instructions"}, tree_id="source")
        prefix = add_turn(router, tree.root_id, "prefix", "echo prefix")
        original = add_turn(router, prefix.id, "original", "echo original")
        router.commit_state(tree.id, original.id, "original")

    ConversationRuntime(db_path).fork_context("source", "fork", user_ordinal=2)
    assert commands("fork") == ["echo prefix"]
    assert commands("source") == ["echo prefix", "echo original"]

    with ContextStoreRouter(context_path) as router:
        retry = add_turn(router, prefix.id, "retry", "echo retry", retry=True)
        router.commit_state("source", retry.id, "retry")
    assert commands("source") == ["echo prefix", "echo retry"]
    assert commands("fork") == ["echo prefix"]


def test_distinct_permission_only_activities_are_not_discarded():
    canonical, reconstructed = dual_projection(count=1)
    pair = deepcopy([canonical[0], reconstructed[0]])
    for message in pair:
        message["trace"] = [entry for entry in message["trace"] if entry["kind"] == "permission"]
    other = deepcopy(pair[0])
    other["id"] = "another-review"
    other["trace"][0]["toolCallId"] = "permission:permission_review_other"
    assert deduplicate_projected_messages([*pair, other]) == [pair[0], other]


def test_chat_detail_endpoint_reconciles_after_context_reconstruction():
    canonical, reconstructed = dual_projection()
    chat = {"id": "chat", "generatedFiles": [], "messages": canonical,
            "messageCount": len(canonical)}
    repository = SimpleNamespace(
        get=lambda _id: deepcopy(chat), read=lambda: {}, read_summaries=lambda: {},
        write=Mock(side_effect=AssertionError("History loading must not write")),
    )
    service = SimpleNamespace(
        repository=repository, prune_orphaned_fork_metadata=lambda _: False,
        public_chat_full=deepcopy, prewarm_workspace_changes=Mock(),
        resolve_chat_workspace_dir=Mock(), sync_chat_generated_files=Mock(),
    )
    context = SimpleNamespace(
        service=service,
        runtime=lambda: SimpleNamespace(read_store=lambda: {}, find_project=lambda *_: None),
        conversation_context=SimpleNamespace(activity_messages=AsyncMock(return_value=reconstructed)),
    )
    router = APIRouter()
    _register_get_route(router, context)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/api/workbench/chats/chat")
            assert response.status_code == 200
            assert response.json()["chat"]["messages"] == canonical
            assert response.json()["chat"]["messageCount"] == len(canonical)
    repository.write.assert_not_called()


@pytest.mark.parametrize("wrapped", [False, True])
def test_card_reference_survives_context_history_reconstruction(wrapped):
    presentation = {"kind": "assistant_card", "card_id": "card_" + "a" * 32, "revision": 1, "operation": "card.create"}
    output = {"ok": True, "presentation": presentation}
    if wrapped:
        output = {"result": output}
    nodes = [
        {"id": "create-card", "value": {"role": "assistant", "run_id": "r", "tool_calls": [{"id": "c", "name": "card.create", "arguments": {}}]}},
        {"id": "result", "value": {"role": "tool_results", "run_id": "r", "results": [{"call_id": "c", "success": True, "value": output}]}}
    ]
    result = project_tool_activity_messages({"nodes": nodes}, "r")
    assert result[0]["trace"][0]["presentation"] == presentation
    timeline = RunTimeline("r")
    timeline.apply({"type": "tool.completed", "toolCallId": "c", "name": "card.create", "status": "completed", "presentation": presentation})
    assert timeline.messages()[0]["trace"][0]["presentation"] == presentation
