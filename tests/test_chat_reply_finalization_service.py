from __future__ import annotations

import threading

from cyrene.workbench.chat.chat_external_turn_service import ExternalTurnProjection
from cyrene.workbench.chat.chat_reply_finalization_service import (
    ChatReplyFinalizationApplicationService,
    ChatReplyFinalizationDependencies,
    ChatReplyFinalizationRequest,
)
from cyrene.workbench.chat.chat_application import (
    deduplicate_projected_messages,
    pending_question_message,
)


def test_builtin_agent_metrics_are_persisted_on_assistant_message():
    service = ChatReplyFinalizationApplicationService(
        ChatReplyFinalizationDependencies(
            lock=threading.Lock(),
            get_chat=lambda _chat_id: None,
            write_chat=lambda *_args, **_kwargs: None,
            state_messages=lambda _chat_id: [],
            extract_timeline=lambda *_args, **_kwargs: ([], {}, []),
            last_model=lambda *_args, **_kwargs: "",
            short_id=lambda prefix: f"{prefix}-1",
            utc_now_iso=lambda: "2030-01-01T00:00:00+00:00",
            merge_messages=lambda *_args, **_kwargs: None,
            next_turn_count=lambda *_args, **_kwargs: 1,
            public_chat_light=lambda _chat: {},
        )
    )
    projection = ExternalTurnProjection(
        usage={
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        },
        latest_request_usage={
            "prompt_tokens": 118,
            "completion_tokens": 30,
            "total_tokens": 148,
            "prompt_cache_hit_tokens": 110,
            "prompt_cache_miss_tokens": 8,
        },
        model="provider/model",
        model_identity={"provider": "provider", "model": "model"},
        generation_duration_ms=750.0,
        output_tokens_per_second=40.0,
    )
    request = ChatReplyFinalizationRequest(
        chat_id="chat-1",
        project_id="project-1",
        workspace_dir="/tmp/workspace",
        message="hello",
        command="",
        retry=False,
        is_side_agent=False,
        is_external_agent=False,
        completed_turn_count_before=0,
        processing_started_at=0.0,
        state_ids_before=set(),
        projection=projection,
        commit_retry_cut=lambda _chat: None,
    )

    assistant = service._assistant_message(
        request,
        "done",
        projection.model,
        {},
        [],
    )

    assert assistant["usage"] == projection.usage
    assert assistant["latestRequestUsage"] == projection.latest_request_usage
    assert assistant["model"] == "provider/model"
    assert assistant["modelIdentity"] == projection.model_identity
    assert assistant["modelGenerationDurationMs"] == 750.0
    assert assistant["outputTokensPerSecond"] == 40.0


def test_pending_question_persists_the_same_latest_request_usage():
    latest_usage = {
        "prompt_tokens": 7986,
        "completion_tokens": 89,
        "total_tokens": 8075,
        "prompt_cache_hit_tokens": 7748,
        "prompt_cache_miss_tokens": 238,
    }

    message = pending_question_message(
        {"id": "question-1", "text": "Continue?"},
        usage={"prompt_tokens": 15734, "total_tokens": 16006},
        latest_request_usage=latest_usage,
    )

    assert message["latestRequestUsage"] == latest_usage


def test_builtin_context_tree_activity_is_included_in_saved_timeline():
    service = ChatReplyFinalizationApplicationService(
        ChatReplyFinalizationDependencies(
            lock=threading.Lock(),
            get_chat=lambda _chat_id: None,
            write_chat=lambda *_args, **_kwargs: None,
            state_messages=lambda _chat_id: [],
            extract_timeline=lambda *_args, **_kwargs: ([], {}, []),
            last_model=lambda *_args, **_kwargs: "",
            short_id=lambda prefix: f"{prefix}-generated",
            utc_now_iso=lambda: "2030-01-01T00:00:00+00:00",
            merge_messages=lambda *_args, **_kwargs: None,
            next_turn_count=lambda *_args, **_kwargs: 1,
            public_chat_light=lambda _chat: {},
        )
    )
    projection = ExternalTurnProjection(
        activity_messages=[
            {
                "id": "activity-assistant-1",
                "role": "assistant",
                "content": "",
                "createdAt": "2030-01-01T00:00:01+00:00",
                "activityCard": True,
                "trace": [
                    {
                        "kind": "tool",
                        "toolCallId": "call-1",
                        "text": "save_project_memory",
                        "status": "completed",
                    }
                ],
            }
        ]
    )
    timeline = []

    service._prepend_external_projection(
        projection,
        timeline,
        {
            "id": "reply-1",
            "role": "assistant",
            "createdAt": "2030-01-01T00:00:02+00:00",
        },
        "provider/model",
    )

    assert [message["id"] for message in timeline] == ["activity-assistant-1"]
    assert timeline[0]["trace"][0]["text"] == "save_project_memory"
    assert timeline[0]["model"] == "provider/model"


