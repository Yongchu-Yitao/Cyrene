"""History must reconcile the live timeline with ContextTree reconstruction."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.workbench.chat.chat_application import deduplicate_projected_messages
from cyrene.workbench.chat.run_timeline import RunTimeline
from cyrene.workbench.core_adapter.tool_activity_projection import project_tool_activity_messages
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