def test_external_projection_does_not_duplicate_a_canonical_tool_activity():
    service = ChatReplyFinalizationApplicationService(
        ChatReplyFinalizationDependencies(
            lock=threading.Lock(),
            get_chat=lambda _chat_id: None,
            write_chat=lambda *_args, **_kwargs: None,
            state_messages=lambda _chat_id: [],
            extract_timeline=lambda *_args, **_kwargs: ([], {}, []),
            last_model=lambda *_args, **_kwargs: "",
            short_id=lambda prefix: f"{prefix}-generated",
            utc_now_iso=lambda: "2030-01-01T00:00:00+00:00",
            merge_messages=lambda *_args, **_kwargs: None,
            next_turn_count=lambda *_args, **_kwargs: 1,
            public_chat_light=lambda _chat: {},
        )
    )
    projection = ExternalTurnProjection(activity_messages=[{
        "id": "activity_assistant_legacy",
        "role": "assistant",
        "content": "",
        "createdAt": "2030-01-01T00:00:01+00:00",
        "activityCard": True,
        "trace": [
            {"kind": "tool", "toolCallId": "call-1", "status": "completed"}
        ],
    }])
    timeline = [{
        "id": "run_1:activity:2",
        "runId": "run_1",
        "timelineVersion": 1,
        "timelineOrder": 2,
        "role": "assistant",
        "content": "",
        "createdAt": "2030-01-01T00:00:01+00:00",
        "activityCard": True,
        "trace": [
            {"kind": "tool", "toolCallId": "call-1", "status": "completed"}
        ],
    }]

    service._prepend_external_projection(
        projection,
        timeline,
        {
            "id": "reply-1",
            "role": "assistant",
            "createdAt": "2030-01-01T00:00:02+00:00",
        },
        "provider/model",
    )

    assert [message["id"] for message in timeline] == ["run_1:activity:2"]


def test_historical_dual_projection_keeps_canonical_identity_and_legacy_metrics():
    messages = [
        {
            "id": "run_1:activity:2",
            "runId": "run_1",
            "timelineVersion": 1,
            "timelineOrder": 2,
            "role": "assistant",
            "content": "",
            "createdAt": "2030-01-01T00:00:01+00:00",
            "activityCard": True,
            "trace": [{"kind": "tool", "toolCallId": "call-1"}],
        },
        {
            "id": "activity_assistant_legacy",
            "role": "assistant",
            "content": "",
            "createdAt": "2030-01-01T00:00:02+00:00",
            "activityCard": True,
            "trace": [{"kind": "tool", "toolCallId": "call-1"}],
        },
        {
            "id": "run_1:message:3",
            "runId": "run_1",
            "timelineVersion": 1,
            "timelineOrder": 3,
            "role": "assistant",
            "content": "done",
            "createdAt": "2030-01-01T00:00:03+00:00",
        },
        {
            "id": "msg_legacy",
            "runId": "run_resume",
            "role": "assistant",
            "content": "done",
            "createdAt": "2030-01-01T00:00:04+00:00",
            "usage": {"total_tokens": 42},
        },
    ]

    projected = deduplicate_projected_messages(messages)

    assert [message["id"] for message in projected] == [
        "run_1:activity:2",
        "run_1:message:3",
    ]
    assert projected[1]["usage"] == {"total_tokens": 42}
